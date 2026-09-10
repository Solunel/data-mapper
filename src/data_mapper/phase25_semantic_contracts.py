"""R2 compatibility exports for former Phase 2.5 contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .metric_resolution_contracts import (
    CandidateRouteScores,
    ExecutionStatus,
    JudgeOutput,
    MetricCandidate,
    MetricCandidateSet,
    OntologyChangeProposal,
    ProposalKind,
    ResolutionReviewStatus,
    SemanticResolution,
    SemanticStatus,
)
from .observation_contracts import Evidence, JsonContract


@dataclass(frozen=True)
class GoldRetrievalCaseResult(JsonContract):
    case_id: str
    report_family: str
    expected_semantic_status: str
    expected_metric_id: str | None
    expected_metric_rank: int | None
    hard_negative_ranks: Mapping[str, int | None]


@dataclass(frozen=True)
class RetrievalEvaluationReport(JsonContract):
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
class SemanticPilotCaseResult(JsonContract):
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
class SemanticPilotReport(JsonContract):
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
class EffectiveMetricMapping(JsonContract):
    source_metric_decision_id: str
    ontology_revision: str
    effective_status: str
    current_metric_id: str | None
    source: str
    source_resolution_id: str | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class EffectiveMappingView(JsonContract):
    mapping_run_id: str
    ontology_revision: str
    items: tuple[EffectiveMetricMapping, ...]
