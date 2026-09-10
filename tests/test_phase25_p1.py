from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_mapper import (
    Phase25ReviewError,
    evaluate_retrieval_on_gold,
    load_ontology_catalog,
    resolve_gold_case_semantic_context,
    retrieve_metric_candidates,
)


ROOT = Path(__file__).parents[1]
GOLD = ROOT / "tests" / "fixtures" / "phase25" / "phase25_p0_gold_truth.json"
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


@pytest.fixture(scope="module")
def gold_payload():
    return json.loads(GOLD.read_text(encoding="utf-8"))


def test_gold_truth_is_explicitly_provenanced_and_minimal(gold_payload, catalog) -> None:
    report = evaluate_retrieval_on_gold(gold_payload, catalog)

    assert gold_payload["draft_status"] == "GOLD_CONFIRMED"
    assert gold_payload["review_provenance"]["review_kind"] == (
        "USER_AUTHORIZED_INDEPENDENT_MODEL_REVIEW"
    )
    assert not gold_payload["review_provenance"]["external_ai_labels_treated_as_truth"]
    assert report.included_case_count == 12
    assert report.map_existing_case_count == 2


def test_p1_recall_and_stability_gate(gold_payload, catalog) -> None:
    report = evaluate_retrieval_on_gold(gold_payload, catalog, top_k=5)

    assert report.recall_at_3 == 1.0
    assert report.recall_at_5 == 1.0
    assert report.stable_replay
    assert all(
        result.expected_metric_rank is not None
        for result in report.case_results
        if result.expected_semantic_status == "MAP_EXISTING"
    )


def test_retrieval_records_route_scores_but_never_semantic_status(
    gold_payload, catalog
) -> None:
    case = next(
        item
        for item in gold_payload["cases"]
        if item["human_review"]["expected_semantic_status"] == "MAP_EXISTING"
        and item["report_family"] == "BALANCE_SHEET"
    )
    semantic_context = resolve_gold_case_semantic_context(gold_payload, case)
    candidate_set = retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=semantic_context,
        catalog=catalog,
        top_k=5,
    )

    candidate_ids = [item.metric.current_metric_id for item in candidate_set.candidates]
    assert case["human_review"]["expected_metric_id"] in candidate_ids
    assert candidate_set == retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=semantic_context,
        catalog=catalog,
        top_k=5,
    )
    serialized = candidate_set.to_dict()
    assert "semantic_status" not in serialized
    assert all(item["scores"]["total"] >= 0 for item in serialized["candidates"])
    assert all(item["evidence"] for item in serialized["candidates"])


def test_same_table_conflicts_are_judgment_evidence_not_retrieval_scores(
    gold_payload, catalog
) -> None:
    case = next(
        item
        for item in gold_payload["cases"]
        if item["metric_subject"]["raw_label"] == "利息费用"
    )
    semantic_context = resolve_gold_case_semantic_context(gold_payload, case)
    with_context = retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=semantic_context,
        source=case["source"],
        catalog=catalog,
        top_k=5,
    )
    stripped_context = dict(semantic_context)
    stripped_context["same_table_metric_subjects"] = []
    stripped_context["report_notes"] = []
    without_context = retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=stripped_context,
        source=case["source"],
        catalog=catalog,
        top_k=5,
    )

    assert [item.to_dict() for item in with_context.candidates] == [
        item.to_dict() for item in without_context.candidates
    ]
    assert with_context.candidate_set_id != without_context.candidate_set_id
    conflicts = with_context.semantic_context["same_table_candidate_conflicts"]
    assert any(
        item["raw_label"] == "2.△利息支出"
        and item["source_row"] == 29
        and item["phase2_status"] == "MATCHED"
        and item["current_metric_id"] == "qc.interest_expense"
        for item in conflicts
    )


def test_retrieval_rejects_cross_revision(gold_payload, catalog) -> None:
    case = gold_payload["cases"][0]

    with pytest.raises(Phase25ReviewError, match="不允许跨 ontology revision"):
        retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision="sha256:other",
            metric_subject=case["metric_subject"],
            semantic_context=case["semantic_context"],
            catalog=catalog,
        )
