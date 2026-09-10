"""Phase 2.5 P0 人工 Gold 审核材料的数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from .mapping_contracts import OntologyMetric


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


class ReviewDatasetRole(str, Enum):
    DEVELOPMENT_CANDIDATE = "DEVELOPMENT_CANDIDATE"
    HOLDOUT_CANDIDATE = "HOLDOUT_CANDIDATE"


@dataclass(frozen=True)
class ReviewDatasetAssignment(_JsonContract):
    report_family: str
    dataset_role: ReviewDatasetRole


@dataclass(frozen=True)
class HumanGoldReview(_JsonContract):
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
class GoldReviewCase(_JsonContract):
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
class GoldReviewSkippedItem(_JsonContract):
    subject_id: str
    source_metric_decision_id: str | None
    phase2_status: str | None
    execution_status: str
    reason: str
    source: Mapping[str, Any]
    metric_subject: Mapping[str, Any]


@dataclass(frozen=True)
class GoldReviewDraft(_JsonContract):
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
    cases: tuple[GoldReviewCase, ...]
    skipped_items: tuple[GoldReviewSkippedItem, ...]


@dataclass(frozen=True)
class GoldReviewIssue(_JsonContract):
    code: str
    message: str
    case_id: str | None = None


@dataclass(frozen=True)
class GoldReviewValidationReport(_JsonContract):
    format_version: str | None
    ontology_revision: str | None
    ready_for_p1: bool
    total_case_count: int
    pending_case_count: int
    confirmed_included_count: int
    confirmed_semantic_status_counts: Mapping[str, int]
    confirmed_dataset_coverage: Mapping[str, int]
    issues: tuple[GoldReviewIssue, ...]
