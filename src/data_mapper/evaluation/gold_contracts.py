"""Gold review material evaluation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from ..metric_resolution_contracts import OntologyMetric
from ..observation_contracts import JsonContract


class ReviewDatasetRole(str, Enum):
    DEVELOPMENT_CANDIDATE = "DEVELOPMENT_CANDIDATE"
    HOLDOUT_CANDIDATE = "HOLDOUT_CANDIDATE"


@dataclass(frozen=True)
class ReviewDatasetAssignment(JsonContract):
    report_family: str
    dataset_role: ReviewDatasetRole


@dataclass(frozen=True)
class HumanGoldReview(JsonContract):
    """兼容字段名；P0 导出不预填，实际 reviewer 必须可追溯。"""

    include_in_gold_set: bool | None = None
    expected_semantic_status: str | None = None
    expected_metric_id: str | None = None
    allowed_metric_ids: tuple[str, ...] = ()
    hard_negative_metric_ids: tuple[str, ...] = ()
    review_status: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_basis: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class GoldReviewCase(JsonContract):
    case_id: str
    source_fingerprint: str
    source_metric_decision_id: str
    ontology_revision: str
    dataset_role: ReviewDatasetRole
    report_family: str
    mapping_run_id: str
    curated_id: str
    raw_dataset_id: str
    raw_version_id: str
    phase2_status: str
    source: Mapping[str, Any]
    metric_subject: Mapping[str, Any]
    semantic_context: Mapping[str, Any]
    human_review: HumanGoldReview = field(default_factory=HumanGoldReview)


@dataclass(frozen=True)
class GoldReviewSkippedItem(JsonContract):
    subject_id: str
    source_metric_decision_id: str | None
    phase2_status: str | None
    execution_status: str
    reason: str
    source: Mapping[str, Any]
    metric_subject: Mapping[str, Any]


@dataclass(frozen=True)
class GoldReviewDraft(JsonContract):
    format_version: str
    export_version: str
    draft_id: str
    draft_status: str
    ontology_revision: str
    context_window: int
    mapping_run_ids: tuple[str, ...]
    required_development_family: str
    required_holdout_families: tuple[str, ...]
    instructions: tuple[str, ...]
    ontology_metrics: tuple[OntologyMetric, ...]
    table_contexts: tuple[Mapping[str, Any], ...]
    cases: tuple[GoldReviewCase, ...]
    skipped_items: tuple[GoldReviewSkippedItem, ...]


@dataclass(frozen=True)
class GoldReviewIssue(JsonContract):
    code: str
    message: str
    case_id: str | None = None


@dataclass(frozen=True)
class GoldReviewValidationReport(JsonContract):
    format_version: str | None
    ontology_revision: str | None
    ready_for_p1: bool
    total_case_count: int
    pending_case_count: int
    confirmed_included_count: int
    confirmed_semantic_status_counts: Mapping[str, int]
    confirmed_dataset_coverage: Mapping[str, int]
    issues: tuple[GoldReviewIssue, ...]
