"""Recall@K evaluation over independently reviewed Gold truth."""

from __future__ import annotations

from typing import Any, Mapping

from ..candidate_retrieval import RETRIEVAL_VERSION, retrieve_metric_candidates
from ..metric_resolution_contracts import OntologyCatalog
from .contracts import GoldRetrievalCaseResult, RetrievalEvaluationReport
from .gold_review import (
    Phase25ReviewError,
    resolve_gold_case_semantic_context,
    validate_gold_review_payload,
)


def evaluate_retrieval_on_gold(
    payload: Mapping[str, Any],
    catalog: OntologyCatalog,
    *,
    top_k: int = 5,
) -> RetrievalEvaluationReport:
    """Evaluate confirmed Gold cases without becoming a production dependency."""

    if payload.get("ontology_revision") != catalog.ontology_revision:
        raise Phase25ReviewError(
            "Gold Truth 与 OntologyCatalog revision 不一致"
        )
    validation = validate_gold_review_payload(payload, catalog)
    if not validation.ready_for_p1:
        codes = ", ".join(item.code for item in validation.issues)
        raise Phase25ReviewError(f"Gold Truth 未通过 P1 门禁：{codes}")
    if top_k < 5:
        raise Phase25ReviewError("Recall@3/Recall@5 评测要求 top_k 至少为 5")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise Phase25ReviewError("Gold Truth cases 必须是数组")

    results: list[GoldRetrievalCaseResult] = []
    replay_stable = True
    map_ranks: list[int | None] = []
    for case in cases:
        review = case.get("human_review") or {}
        if not (
            review.get("review_status") == "CONFIRMED"
            and review.get("include_in_gold_set") is True
        ):
            continue
        context = resolve_gold_case_semantic_context(payload, case)
        candidate_set = retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision=case["ontology_revision"],
            metric_subject=case["metric_subject"],
            semantic_context=context,
            catalog=catalog,
            source=case.get("source"),
            top_k=top_k,
        )
        replay = retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision=case["ontology_revision"],
            metric_subject=case["metric_subject"],
            semantic_context=context,
            catalog=catalog,
            source=case.get("source"),
            top_k=top_k,
        )
        replay_stable = replay_stable and candidate_set == replay
        ranks = {
            candidate.metric.current_metric_id: candidate.rank
            for candidate in candidate_set.candidates
        }
        expected_metric_id = review.get("expected_metric_id")
        expected_rank = ranks.get(expected_metric_id) if expected_metric_id else None
        if review.get("expected_semantic_status") == "MAP_EXISTING":
            map_ranks.append(expected_rank)
        results.append(
            GoldRetrievalCaseResult(
                case_id=case["case_id"],
                report_family=case["report_family"],
                expected_semantic_status=review["expected_semantic_status"],
                expected_metric_id=expected_metric_id,
                expected_metric_rank=expected_rank,
                hard_negative_ranks={
                    metric_id: ranks.get(metric_id)
                    for metric_id in review.get("hard_negative_metric_ids") or ()
                },
            )
        )

    if not map_ranks:
        raise Phase25ReviewError(
            "Gold Truth 没有 MAP_EXISTING 案例，无法计算 Recall@K"
        )
    recall_at_3 = sum(
        rank is not None and rank <= 3 for rank in map_ranks
    ) / len(map_ranks)
    recall_at_5 = sum(
        rank is not None and rank <= 5 for rank in map_ranks
    ) / len(map_ranks)
    return RetrievalEvaluationReport(
        ontology_revision=catalog.ontology_revision,
        retrieval_version=RETRIEVAL_VERSION,
        top_k=top_k,
        included_case_count=len(results),
        map_existing_case_count=len(map_ranks),
        recall_at_3=recall_at_3,
        recall_at_5=recall_at_5,
        stable_replay=replay_stable,
        case_results=tuple(results),
    )


__all__ = ["evaluate_retrieval_on_gold"]
