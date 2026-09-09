"""精简且可序列化为 JSON 的 Phase 1 数据契约。

这些契约只描述表格数据，刻意不依赖项目的 Definition/Knowledge 本体资产。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SUPPORTED_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "date", "datetime", "null"}
)


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class PipelineConfig:
    """会影响 Phase 1 重放结果的全部配置。"""

    header_rows: Mapping[str, int | Sequence[int]] = field(default_factory=dict)
    csv_encoding: str | None = None
    csv_delimiter: str | None = None
    type_overrides: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    inference_sample_size: int = 100
    conversion_error_policy: str = "null"
    parser_version: str = "phase1-v2"

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class RawDataset:
    dataset_id: str
    version_id: str
    source_file: str
    extension: str
    size_bytes: int
    sha256: str
    content_ref: str
    ingested_at: datetime
    parser_config: Mapping[str, Any]
    parser_version: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    step: str
    source_file: str
    sheet_name: str
    source_row: int | None = None
    source_column: str | None = None
    value: Any = None


@dataclass(frozen=True)
class ParsedRecord:
    source_row: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class SourceContextCell:
    source_row: int
    source_column: int
    value: Any


@dataclass(frozen=True)
class ParsedTable:
    source_file: str
    sheet_name: str
    header_row: int
    header_rows: tuple[int, ...]
    original_headers: tuple[str, ...]
    source_column_positions: tuple[int, ...]
    context_cells: tuple[SourceContextCell, ...]
    records: tuple[ParsedRecord, ...]
    warnings: tuple[Issue, ...] = ()
    errors: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class HeaderMapping:
    position: int
    source_position: int
    original_name: str
    normalized_name: str
    base_normalized_name: str
    was_empty: bool
    had_collision: bool


@dataclass(frozen=True)
class ColumnSchema:
    position: int
    original_name: str
    normalized_name: str
    data_type: str
    nullable: bool
    sample_values: tuple[Any, ...]
    inference_evidence: Mapping[str, int]
    confidence: float
    inference_method: str


@dataclass(frozen=True)
class DataSchema:
    columns: tuple[ColumnSchema, ...]
    row_count: int
    header_row: int
    header_rows: tuple[int, ...]


@dataclass(frozen=True)
class CuratedRow:
    source_row: int
    values: Mapping[str, Any]


@dataclass(frozen=True)
class PipelineStepReport:
    name: str
    rows_in: int
    rows_out: int
    config: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnQuality:
    name: str
    null_count: int
    null_rate: float


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    input_row_count: int
    curated_row_count: int
    row_count_difference: int
    row_count_reasons: Mapping[str, int]
    column_count: int
    columns: tuple[ColumnQuality, ...]
    duplicate_row_count: int
    conversion_error_count: int
    conversion_error_examples: tuple[Issue, ...]
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class CuratedDataset:
    curated_id: str
    generated_at: datetime
    raw_dataset_id: str
    raw_version_id: str
    source_file: str
    sheet_name: str
    header_row: int
    header_rows: tuple[int, ...]
    context_cells: tuple[SourceContextCell, ...]
    header_mapping: tuple[HeaderMapping, ...]
    data_schema: DataSchema
    rows: tuple[CuratedRow, ...]
    pipeline_steps: tuple[PipelineStepReport, ...]
    quality_report: QualityReport
    issues: tuple[Issue, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class Phase1Result:
    raw_dataset: RawDataset
    curated_datasets: tuple[CuratedDataset, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


def resolved_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=True)
