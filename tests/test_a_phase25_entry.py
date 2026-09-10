from __future__ import annotations

from pathlib import Path

import a as demo_entry

from data_mapper import (
    Evidence,
    JudgeOutput,
    MappingRequest,
    ScalarBinding,
    SemanticStatus,
    curate_file,
    load_ontology_catalog,
    map_curated_dataset,
)


ROOT = Path(__file__).parents[1]


class ConservativeInterestJudge:
    name = "entry-test-judge"
    version = "v1"
    prompt_version = "prompt-v1"
    model = None

    def judge(self, candidate_set):
        return JudgeOutput(
            semantic_status=SemanticStatus.NO_EQUIVALENT,
            reason="同表已有独立利息支出项目",
            counter_evidence=(
                Evidence(
                    "same_table_metric_conflict",
                    "entry_test",
                    "利息支出已占用该本体 Metric",
                ),
            ),
        )


def test_right_click_phase25_view_keeps_phase2_frozen_and_results_proposed(
    monkeypatch,
) -> None:
    source = ROOT / "reports" / "集团总公司_利润表_2025-07.xlsx"
    catalog = load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )
    curated = curate_file(source).curated_datasets[0]
    mapping_result = map_curated_dataset(
        curated,
        MappingRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        catalog,
    )
    phase2_before = mapping_result.plan.to_dict()
    monkeypatch.setattr(demo_entry, "DEFAULT_PHASE25_SOURCE_ROWS", (43,))
    monkeypatch.setattr(demo_entry, "DEFAULT_PHASE25_MAX_JUDGMENTS", 1)

    report, failure_count = demo_entry.build_phase25_console_report(
        mapping_result,
        catalog,
        ConservativeInterestJudge(),
    )

    assert failure_count == 0
    assert report["Eligibility"]["本次实际判断数"] == 1
    detail = report["结果明细"][0]
    assert detail["raw_label"] == "利息费用"
    assert detail["SemanticResolution"]["semantic_status"] == "NO_EQUIVALENT"
    assert detail["SemanticResolution"]["review_status"] == "PROPOSED"
    assert "候选摘要" in detail["CandidateSet"]
    assert "上下文摘要" not in detail["CandidateSet"]
    assert any(
        item["源行"] == 29
        and item["current_metric_id"] == "qc.interest_expense"
        for item in detail["CandidateSet"]["同表候选冲突"]
    )
    assert (
        report["Effective Mapping（只读派生视图）"][
            "Phase 2.5 CONFIRMED 映射数"
        ]
        == 0
    )
    assert mapping_result.plan.to_dict() == phase2_before


def test_right_click_defaults_are_phase25_and_bounded() -> None:
    assert demo_entry.DEFAULT_PHASE == "2.5"
    assert demo_entry.DEFAULT_TEST_PATH.name == "一级子公司A_利润表_2025-01.xlsx"
    assert demo_entry.DEFAULT_PHASE25_USE_LLM
    assert demo_entry.DEFAULT_PHASE25_SOURCE_ROWS == (13, 37, 43, 44, 58)
    assert demo_entry.DEFAULT_PHASE25_MAX_JUDGMENTS == 12
    assert not demo_entry.DEFAULT_PHASE25_SHOW_FULL_PHASE2
    assert not demo_entry.DEFAULT_PHASE25_SHOW_DETAIL
    assert demo_entry.DEFAULT_PHASE25_CANDIDATE_PREVIEW == 3
