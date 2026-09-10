"""Semantic judge pilot over independently reviewed Gold truth."""

from __future__ import annotations

from typing import Any, Mapping

from ..candidate_retrieval import RETRIEVAL_VERSION, retrieve_metric_candidates
from ..metric_resolution_contracts import (
    ExecutionStatus,
    OntologyCatalog,
    SemanticStatus,
)
from ..semantic_resolution import SemanticJudge, run_semantic_judgment
from .contracts import SemanticPilotCaseResult, SemanticPilotReport
from .gold_review import (
    Phase25ReviewError,
    resolve_gold_case_semantic_context,
    validate_gold_review_payload,
)


def run_semantic_pilot_on_gold(
    payload: Mapping[str, Any],
    catalog: OntologyCatalog,
    judge: SemanticJudge,
    *,
    top_k: int = 5,
) -> SemanticPilotReport:
    """Run a read-only Gold pilot; resolutions remain proposed."""

    if payload.get("ontology_revision") != catalog.ontology_revision:
        raise Phase25ReviewError(
            "Gold Truth 与 OntologyCatalog revision 不一致"
        )
    validation = validate_gold_review_payload(payload, catalog)
    if not validation.ready_for_p1:
        codes = ", ".join(item.code for item in validation.issues)
        raise Phase25ReviewError(
            f"Gold Truth 未通过 P1/P2 Pilot 门禁：{codes}"
        )
    if top_k < 5:
        raise Phase25ReviewError("Semantic Pilot 要求 top_k 至少为 5")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise Phase25ReviewError("Gold Truth cases 必须是数组")

    results: list[SemanticPilotCaseResult] = []
    semantic_correct_count = 0
    map_correct_count = 0
    map_case_count = 0
    succeeded_count = 0
    failed_count = 0
    skipped_count = 0
    hard_negative_false_match_count = 0
    conservative_abstention_count = 0
    non_conservative_error_count = 0

    for case in cases:
        review = case.get("human_review") or {}
        if not (
            review.get("review_status") == "CONFIRMED"
            and review.get("include_in_gold_set") is True
        ):
            continue
        candidate_set = retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision=case["ontology_revision"],
            metric_subject=case["metric_subject"],
            semantic_context=resolve_gold_case_semantic_context(payload, case),
            source=case.get("source"),
            catalog=catalog,
            top_k=top_k,
        )
        resolution = run_semantic_judgment(candidate_set, catalog, judge)
        expected_status = str(review["expected_semantic_status"])
        expected_metric_id = review.get("expected_metric_id")
        hard_negatives = tuple(review.get("hard_negative_metric_ids") or ())

        semantic_correct: bool | None = None
        selected_correct: bool | None = None
        if resolution.execution_status is ExecutionStatus.SUCCEEDED:
            succeeded_count += 1
            semantic_correct = resolution.semantic_status is SemanticStatus(
                expected_status
            )
            semantic_correct_count += int(semantic_correct)
            if expected_status == SemanticStatus.MAP_EXISTING.value:
                map_case_count += 1
                selected_correct = (
                    resolution.selected_metric_id == expected_metric_id
                )
                map_correct_count += int(selected_correct)
        elif resolution.execution_status is ExecutionStatus.FAILED:
            failed_count += 1
            if expected_status == SemanticStatus.MAP_EXISTING.value:
                map_case_count += 1
                selected_correct = False
        else:
            skipped_count += 1
            if expected_status == SemanticStatus.MAP_EXISTING.value:
                map_case_count += 1
                selected_correct = False

        selected_hard_negative = resolution.selected_metric_id in hard_negatives
        hard_negative_false_match_count += int(selected_hard_negative)
        conservative_abstention = (
            resolution.execution_status is ExecutionStatus.SUCCEEDED
            and expected_status == SemanticStatus.NO_EQUIVALENT.value
            and resolution.semantic_status is SemanticStatus.AMBIGUOUS
        )
        non_conservative_error = (
            resolution.execution_status is ExecutionStatus.SUCCEEDED
            and semantic_correct is False
            and not conservative_abstention
        )
        conservative_abstention_count += int(conservative_abstention)
        non_conservative_error_count += int(non_conservative_error)
        results.append(
            SemanticPilotCaseResult(
                case_id=case["case_id"],
                report_family=case["report_family"],
                expected_semantic_status=expected_status,
                expected_metric_id=expected_metric_id,
                hard_negative_metric_ids=hard_negatives,
                resolution=resolution,
                semantic_status_correct=semantic_correct,
                selected_metric_correct=selected_correct,
                selected_hard_negative=selected_hard_negative,
                conservative_abstention=conservative_abstention,
                non_conservative_error=non_conservative_error,
            )
        )

    if not results:
        raise Phase25ReviewError(
            "Gold Truth 没有可用于 Semantic Pilot 的案例"
        )
    return SemanticPilotReport(
        ontology_revision=catalog.ontology_revision,
        retrieval_version=RETRIEVAL_VERSION,
        judge=judge.name,
        judge_version=judge.version,
        prompt_version=judge.prompt_version,
        model=judge.model,
        included_case_count=len(results),
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        semantic_status_accuracy=semantic_correct_count / len(results),
        map_existing_metric_accuracy=(
            map_correct_count / map_case_count if map_case_count else None
        ),
        hard_negative_false_match_count=hard_negative_false_match_count,
        conservative_abstention_count=conservative_abstention_count,
        non_conservative_error_count=non_conservative_error_count,
        case_results=tuple(results),
    )


__all__ = ["run_semantic_pilot_on_gold"]
