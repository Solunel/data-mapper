"""R1 迁移期旧 Phase 2 复合契约。

新代码应分别使用 observation_contracts 与 metric_resolution_contracts；本模块仅
维持仓库内消费者迁移窗口，R3 删除旧复合 DTO。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .metric_resolution_contracts import (
    MetricDecision,
    MetricMatchStatus,
    OntologyCatalog,
    OntologyCatalogSummary,
    OntologyMetric,
)
from .observation_contracts import (
    Evidence,
    IgnoredField,
    JsonContract,
    MetricSubject,
    PlannedValueField,
    ResolvedBinding,
    RowRole,
    ScalarBinding,
    StructureStatus,
    TableMappingPlan,
    UnresolvedBinding,
    ValueFieldBinding,
)


_JsonContract = JsonContract


@dataclass(frozen=True)
class MappingRequest(JsonContract):
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
class ObservationCandidate(JsonContract):
    """迁移期来源候选；R3 随旧主链删除。"""

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
class MappingPlan(JsonContract):
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
class MappingReport(JsonContract):
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
class MappingResult(JsonContract):
    plan: MappingPlan
    report: MappingReport


__all__ = [
    "Evidence",
    "IgnoredField",
    "MappingPlan",
    "MappingReport",
    "MappingRequest",
    "MappingResult",
    "MetricDecision",
    "MetricMatchStatus",
    "MetricSubject",
    "ObservationCandidate",
    "OntologyCatalog",
    "OntologyCatalogSummary",
    "OntologyMetric",
    "PlannedValueField",
    "ResolvedBinding",
    "RowRole",
    "ScalarBinding",
    "StructureStatus",
    "TableMappingPlan",
    "UnresolvedBinding",
    "ValueFieldBinding",
]
