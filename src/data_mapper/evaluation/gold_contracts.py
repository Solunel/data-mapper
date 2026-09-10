"""R2 evaluation contract boundary; legacy storage is removed in R3."""

from ..phase25_contracts import (
    GoldReviewCase,
    GoldReviewDraft,
    GoldReviewIssue,
    GoldReviewSkippedItem,
    GoldReviewValidationReport,
    HumanGoldReview,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
)

__all__ = [
    "GoldReviewCase",
    "GoldReviewDraft",
    "GoldReviewIssue",
    "GoldReviewSkippedItem",
    "GoldReviewValidationReport",
    "HumanGoldReview",
    "ReviewDatasetAssignment",
    "ReviewDatasetRole",
]
