"""Phase 2 只读观测 Mapping 的存储无关数据契约。"""

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


class _JsonContract:
    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


class StructureStatus(str, Enum):
    READY = "READY"
    NEEDS_BINDING = "NEEDS_BINDING"
    BLOCKED = "BLOCKED"


class MetricMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    ONTOLOGY_GAP = "ONTOLOGY_GAP"


class RowRole(str, Enum):
    METRIC = "METRIC"
    GROUP = "GROUP"
    NOTE = "NOTE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence(_JsonContract):
    code: str
    source: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScalarBinding(_JsonContract):
    """请求中的常量或字段来源；两者同时填写属于无效契约。"""

    constant: Any = None
    field: str | None = None


@dataclass(frozen=True)
class ValueFieldBinding(_JsonContract):
    value_field: str
    business_scope: ScalarBinding | None = None
    period_type: str | None = None
    period_key: ScalarBinding | None = None
    period_basis: str | None = None
    unit: ScalarBinding | None = None


@dataclass(frozen=True)
class MappingRequest(_JsonContract):
    curated_id: str
    metric_name_field: str | None = None
    value_bindings: tuple[ValueFieldBinding, ...] = ()
    organization: ScalarBinding | None = None
    organization_current_id: str | None = None
    report_period_type: str | None = None
    report_period_key: ScalarBinding | None = None
    unit: ScalarBinding | None = None
    ignored_fields: Mapping[str, str] = field(default_factory=dict)
    metric_overrides: Mapping[int, str] = field(default_factory=dict)
    ontology_gap_confirmations: tuple[int, ...] = ()
    mapping_rule_version: str = "phase2-v2"


@dataclass(frozen=True)
class ResolvedBinding(_JsonContract):
    role: str
    kind: str
    value: Any = None
    field: str | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PlannedValueField(_JsonContract):
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
class IgnoredField(_JsonContract):
    field: str
    source_column: int
    reason: str
    disposition: str = "ignored"


@dataclass(frozen=True)
class UnresolvedBinding(_JsonContract):
    field: str | None
    role: str
    reason: str


@dataclass(frozen=True)
class TableMappingPlan(_JsonContract):
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
class OntologyMetric(_JsonContract):
    current_metric_id: str
    name_cn: str
    aliases: tuple[str, ...]
    definition_cn: str
    business_labels: tuple[str, ...]
    value_semantics: str | None
    status: str
    version: str


@dataclass(frozen=True)
class OntologyCatalog(_JsonContract):
    """Mapping Core 唯一依赖的只读本体值对象。"""

    ontology_revision: str
    actual_observation_required_fields: tuple[str, ...]
    period_required_fields: tuple[str, ...]
    period_basis_values: tuple[str, ...]
    period_type_values: tuple[str, ...]
    unit_values: Mapping[str, str]
    organization_ids: tuple[str, ...]
    metrics: tuple[OntologyMetric, ...]

    def metric_by_id(self, current_metric_id: str) -> OntologyMetric | None:
        return next(
            (
                metric
                for metric in self.metrics
                if metric.current_metric_id == current_metric_id
            ),
            None,
        )

    def summary(self) -> "OntologyCatalogSummary":
        return OntologyCatalogSummary(
            ontology_revision=self.ontology_revision,
            metric_count=len(self.metrics),
            organization_count=len(self.organization_ids),
            period_basis_values=self.period_basis_values,
            unit_values=tuple(self.unit_values),
        )


@dataclass(frozen=True)
class OntologyCatalogSummary(_JsonContract):
    ontology_revision: str
    metric_count: int
    organization_count: int
    period_basis_values: tuple[str, ...]
    unit_values: tuple[str, ...]


@dataclass(frozen=True)
class MetricSubject(_JsonContract):
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
        """兼容早期 Phase 2 进程内调用；序列化契约使用 raw_label。"""

        return self.raw_label


@dataclass(frozen=True)
class MetricDecision(_JsonContract):
    decision_id: str
    subject: MetricSubject
    status: MetricMatchStatus
    ontology_revision: str
    selected_metric: OntologyMetric | None
    candidates: tuple[OntologyMetric, ...]
    evidence: tuple[Evidence, ...]
    ontology_gap_candidate: bool = False


@dataclass(frozen=True)
class ObservationCandidate(_JsonContract):
    """来源候选；candidate_id 不是正式 ActualObservation.id。"""

    candidate_id: str
    candidate_identity_kind: str
    metric_decision_id: str
    metric_name: str
    ontology_revision: str
    current_metric_id: str | None
    actual_value: int | float
    business_scope: str
    organization_value: str | None
    organization_current_id: str | None
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
    mapping_rule_version: str
    role_bindings: tuple[ResolvedBinding, ...]
    binding_evidence: tuple[Evidence, ...]
    definition_constraints_satisfied: bool
    instantiation_missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class MappingPlan(_JsonContract):
    mapping_run_id: str
    curated_id: str
    raw_dataset_id: str
    raw_version_id: str
    ontology_catalog: OntologyCatalogSummary
    request: MappingRequest
    table_mapping_plan: TableMappingPlan
    row_subjects: tuple[MetricSubject, ...]
    metric_decisions: tuple[MetricDecision, ...]
    observation_candidates: tuple[ObservationCandidate, ...]


@dataclass(frozen=True)
class MappingReport(_JsonContract):
    mapping_run_id: str
    structure_status: StructureStatus
    ontology_revision: str
    row_role_counts: Mapping[str, int]
    metric_status_counts: Mapping[str, int]
    observation_candidate_count: int
    metric_decision_count: int
    empty_value_row_count: int
    unprojected_values: tuple[Mapping[str, Any], ...]
    ignored_fields: tuple[IgnoredField, ...]
    unresolved_bindings: tuple[UnresolvedBinding, ...]
    constraint_issue_counts: Mapping[str, int]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MappingResult(_JsonContract):
    plan: MappingPlan
    report: MappingReport
