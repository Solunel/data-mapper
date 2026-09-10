"""Observation Structuring 的存储无关数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


class JsonContract:
    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


class StructureStatus(str, Enum):
    READY = "READY"
    NEEDS_BINDING = "NEEDS_BINDING"
    BLOCKED = "BLOCKED"


class RowRole(str, Enum):
    METRIC = "METRIC"
    GROUP = "GROUP"
    NOTE = "NOTE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence(JsonContract):
    code: str
    source: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaField(JsonContract):
    name: str
    value_type: str
    target: str | None
    required: bool


@dataclass(frozen=True)
class ObservationSchema(JsonContract):
    """Definition 中 ActualObservation 及必要 struct/enum 的窄只读投影。"""

    actual_observation_fields: tuple[SchemaField, ...]
    period_fields: tuple[SchemaField, ...]
    period_constraints: Mapping[str, Any]
    period_basis_values: tuple[str, ...]
    period_type_values: tuple[str, ...]
    unit_values: Mapping[str, str]
    status_values: tuple[str, ...]
    fingerprint: str

    @property
    def actual_observation_required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.actual_observation_fields if field.required)

    @property
    def period_required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.period_fields if field.required)


@dataclass(frozen=True)
class ScalarBinding(JsonContract):
    """请求中的常量或字段来源；两者同时填写属于无效契约。"""

    constant: Any = None
    field: str | None = None


@dataclass(frozen=True)
class ValueFieldBinding(JsonContract):
    value_field: str
    business_scope: ScalarBinding | None = None
    period_type: str | None = None
    period_key: ScalarBinding | None = None
    period_basis: str | None = None
    unit: ScalarBinding | None = None


@dataclass(frozen=True)
class ObservationStructuringRequest(JsonContract):
    curated_id: str
    metric_name_field: str | None = None
    value_bindings: tuple[ValueFieldBinding, ...] = ()
    organization: ScalarBinding | None = None
    organization_id: str | None = None
    report_period_type: str | None = None
    report_period_key: ScalarBinding | None = None
    unit: ScalarBinding | None = None
    ignored_fields: Mapping[str, str] = field(default_factory=dict)
    metric_row_hints: tuple[int, ...] = ()
    structuring_rule_version: str = "observation-structuring-v1"


@dataclass(frozen=True)
class ResolvedBinding(JsonContract):
    role: str
    kind: str
    value: Any = None
    field: str | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PlannedValueField(JsonContract):
    value_field: str
    source_column: int
    business_scope: ResolvedBinding | None
    period_type: str | None
    period_key: ResolvedBinding | None
    period_basis: str | None
    unit: ResolvedBinding | None
    binding_complete: bool
    evidence: tuple[Evidence, ...] = ()
    unresolved_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class IgnoredField(JsonContract):
    field: str
    source_column: int
    reason: str
    disposition: str = "ignored"


@dataclass(frozen=True)
class UnresolvedBinding(JsonContract):
    field: str | None
    role: str
    reason: str


@dataclass(frozen=True)
class TableMappingPlan(JsonContract):
    structure_status: StructureStatus
    metric_name_field: str | None
    metric_name_source_column: int | None
    organization: ResolvedBinding | None
    value_fields: tuple[PlannedValueField, ...]
    source_file: str
    sheet_name: str
    source_context: tuple[Mapping[str, Any], ...]
    ignored_fields: tuple[IgnoredField, ...]
    unresolved_bindings: tuple[UnresolvedBinding, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MetricSubject(JsonContract):
    subject_id: str
    raw_label: str
    row_role: RowRole
    comparison_name: str
    comparison_key: str
    source_file: str
    sheet_name: str
    source_row: int
    source_column: int
    context: Mapping[str, Any]
    evidence: tuple[Evidence, ...]

    @property
    def raw_name(self) -> str:
        return self.raw_label


@dataclass(frozen=True)
class ObservationDraft(JsonContract):
    """结构化工作态观测；不包含 Metric Resolution 或实例化 readiness。"""

    observation_draft_id: str
    metric_subject_id: str
    metric_name: str
    actual_value: int | float
    business_scope: str
    organization_value: str | None
    organization_id: str | None
    period_type: str
    period_key: str
    period_basis: str
    unit_raw: str | None
    unit_normalized: str | None
    source: str
    source_file: str
    sheet_name: str
    source_row: int
    metric_source_column: int
    value_source_column: int
    value_field: str
    curated_id: str
    raw_dataset_id: str
    raw_version_id: str
    structuring_rule_version: str
    role_bindings: tuple[ResolvedBinding, ...]
    binding_evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class ObservationStructuringReport(JsonContract):
    structuring_run_id: str
    structure_status: StructureStatus
    row_role_counts: Mapping[str, int]
    observation_draft_count: int
    metric_subject_count: int
    empty_value_row_count: int
    unprojected_values: tuple[Mapping[str, Any], ...]
    ignored_fields: tuple[IgnoredField, ...]
    unresolved_bindings: tuple[UnresolvedBinding, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ObservationStructuringResult(JsonContract):
    structuring_run_id: str
    curated_id: str
    raw_dataset_id: str
    raw_version_id: str
    observation_schema_fingerprint: str
    request: ObservationStructuringRequest
    table_mapping_plan: TableMappingPlan
    row_subjects: tuple[MetricSubject, ...]
    observation_drafts: tuple[ObservationDraft, ...]
    report: ObservationStructuringReport
