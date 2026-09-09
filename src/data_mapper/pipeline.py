"""面向表格输入的固定、可重放 Phase 1 Pipeline。"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    SUPPORTED_TYPES,
    ColumnQuality,
    ColumnSchema,
    CuratedDataset,
    CuratedRow,
    DataSchema,
    HeaderMapping,
    Issue,
    Phase1Result,
    PipelineConfig,
    PipelineStepReport,
    QualityReport,
)
from .errors import InputParseError
from .parsers import ingest_raw, parse_raw


PIPELINE_ORDER = (
    "Parse",
    "Normalize Headers",
    "Infer Data Schema",
    "Convert Types",
    "Basic Clean",
    "Build Curated Dataset",
    "Quality Check",
)


@dataclass(frozen=True)
class _WorkingRow:
    source_row: int
    values: Mapping[str, Any]


def curate_file(path: str | Path, config: PipelineConfig | None = None) -> Phase1Result:
    """让一个本地 .xlsx/.csv 文件完整通过 Phase 1 处理链。"""

    config = config or PipelineConfig()
    _validate_config(config)
    raw = ingest_raw(path, config)
    try:
        parsed_tables = parse_raw(raw, config)
    except InputParseError as exc:
        if exc.raw_dataset is None:
            exc.raw_dataset = replace(raw, status="failed", error=str(exc))
        raise

    successful_raw = replace(raw, status="parsed")
    curated = tuple(_curate_table(successful_raw, table, config) for table in parsed_tables)
    return Phase1Result(raw_dataset=successful_raw, curated_datasets=curated)


def _validate_config(config: PipelineConfig) -> None:
    if config.inference_sample_size < 1:
        raise ValueError("inference_sample_size 必须至少为 1")
    if config.conversion_error_policy != "null":
        raise ValueError("Phase 1 仅支持 conversion_error_policy='null'")
    for unit, overrides in config.type_overrides.items():
        for column, data_type in overrides.items():
            if data_type not in SUPPORTED_TYPES:
                raise ValueError(f"不支持的类型覆盖：{unit}.{column}={data_type}")


def _curate_table(raw, table, config: PipelineConfig) -> CuratedDataset:
    row_count = len(table.records)
    reports: list[PipelineStepReport] = [
        PipelineStepReport(
            name="Parse",
            rows_in=row_count,
            rows_out=row_count,
            config={
                "parser_version": config.parser_version,
                "header_row": table.header_row,
                "header_rows": list(table.header_rows),
            },
            warnings=tuple(issue.message for issue in table.warnings),
            errors=tuple(issue.message for issue in table.errors),
        )
    ]
    issues: list[Issue] = [*table.warnings, *table.errors]

    header_mapping, normalized_rows, header_issues = _normalize_table(table)
    issues.extend(header_issues)
    reports.append(
        PipelineStepReport(
            name="Normalize Headers",
            rows_in=row_count,
            rows_out=len(normalized_rows),
            config={"algorithm": "preserve-trim-unique-v2"},
            warnings=tuple(issue.message for issue in header_issues),
        )
    )

    schema, inference_issues = _infer_schema(
        table.source_file,
        table.sheet_name,
        table.header_row,
        table.header_rows,
        header_mapping,
        normalized_rows,
        config,
    )
    issues.extend(inference_issues)
    reports.append(
        PipelineStepReport(
            name="Infer Data Schema",
            rows_in=len(normalized_rows),
            rows_out=len(normalized_rows),
            config={
                "sample_size": config.inference_sample_size,
                "type_overrides": dict(config.type_overrides.get(table.sheet_name, {})),
            },
            warnings=tuple(issue.message for issue in inference_issues),
        )
    )

    converted_rows, conversion_issues, schema = _convert_rows(
        table.source_file, table.sheet_name, normalized_rows, schema, config
    )
    issues.extend(conversion_issues)
    reports.append(
        PipelineStepReport(
            name="Convert Types",
            rows_in=len(normalized_rows),
            rows_out=len(converted_rows),
            config={"on_error": config.conversion_error_policy},
            errors=tuple(issue.message for issue in conversion_issues),
        )
    )

    cleaned_rows, removed_blank_rows = _basic_clean(converted_rows)
    reports.append(
        PipelineStepReport(
            name="Basic Clean",
            rows_in=len(converted_rows),
            rows_out=len(cleaned_rows),
            config={
                "trim_strings": True,
                "remove_entirely_blank_rows": True,
                "null_placeholders": ["-", "–", "—", "―", "－"],
                "deduplicate": False,
                "fill_nulls": False,
            },
            warnings=(
                (f"已移除 {removed_blank_rows} 个完全空白数据行",)
                if removed_blank_rows
                else ()
            ),
        )
    )

    curated_rows = tuple(
        CuratedRow(source_row=row.source_row, values=dict(row.values)) for row in cleaned_rows
    )
    reports.append(
        PipelineStepReport(
            name="Build Curated Dataset",
            rows_in=len(cleaned_rows),
            rows_out=len(curated_rows),
            config={"model": "CuratedDataset-v1"},
        )
    )

    quality = _quality_report(
        table.source_file,
        table.sheet_name,
        len(normalized_rows),
        curated_rows,
        schema,
        removed_blank_rows,
        issues,
    )
    reports.append(
        PipelineStepReport(
            name="Quality Check",
            rows_in=len(curated_rows),
            rows_out=len(curated_rows),
            config={"duplicate_policy": "report_only"},
            warnings=tuple(
                issue.message for issue in quality.issues if issue.severity == "warning"
            ),
            errors=tuple(issue.message for issue in quality.issues if issue.severity == "error"),
        )
    )

    config_json = json.dumps(config.to_dict(), sort_keys=True, ensure_ascii=False)
    curated_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{raw.version_id}:{table.sheet_name}:{table.header_rows}:{config_json}",
        )
    )
    return CuratedDataset(
        curated_id=curated_id,
        generated_at=datetime.now(timezone.utc),
        raw_dataset_id=raw.dataset_id,
        raw_version_id=raw.version_id,
        source_file=table.source_file,
        sheet_name=table.sheet_name,
        header_row=table.header_row,
        header_rows=table.header_rows,
        context_cells=table.context_cells,
        header_mapping=header_mapping,
        data_schema=schema,
        rows=curated_rows,
        pipeline_steps=tuple(reports),
        quality_report=quality,
        issues=quality.issues,
    )


def _normalize_table(table) -> tuple[tuple[HeaderMapping, ...], tuple[_WorkingRow, ...], tuple[Issue, ...]]:
    used_names: set[str] = set()
    mapping: list[HeaderMapping] = []
    issues: list[Issue] = []
    for position, (source_position, original) in enumerate(
        zip(table.source_column_positions, table.original_headers, strict=True),
        start=1,
    ):
        base = _header_key_base(original) or f"column_{position}"
        normalized = base
        suffix = 2
        while normalized in used_names:
            normalized = f"{base}_{suffix}"
            suffix += 1
        used_names.add(normalized)
        item = HeaderMapping(
            position=position,
            source_position=source_position,
            original_name=original,
            normalized_name=normalized,
            base_normalized_name=base,
            was_empty=not original.strip(),
            had_collision=normalized != base,
        )
        mapping.append(item)
        if item.was_empty:
            issues.append(
                Issue(
                    severity="warning",
                    code="empty_header",
                    message=f"第 {position} 列表头为空，已命名为“{normalized}”",
                    step="Normalize Headers",
                    source_file=table.source_file,
                    sheet_name=table.sheet_name,
                    source_row=table.header_row,
                )
            )
        if item.had_collision:
            issues.append(
                Issue(
                    severity="warning",
                    code="duplicate_normalized_header",
                    message=f"表头“{original}”与已有内部键重复，已命名为“{normalized}”",
                    step="Normalize Headers",
                    source_file=table.source_file,
                    sheet_name=table.sheet_name,
                    source_row=table.header_row,
                    source_column=original,
                )
            )

    empty_count = sum(item.was_empty for item in mapping)
    if mapping and empty_count >= max(2, math.ceil(len(mapping) / 2)):
        issues.append(
            Issue(
                severity="error",
                code="excessive_empty_headers",
                message=(
                    f"{empty_count}/{len(mapping)} 个表头为空，当前表头区域不可靠；"
                    "请检查自动识别结果或显式配置 header_rows"
                ),
                step="Normalize Headers",
                source_file=table.source_file,
                sheet_name=table.sheet_name,
                source_row=table.header_row,
            )
        )

    rows = tuple(
        _WorkingRow(
            source_row=record.source_row,
            values={item.normalized_name: record.values[index] for index, item in enumerate(mapping)},
        )
        for record in table.records
    )
    return tuple(mapping), rows, tuple(issues)


def _header_key_base(value: str) -> str:
    """正常表头原样作为 Curated 键，仅移除首尾空白。"""

    return value.strip()


def _infer_schema(
    source_file: str,
    sheet_name: str,
    header_row: int,
    header_rows: tuple[int, ...],
    header_mapping: tuple[HeaderMapping, ...],
    rows: tuple[_WorkingRow, ...],
    config: PipelineConfig,
) -> tuple[DataSchema, tuple[Issue, ...]]:
    columns: list[ColumnSchema] = []
    issues: list[Issue] = []
    overrides = config.type_overrides.get(sheet_name, {})
    for item in header_mapping:
        values = [row.values.get(item.normalized_name) for row in rows]
        non_null = [value for value in values if not _is_null(value)]
        sampled = _even_sample(non_null, config.inference_sample_size)
        evidence: dict[str, int] = {}
        for value in sampled:
            inferred = _infer_value_type(value)
            evidence[inferred] = evidence.get(inferred, 0) + 1

        inferred_type = _resolve_type(evidence)
        method = "deterministic-sample"
        override = overrides.get(item.normalized_name, overrides.get(item.original_name))
        if override is not None:
            inferred_type = override
            method = "explicit-override"
        elif len(evidence) > 1 and inferred_type == "string":
            issues.append(
                Issue(
                    severity="warning",
                    code="mixed_type_inference",
                    message=f"列“{item.normalized_name}”存在混合类型证据，已保守推断为 string",
                    step="Infer Data Schema",
                    source_file=source_file,
                    sheet_name=sheet_name,
                    source_column=item.original_name,
                )
            )

        confidence = 1.0
        if sampled and method != "explicit-override":
            confidence = max(evidence.values()) / len(sampled)
        columns.append(
            ColumnSchema(
                position=item.position,
                original_name=item.original_name,
                normalized_name=item.normalized_name,
                data_type=inferred_type,
                nullable=len(non_null) != len(values),
                sample_values=tuple(sampled[:5]),
                inference_evidence=dict(sorted(evidence.items())),
                confidence=confidence,
                inference_method=method,
            )
        )
    return (
        DataSchema(
            columns=tuple(columns),
            row_count=len(rows),
            header_row=header_row,
            header_rows=header_rows,
        ),
        tuple(issues),
    )


def _even_sample(values: list[Any], limit: int) -> list[Any]:
    if len(values) <= limit:
        return values
    if limit == 1:
        return [values[len(values) // 2]]
    indexes = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
    return [values[index] for index in indexes]


def _infer_value_type(value: Any) -> str:
    if _is_null(value):
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        return "date" if value.time() == time.min else "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if math.isfinite(value) and value.is_integer() else "number"

    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"true", "false", "yes", "no", "y", "n"}:
        return "boolean"
    numeric = text.replace(",", "")
    try:
        int(numeric)
        return "integer"
    except ValueError:
        pass
    try:
        number = float(numeric)
        if math.isfinite(number):
            return "number"
    except ValueError:
        pass
    if _parse_date(text) is not None:
        return "date"
    if _parse_datetime(text) is not None:
        return "datetime"
    return "string"


def _resolve_type(evidence: Mapping[str, int]) -> str:
    kinds = set(evidence)
    if not kinds:
        return "null"
    if len(kinds) == 1:
        return next(iter(kinds))
    if kinds <= {"integer", "number"}:
        return "number"
    if kinds <= {"date", "datetime"}:
        return "datetime"
    return "string"


def _convert_rows(
    source_file: str,
    sheet_name: str,
    rows: tuple[_WorkingRow, ...],
    schema: DataSchema,
    config: PipelineConfig,
) -> tuple[tuple[_WorkingRow, ...], tuple[Issue, ...], DataSchema]:
    issues: list[Issue] = []
    converted: list[_WorkingRow] = []
    source_names = {column.normalized_name: column.original_name for column in schema.columns}
    for row in rows:
        values: dict[str, Any] = {}
        for column in schema.columns:
            value = row.values.get(column.normalized_name)
            try:
                values[column.normalized_name] = _convert_value(value, column.data_type)
            except (TypeError, ValueError):
                values[column.normalized_name] = None
                issues.append(
                    Issue(
                        severity="error",
                        code="conversion_failed",
                        message=(
                            f"列“{column.normalized_name}”的值无法转换为 {column.data_type}；"
                            f"已应用“{config.conversion_error_policy}”策略"
                        ),
                        step="Convert Types",
                        source_file=source_file,
                        sheet_name=sheet_name,
                        source_row=row.source_row,
                        source_column=source_names[column.normalized_name],
                        value=value,
                    )
                )
        converted.append(_WorkingRow(source_row=row.source_row, values=values))

    updated_columns = tuple(
        replace(
            column,
            nullable=column.nullable
            or any(row.values.get(column.normalized_name) is None for row in converted),
        )
        for column in schema.columns
    )
    return tuple(converted), tuple(issues), replace(schema, columns=updated_columns)


def _convert_value(value: Any, target: str) -> Any:
    if _is_null(value):
        return None
    if target == "string":
        return str(value)
    if target == "integer":
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return int(str(value).strip().replace(",", ""))
    if target == "number":
        if isinstance(value, bool):
            raise ValueError
        number = float(value) if isinstance(value, (int, float)) else float(str(value).strip().replace(",", ""))
        if not math.isfinite(number):
            raise ValueError
        return number
    if target == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "yes", "y"}:
            return True
        if lowered in {"false", "no", "n"}:
            return False
        raise ValueError
    if target == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        parsed = _parse_date(str(value).strip())
        if parsed is None:
            raise ValueError
        return parsed
    if target == "datetime":
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        parsed = _parse_datetime(str(value).strip())
        if parsed is None:
            parsed_date = _parse_date(str(value).strip())
            if parsed_date is None:
                raise ValueError
            return datetime.combine(parsed_date, time.min)
        return parsed
    if target == "null":
        raise ValueError
    raise ValueError(f"不支持的目标类型：{target}")


def _parse_date(value: str) -> date | None:
    candidate = value.replace("/", "-")
    if re.fullmatch(r"\d{8}", candidate):
        candidate = f"{candidate[:4]}-{candidate[4:6]}-{candidate[6:]}"
    if not re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", candidate):
        return None
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    if "T" not in value and not re.search(r"\d{1,2}:\d{2}", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _basic_clean(rows: tuple[_WorkingRow, ...]) -> tuple[tuple[_WorkingRow, ...], int]:
    cleaned: list[_WorkingRow] = []
    removed = 0
    for row in rows:
        values = {
            name: value.strip() if isinstance(value, str) else value
            for name, value in row.values.items()
        }
        if all(_is_null(value) for value in values.values()):
            removed += 1
            continue
        cleaned.append(_WorkingRow(source_row=row.source_row, values=values))
    return tuple(cleaned), removed


def _quality_report(
    source_file: str,
    sheet_name: str,
    input_rows: int,
    rows: tuple[CuratedRow, ...],
    schema: DataSchema,
    removed_blank_rows: int,
    existing_issues: Iterable[Issue],
) -> QualityReport:
    issues = list(existing_issues)
    if not rows:
        issues.append(
            Issue(
                severity="error",
                code="empty_table",
                message="Curated 表不包含数据行",
                step="Quality Check",
                source_file=source_file,
                sheet_name=sheet_name,
            )
        )

    column_quality: list[ColumnQuality] = []
    for column in schema.columns:
        null_count = sum(row.values.get(column.normalized_name) is None for row in rows)
        column_quality.append(
            ColumnQuality(
                name=column.normalized_name,
                null_count=null_count,
                null_rate=(null_count / len(rows)) if rows else 0.0,
            )
        )
        if rows and null_count == len(rows):
            issues.append(
                Issue(
                    severity="warning",
                    code="all_null_column",
                    message=(
                        f"列“{column.normalized_name}”全部为空；"
                        "已保留列结构，但无法从当前数据推断非空类型"
                    ),
                    step="Quality Check",
                    source_file=source_file,
                    sheet_name=sheet_name,
                    source_column=column.original_name,
                )
            )

    seen: set[str] = set()
    duplicate_count = 0
    for row in rows:
        key = json.dumps(_stable_json(row.values), sort_keys=True, ensure_ascii=False)
        if key in seen:
            duplicate_count += 1
        seen.add(key)
    if duplicate_count:
        issues.append(
            Issue(
                severity="warning",
                code="duplicate_rows",
                message=f"发现 {duplicate_count} 个完全重复行；重复行已保留",
                step="Quality Check",
                source_file=source_file,
                sheet_name=sheet_name,
            )
        )

    conversion_errors = tuple(issue for issue in issues if issue.code == "conversion_failed")
    passed = bool(rows) and not any(issue.severity == "error" for issue in issues)
    return QualityReport(
        passed=passed,
        input_row_count=input_rows,
        curated_row_count=len(rows),
        row_count_difference=input_rows - len(rows),
        row_count_reasons={"entirely_blank_rows_removed": removed_blank_rows},
        column_count=len(schema.columns),
        columns=tuple(column_quality),
        duplicate_row_count=duplicate_count,
        conversion_error_count=len(conversion_errors),
        conversion_error_examples=conversion_errors[:5],
        issues=tuple(issues),
    )


def _stable_json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _stable_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_stable_json(item) for item in value]
    return value


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip() in {"", "-", "–", "—", "―", "－"}
