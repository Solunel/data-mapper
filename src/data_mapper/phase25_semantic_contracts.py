"""Phase 2.5 候选召回、语义判断和派生结果的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .mapping_contracts import Evidence, OntologyMetric
from .phase25_contracts import _JsonContract


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


@dataclass(frozen=True)
class CandidateRouteScores(_JsonContract):
    name_sequence: float
    name_ngram: float
    alias_sequence: float
    alias_ngram: float
    definition_overlap: float
    business_label_overlap: float
    context_similarity: float
    total: float


@dataclass(frozen=True)
class MetricCandidate(_JsonContract):
    rank: int
    metric: OntologyMetric
    scores: CandidateRouteScores
    matched_fields: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MetricCandidateSet(_JsonContract):
    candidate_set_id: str
    source_metric_decision_id: str
    ontology_revision: str
    retrieval_version: str
    top_k: int
    semantic_context: Mapping[str, Any]
    candidates: tuple[MetricCandidate, ...]


@dataclass(frozen=True)
class GoldRetrievalCaseResult(_JsonContract):
    case_id: str
    report_family: str
    expected_semantic_status: str
    expected_metric_id: str | None
    expected_metric_rank: int | None
    hard_negative_ranks: Mapping[str, int | None]


@dataclass(frozen=True)
class RetrievalEvaluationReport(_JsonContract):
    ontology_revision: str
    retrieval_version: str
    top_k: int
    included_case_count: int
    map_existing_case_count: int
    recall_at_3: float
    recall_at_5: float
    stable_replay: bool
    case_results: tuple[GoldRetrievalCaseResult, ...]


@dataclass(frozen=True)
class JudgeOutput(_JsonContract):
    semantic_status: SemanticStatus
    selected_metric_id: str | None = None
    reason: str = ""
    supporting_evidence: tuple[Evidence, ...] = ()
    counter_evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class SemanticResolution(_JsonContract):
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
class SemanticPilotCaseResult(_JsonContract):
    case_id: str
    report_family: str
    expected_semantic_status: str
    expected_metric_id: str | None
    hard_negative_metric_ids: tuple[str, ...]
    resolution: SemanticResolution
    semantic_status_correct: bool | None
    selected_metric_correct: bool | None
    selected_hard_negative: bool
    conservative_abstention: bool
    non_conservative_error: bool


@dataclass(frozen=True)
class SemanticPilotReport(_JsonContract):
    ontology_revision: str
    retrieval_version: str
    judge: str
    judge_version: str
    prompt_version: str | None
    model: str | None
    included_case_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    semantic_status_accuracy: float | None
    map_existing_metric_accuracy: float | None
    hard_negative_false_match_count: int
    conservative_abstention_count: int
    non_conservative_error_count: int
    case_results: tuple[SemanticPilotCaseResult, ...]


@dataclass(frozen=True)
class EffectiveMetricMapping(_JsonContract):
    source_metric_decision_id: str
    ontology_revision: str
    effective_status: str
    current_metric_id: str | None
    source: str
    source_resolution_id: str | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class EffectiveMappingView(_JsonContract):
    mapping_run_id: str
    ontology_revision: str
    items: tuple[EffectiveMetricMapping, ...]


@dataclass(frozen=True)
class OntologyChangeProposal(_JsonContract):
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
