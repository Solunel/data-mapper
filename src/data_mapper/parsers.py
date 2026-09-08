"""CSV/XLSX 的不可变本地接入与表格解析。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import load_workbook

from .contracts import ParsedRecord, ParsedTable, PipelineConfig, RawDataset, resolved_path
from .errors import InputParseError, UnsupportedFormatError


_SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


def ingest_raw(path: str | Path, config: PipelineConfig) -> RawDataset:
    source = resolved_path(path)
    if not source.is_file():
        raise InputParseError(f"输入不是普通文件：{source}")
    extension = source.suffix.lower()
    if extension not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"不支持的输入格式“{extension or '<无扩展名>'}”；Phase 1 仅支持 .xlsx 和 .csv"
        )

    payload = source.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    dataset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source.as_uri()))
    config_json = json.dumps(config.to_dict(), sort_keys=True, ensure_ascii=False)
    version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{dataset_id}:{checksum}:{config_json}"))
    return RawDataset(
        dataset_id=dataset_id,
        version_id=version_id,
        source_file=source.name,
        extension=extension,
        size_bytes=len(payload),
        sha256=checksum,
        content_ref=str(source),
        ingested_at=datetime.now(timezone.utc),
        parser_config=config.to_dict(),
        parser_version=config.parser_version,
        status="ready",
    )


def parse_raw(raw: RawDataset, config: PipelineConfig) -> tuple[ParsedTable, ...]:
    payload = Path(raw.content_ref).read_bytes()
    actual_checksum = hashlib.sha256(payload).hexdigest()
    if actual_checksum != raw.sha256:
        raise InputParseError(
            "Raw 文件在接入后发生变化，内容校验值不再匹配",
            raw_dataset=replace(raw, status="failed", error="checksum_mismatch"),
        )

    try:
        if raw.extension == ".csv":
            return (_parse_csv(payload, raw.source_file, config),)
        if raw.extension == ".xlsx":
            return _parse_xlsx(payload, raw.source_file, config)
    except InputParseError:
        raise
    except Exception as exc:
        message = f"无法解析 {raw.source_file}：{exc}"
        raise InputParseError(
            message,
            raw_dataset=replace(raw, status="failed", error=message),
        ) from exc
    raise UnsupportedFormatError(f"不支持的输入格式“{raw.extension}”")


def _decode_csv(payload: bytes, encoding: str | None) -> tuple[str, str]:
    candidates = (encoding,) if encoding else ("utf-8-sig", "gb18030")
    failures: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return payload.decode(candidate, errors="strict"), candidate
        except (LookupError, UnicodeDecodeError) as exc:
            failures.append(f"{candidate}: {exc}")
    raise InputParseError("CSV 无法使用允许的编码解码：" + "; ".join(failures))


def _parse_csv(payload: bytes, source_file: str, config: PipelineConfig) -> ParsedTable:
    text, encoding = _decode_csv(payload, config.csv_encoding)
    delimiter = config.csv_delimiter
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = [tuple(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    warning = ()
    if encoding == "gb18030" and config.csv_encoding is None:
        warning = (f"CSV 使用受限回退编码 {encoding} 解码",)
    return _table_from_rows(source_file, "CSV", rows, config, warning)


def _parse_xlsx(payload: bytes, source_file: str, config: PipelineConfig) -> tuple[ParsedTable, ...]:
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:
        raise InputParseError(f"XLSX 文件无效或不受支持：{exc}") from exc

    tables: list[ParsedTable] = []
    try:
        for worksheet in workbook.worksheets:
            rows = [tuple(row) for row in worksheet.iter_rows(values_only=True)]
            if not any(not _row_is_empty(row) for row in rows):
                continue
            tables.append(_table_from_rows(source_file, worksheet.title, rows, config, ()))
    finally:
        workbook.close()
    if not tables:
        raise InputParseError("XLSX 不包含任何非空工作表")
    return tuple(tables)


def _table_from_rows(
    source_file: str,
    sheet_name: str,
    rows: Sequence[Sequence[Any]],
    config: PipelineConfig,
    warning_messages: Iterable[str],
) -> ParsedTable:
    last_column = max(
        (index + 1 for row in rows for index, value in enumerate(row) if not _is_empty(value)),
        default=0,
    )
    if last_column == 0:
        raise InputParseError(f"{source_file} [{sheet_name}] 为空")
    bounded_rows = [tuple(row[:last_column]) for row in rows]
    header_row = _find_header_row(bounded_rows, config.header_rows.get(sheet_name))
    header_values = list(bounded_rows[header_row - 1])
    header_values.extend([None] * (last_column - len(header_values)))
    headers = tuple("" if value is None else str(value).strip() for value in header_values)
    if not any(headers):
        raise InputParseError(f"{source_file} [{sheet_name}] 没有可用表头")

    records: list[ParsedRecord] = []
    for source_row, row in enumerate(bounded_rows[header_row:], start=header_row + 1):
        values = list(row)
        values.extend([None] * (last_column - len(values)))
        records.append(ParsedRecord(source_row=source_row, values=tuple(values[:last_column])))

    from .contracts import Issue

    warnings = tuple(
        Issue(
            severity="warning",
            code="parse_warning",
            message=message,
            step="Parse",
            source_file=source_file,
            sheet_name=sheet_name,
        )
        for message in warning_messages
    )
    return ParsedTable(
        source_file=source_file,
        sheet_name=sheet_name,
        header_row=header_row,
        original_headers=headers,
        records=tuple(records),
        warnings=warnings,
    )


def _find_header_row(rows: Sequence[Sequence[Any]], explicit: int | None) -> int:
    if explicit is not None:
        if explicit < 1 or explicit > len(rows):
            raise InputParseError(f"配置的表头行 {explicit} 超出输入范围")
        if _row_is_empty(rows[explicit - 1]):
            raise InputParseError(f"配置的表头行 {explicit} 为空")
        return explicit

    non_empty_indexes = [index for index, row in enumerate(rows[:20]) if not _row_is_empty(row)]
    for index in non_empty_indexes:
        cells = [str(value).strip() for value in rows[index] if not _is_empty(value)]
        if len(cells) < 2 or len(set(cells)) != len(cells):
            continue
        later = next((rows[i] for i in non_empty_indexes if i > index), None)
        if later is None:
            return index + 1
        required = max(2, (len(cells) + 1) // 2)
        if sum(not _is_empty(value) for value in later) >= required:
            return index + 1
    raise InputParseError(
        "无法在前 20 行中可靠定位表头，请显式配置 header_rows"
    )


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _row_is_empty(row: Sequence[Any]) -> bool:
    return all(_is_empty(value) for value in row)
