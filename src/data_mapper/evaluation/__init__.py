"""Gold, Recall, and Pilot evaluation surface (never a production dependency)."""

from .contracts import (
    GoldRetrievalCaseResult,
    RetrievalEvaluationReport,
    SemanticPilotCaseResult,
    SemanticPilotReport,
)
from .gold_contracts import (
    GoldReviewCase,
    GoldReviewDraft,
    GoldReviewIssue,
    GoldReviewSkippedItem,
    GoldReviewValidationReport,
    HumanGoldReview,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
)
from .gold_review import (
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    COST_EXPENSE_STATEMENT,
    PROFIT_STATEMENT,
    Phase25ReviewError,
    build_gold_review_draft,
    export_gold_review_draft,
    load_gold_review_payload,
    resolve_gold_case_semantic_context,
    validate_gold_review_file,
    validate_gold_review_payload,
)
from .retrieval_evaluation import evaluate_retrieval_on_gold
from .semantic_pilot import run_semantic_pilot_on_gold

__all__ = [
    "BALANCE_SHEET",
    "CASH_FLOW_STATEMENT",
    "COST_EXPENSE_STATEMENT",
    "PROFIT_STATEMENT",
    "GoldReviewCase",
    "GoldReviewDraft",
    "GoldReviewIssue",
    "GoldReviewSkippedItem",
    "GoldReviewValidationReport",
    "GoldRetrievalCaseResult",
    "HumanGoldReview",
    "Phase25ReviewError",
    "ReviewDatasetAssignment",
    "ReviewDatasetRole",
    "RetrievalEvaluationReport",
    "SemanticPilotCaseResult",
    "SemanticPilotReport",
    "build_gold_review_draft",
    "evaluate_retrieval_on_gold",
    "export_gold_review_draft",
    "load_gold_review_payload",
    "resolve_gold_case_semantic_context",
    "run_semantic_pilot_on_gold",
    "validate_gold_review_file",
    "validate_gold_review_payload",
]
