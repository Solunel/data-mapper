from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from data_mapper import (
    MappingRequest,
    ScalarBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_dataset,
)
from data_mapper.evaluation import (
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    COST_EXPENSE_STATEMENT,
    PROFIT_STATEMENT,
    Phase25ReviewError,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
    build_gold_review_draft,
    export_gold_review_draft,
    resolve_gold_case_semantic_context,
    validate_gold_review_file,
    validate_gold_review_payload,
)
from data_mapper.contracts import CuratedRow


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PROFIT_FIXTURE = FIXTURES / "财务快报-利润表.xlsx"
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
GOLD_TRUTH = (
    ROOT / "tests" / "fixtures" / "phase25" / "phase25_p0_gold_truth.json"
)


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


def _review_plan(catalog, *, source_file: str, curated_id: str):
    base = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    labels = ("合成待审指标", "净利润", "其他", "合成本体缺口")
    rows = tuple(
        CuratedRow(
            source_row=original.source_row,
            values={**original.values, "项      目": label},
        )
        for original, label in zip(base.rows[: len(labels)], labels, strict=True)
    )
    curated = replace(
        base,
        curated_id=curated_id,
        source_file=source_file,
        rows=rows,
    )
    request = MappingRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
        ontology_gap_confirmations=(rows[-1].source_row,),
    )
    return map_curated_dataset(curated, request, catalog).plan


def _four_plan_draft(catalog):
    specs = (
        (
            "财务快报-利润表.xlsx",
            "phase25-profit",
            PROFIT_STATEMENT,
            ReviewDatasetRole.DEVELOPMENT_CANDIDATE,
        ),
        (
            "财务快报-资产负债表.xlsx",
            "phase25-balance",
            BALANCE_SHEET,
            ReviewDatasetRole.HOLDOUT_CANDIDATE,
        ),
        (
            "财务快报-现金流量表.xlsx",
            "phase25-cash-flow",
            CASH_FLOW_STATEMENT,
            ReviewDatasetRole.HOLDOUT_CANDIDATE,
        ),
        (
            "财务快报-成本费用表.xlsx",
            "phase25-cost",
            COST_EXPENSE_STATEMENT,
            ReviewDatasetRole.HOLDOUT_CANDIDATE,
        ),
    )
    plans = tuple(
        _review_plan(catalog, source_file=name, curated_id=curated_id)
        for name, curated_id, _, _ in specs
    )
    assignments = {
        plan.mapping_run_id: ReviewDatasetAssignment(
            report_family=report_family,
            dataset_role=dataset_role,
        )
        for plan, (_, _, report_family, dataset_role) in zip(
            plans, specs, strict=True
        )
    }
    return build_gold_review_draft(plans, catalog, assignments), plans


def test_p0_export_is_eligible_context_only_and_replayable(catalog) -> None:
    first, plans = _four_plan_draft(catalog)
    second, _ = _four_plan_draft(catalog)

    assert first.to_dict() == second.to_dict()
    assert first.draft_id == second.draft_id
    assert len(first.cases) == 4
    assert len(first.skipped_items) == 12
    assert first.required_development_family == PROFIT_STATEMENT
    assert set(first.required_holdout_families) == {
        BALANCE_SHEET,
        CASH_FLOW_STATEMENT,
        COST_EXPENSE_STATEMENT,
    }
    assert [item.current_metric_id for item in first.ontology_metrics] == sorted(
        item.current_metric_id for item in catalog.metrics
    )
    assert len(first.table_contexts) == 4

    before = [plan.to_dict() for plan in plans]
    for case in first.cases:
        assert case.source_fingerprint.startswith("gold-review-source:")
        assert case.phase2_status == "UNMATCHED"
        assert case.metric_subject["row_role"] == "METRIC"
        assert case.metric_subject["raw_label"] == "合成待审指标"
        assert case.human_review.include_in_gold_set is None
        assert case.human_review.expected_semantic_status is None
        assert case.human_review.expected_metric_id is None
        assert case.human_review.review_status is None
        assert len(case.semantic_context["following_subjects"]) == 2
        assert case.semantic_context["table_context_id"].startswith(
            "gold-table-context:"
        )
        resolved_context = resolve_gold_case_semantic_context(
            first.to_dict(), case.to_dict()
        )
        assert len(resolved_context["same_table_metric_subjects"]) == 2
        assert resolved_context["report_notes"] == []
    assert {item.reason for item in first.skipped_items} == {
        "PHASE2_MATCHED",
        "PHASE2_ONTOLOGY_GAP",
        "ROW_ROLE_UNKNOWN",
    }
    assert any(
        item.metric_subject["raw_label"] == "其他"
        and item.reason == "ROW_ROLE_UNKNOWN"
        for item in first.skipped_items
    )
    assert before == [plan.to_dict() for plan in plans]

    serialized = json.dumps(first.to_dict(), ensure_ascii=False)
    assert '"actual_value"' not in serialized
    assert '"candidates"' not in serialized
    assert '"candidate_set_id"' not in serialized
    assert "similarity" not in serialized


def test_blank_draft_stops_at_human_confirmation_gate(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)

    report = validate_gold_review_payload(draft.to_dict(), catalog)

    assert not report.ready_for_p1
    assert report.total_case_count == 4
    assert report.pending_case_count == 4
    assert report.confirmed_included_count == 0
    assert {item.code for item in report.issues} == {
        "MAP_EXISTING_REQUIRED",
        "HARD_NEGATIVE_REQUIRED",
        "HOLDOUT_FAMILY_REQUIRED",
    }


def test_independently_confirmed_synthetic_contract_can_pass_p1_gate(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)
    net_profit = next(
        item.current_metric_id for item in catalog.metrics if item.name_cn == "净利润"
    )
    hard_negative_metric_id = next(
        item.current_metric_id
        for item in catalog.metrics
        if item.current_metric_id != net_profit
    )
    reviewed_cases = []
    for case in draft.cases:
        semantic_status = (
            "MAP_EXISTING"
            if case.report_family == PROFIT_STATEMENT
            else "NO_EQUIVALENT"
        )
        reviewed_cases.append(
            replace(
                case,
                human_review=replace(
                    case.human_review,
                    include_in_gold_set=True,
                    expected_semantic_status=semantic_status,
                    expected_metric_id=(
                        net_profit if semantic_status == "MAP_EXISTING" else None
                    ),
                    hard_negative_metric_ids=(hard_negative_metric_id,),
                    review_status="CONFIRMED",
                    reviewer="synthetic-contract-reviewer",
                    reviewed_at="2026-09-09T12:00:00+08:00",
                    review_basis="仅用于验证人工 Gold 契约状态组合的合成测试。",
                ),
            )
        )
    reviewed = replace(draft, cases=tuple(reviewed_cases))

    report = validate_gold_review_payload(reviewed.to_dict(), catalog)

    assert report.ready_for_p1
    assert not report.issues
    assert report.confirmed_included_count == 4
    assert report.confirmed_semantic_status_counts == {
        "AMBIGUOUS": 0,
        "MAP_EXISTING": 1,
        "NO_EQUIVALENT": 3,
    }


def test_validator_rejects_unconfirmed_and_cross_revision_answers(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)
    payload = draft.to_dict()
    first = payload["cases"][0]
    first["ontology_revision"] = "sha256:other"
    first["human_review"].update(
        {
            "include_in_gold_set": True,
            "expected_semantic_status": "MAP_EXISTING",
            "expected_metric_id": "invented.metric.id",
            "hard_negative_metric_ids": [],
            "review_status": "PROPOSED",
        }
    )

    report = validate_gold_review_payload(payload, catalog)

    codes = {item.code for item in report.issues}
    assert "CASE_REVISION_MISMATCH" in codes
    assert "CASE_SOURCE_FINGERPRINT_MISMATCH" in codes
    assert "INCLUDED_CASE_NOT_CONFIRMED" in codes
    assert not report.ready_for_p1


def test_validator_rejects_modified_source_and_ontology_reference(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)
    payload = draft.to_dict()
    payload["cases"][0]["metric_subject"]["raw_label"] = "被修改的来源文本"
    payload["ontology_metrics"].pop()

    report = validate_gold_review_payload(payload, catalog)

    codes = {item.code for item in report.issues}
    assert "CASE_SOURCE_FINGERPRINT_MISMATCH" in codes
    assert "ONTOLOGY_REFERENCE_MISMATCH" in codes
    assert not report.ready_for_p1


def test_validator_rejects_modified_table_context(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)
    payload = draft.to_dict()
    payload["table_contexts"][0]["same_table_metric_subjects"][0][
        "raw_label"
    ] = "被篡改的同表项目"

    report = validate_gold_review_payload(payload, catalog)

    assert "TABLE_CONTEXT_FINGERPRINT_MISMATCH" in {
        item.code for item in report.issues
    }
    assert not report.ready_for_p1


def test_export_is_exclusive_and_validation_is_read_only(catalog) -> None:
    draft, _ = _four_plan_draft(catalog)
    destination = ROOT / "outputs" / f"pytest-phase25-review-{uuid4().hex}.json"

    try:
        exported = export_gold_review_draft(draft, destination)
        before = exported.read_bytes()
        report = validate_gold_review_file(exported, catalog)

        assert not report.ready_for_p1
        assert exported.read_bytes() == before
        with pytest.raises(FileExistsError):
            export_gold_review_draft(draft, destination)
    finally:
        destination.unlink(missing_ok=True)


def test_export_rejects_ontology_revision_mismatch(catalog) -> None:
    plan = _review_plan(
        catalog,
        source_file="财务快报-利润表.xlsx",
        curated_id="phase25-revision-mismatch",
    )
    mismatched = replace(
        plan,
        ontology_catalog=replace(
            plan.ontology_catalog,
            ontology_revision="sha256:other",
        ),
    )
    assignment = ReviewDatasetAssignment(
        report_family=PROFIT_STATEMENT,
        dataset_role=ReviewDatasetRole.DEVELOPMENT_CANDIDATE,
    )

    with pytest.raises(Phase25ReviewError, match="ontology_revision 不一致"):
        build_gold_review_draft(
            (mismatched,),
            catalog,
            {mismatched.mapping_run_id: assignment},
        )


def test_user_authorized_gold_truth_passes_p1_gate(catalog) -> None:
    report = validate_gold_review_file(GOLD_TRUTH, catalog)

    assert report.ready_for_p1
    assert report.pending_case_count == 0
    assert report.confirmed_included_count == 12
    assert report.confirmed_semantic_status_counts == {
        "AMBIGUOUS": 0,
        "MAP_EXISTING": 2,
        "NO_EQUIVALENT": 10,
    }
