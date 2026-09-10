"""Metric Resolution 的生产数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .observation_contracts import (
    Evidence,
    JsonContract,
    MetricSubject,
    ObservationDraft,
    ObservationSchema,
    ObservationStructuringResult,
)


class MetricMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    ONTOLOGY_GAP = "ONTOLOGY_GAP"


class MetricResolutionMode(str, Enum):
    DETERMINISTIC_ONLY = "DETERMINISTIC_ONLY"
    DETERMINISTIC_WITH_SEMANTIC_FALLBACK = "DETERMINISTIC_WITH_SEMANTIC_FALLBACK"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SemanticStatus(str, Enum):
    MAP_EXISTING = "MAP_EXISTING"
    NO_EQUIVALENT = "NO_EQUIVALENT"
    AMBIGUOUS = "AMBIGUOUS"


class ResolutionReviewStatus(str, Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ProposalKind(str, Enum):
    ADD_METRIC = "ADD_METRIC"
    ADD_ALIAS = "ADD_ALIAS"


class MetricResolutionConfigurationError(ValueError):
    """The formal resolution workflow is missing valid runtime configuration."""


class MetricResolutionReplayError(ValueError):
    """Reviewed resolutions cannot be safely replayed over the supplied result."""


@dataclass(frozen=True)
class MetricResolutionRequest(JsonContract):
    mode: MetricResolutionMode = MetricResolutionMode.DETERMINISTIC_ONLY
    metric_overrides: Mapping[int, str] = field(default_factory=dict)
    ontology_gap_confirmations: tuple[int, ...] = ()
    deterministic_rule_version: str = "deterministic-resolution-v1"
    retrieval_top_k: int = 5
    semantic_context_window: int = 2
    semantic_source_rows: tuple[int, ...] = ()
    semantic_max_judgments: int | None = None


@dataclass(frozen=True)
class OntologyMetric(JsonContract):
    current_metric_id: str
    name_cn: str
    aliases: tuple[str, ...]
    definition_cn: str
    business_labels: tuple[str, ...]
    value_semantics: str | None
    status: str
    version: str


@dataclass(frozen=True)
class OntologyCatalogSummary(JsonContract):
    ontology_revision: str
    metric_count: int
    organization_count: int
    period_basis_values: tuple[str, ...]
    unit_values: tuple[str, ...]


@dataclass(frozen=True)
class OntologyCatalog(JsonContract):
    """Mapping Core 唯一依赖的只读本体值对象。"""

    ontology_revision: str
    observation_schema: ObservationSchema
    organization_ids: tuple[str, ...]
    metrics: tuple[OntologyMetric, ...]

    @property
    def actual_observation_required_fields(self) -> tuple[str, ...]:
        return self.observation_schema.actual_observation_required_fields

    @property
    def period_required_fields(self) -> tuple[str, ...]:
        return self.observation_schema.period_required_fields

    @property
    def period_basis_values(self) -> tuple[str, ...]:
        return self.observation_schema.period_basis_values

    @property
    def period_type_values(self) -> tuple[str, ...]:
        return self.observation_schema.period_type_values

    @property
    def unit_values(self) -> Mapping[str, str]:
        return self.observation_schema.unit_values

    def metric_by_id(self, current_metric_id: str) -> OntologyMetric | None:
        return next(
            (
                metric
                for metric in self.metrics
                if metric.current_metric_id == current_metric_id
            ),
            None,
        )

    def summary(self) -> OntologyCatalogSummary:
        return OntologyCatalogSummary(
            ontology_revision=self.ontology_revision,
            metric_count=len(self.metrics),
            organization_count=len(self.organization_ids),
            period_basis_values=self.period_basis_values,
            unit_values=tuple(self.unit_values),
        )


@dataclass(frozen=True)
class MetricDecision(JsonContract):
    decision_id: str
    subject: MetricSubject
    status: MetricMatchStatus
    ontology_revision: str
    selected_metric: OntologyMetric | None
    candidates: tuple[OntologyMetric, ...]
    evidence: tuple[Evidence, ...]
    ontology_gap_candidate: bool = False


@dataclass(frozen=True)
class CandidateRouteScores(JsonContract):
    name_sequence: float
    name_ngram: float
    alias_sequence: float
    alias_ngram: float
    definition_overlap: float
    business_label_overlap: float
    context_similarity: float
    total: float


@dataclass(frozen=True)
class MetricCandidate(JsonContract):
    rank: int
    metric: OntologyMetric
    scores: CandidateRouteScores
    matched_fields: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MetricCandidateSet(JsonContract):
    candidate_set_id: str
    source_metric_decision_id: str
    ontology_revision: str
    retrieval_version: str
    top_k: int
    semantic_context: Mapping[str, Any]
    candidates: tuple[MetricCandidate, ...]


@dataclass(frozen=True)
class JudgeOutput(JsonContract):
    semantic_status: SemanticStatus
    selected_metric_id: str | None = None
    reason: str = ""
    supporting_evidence: tuple[Evidence, ...] = ()
    counter_evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class SemanticResolution(JsonContract):
    resolution_id: str
    source_metric_decision_id: str
    candidate_set_id: str | None
    ontology_revision: str
    execution_status: ExecutionStatus
    semantic_status: SemanticStatus | None
    review_status: ResolutionReviewStatus | None
    selected_metric_id: str | None
    reason: str
    failure_stage: str | None
    error_code: str | None
    error_message: str | None
    supporting_evidence: tuple[Evidence, ...]
    counter_evidence: tuple[Evidence, ...]
    judge: str
    judge_version: str
    prompt_version: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class OntologyChangeProposal(JsonContract):
    proposal_id: str
    proposal_kind: ProposalKind
    source_resolution_id: str
    source_metric_decision_id: str
    ontology_revision: str
    source: Mapping[str, Any]
    raw_label: str
    comparison_name: str
    context: Mapping[str, Any]
    suggested_name_cn: str | None
    suggested_definition_cn: str | None
    suggested_value_semantics: str | None
    target_metric_id: str | None
    suggested_alias: str | None
    related_metric_ids: tuple[str, ...]
    reason: str
    evidence: tuple[Evidence, ...] = ()
    review_status: ResolutionReviewStatus = field(
        default=ResolutionReviewStatus.PROPOSED
    )


@dataclass(frozen=True)
class MetricResolutionReport(JsonContract):
    resolution_run_id: str
    deterministic_status_counts: Mapping[str, int]
    candidate_set_count: int
    semantic_execution_counts: Mapping[str, int]
    semantic_status_counts: Mapping[str, int]
    ontology_change_proposal_count: int


@dataclass(frozen=True)
class MetricResolutionResult(JsonContract):
    resolution_run_id: str
    structuring_run_id: str
    ontology_revision: str
    request: MetricResolutionRequest
    deterministic_decisions: tuple[MetricDecision, ...]
    candidate_sets: tuple[MetricCandidateSet, ...]
    semantic_resolutions: tuple[SemanticResolution, ...]
    ontology_change_proposals: tuple[OntologyChangeProposal, ...]
    report: MetricResolutionReport


@dataclass(frozen=True)
class EffectiveMetricResolution(JsonContract):
    source_metric_decision_id: str
    metric_subject_id: str
    ontology_revision: str
    effective_status: str
    current_metric_id: str | None
    source: str
    source_resolution_id: str | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class ResolvedObservation(JsonContract):
    observation: ObservationDraft
    metric_resolution: EffectiveMetricResolution


@dataclass(frozen=True)
class DataMappingResult(JsonContract):
    structuring_result: ObservationStructuringResult
    metric_resolution_result: MetricResolutionResult
    effective_metric_resolutions: tuple[EffectiveMetricResolution, ...]
    resolved_observations: tuple[ResolvedObservation, ...]
