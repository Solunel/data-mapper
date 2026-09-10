from __future__ import annotations

from pathlib import Path

import a as demo_entry

from data_mapper import (
    Evidence,
    JudgeOutput,
    MetricResolutionMode,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ScalarBinding,
    SemanticStatus,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
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


def test_right_click_semantic_view_uses_formal_workflow_and_results_proposed(
    monkeypatch,
) -> None:
    source = ROOT / "reports" / "集团总公司_利润表_2025-07.xlsx"
    catalog = load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )
    curated = curate_file(source).curated_datasets[0]
    monkeypatch.setattr(demo_entry, "DEFAULT_SEMANTIC_SOURCE_ROWS", (43,))
    monkeypatch.setattr(demo_entry, "DEFAULT_SEMANTIC_MAX_JUDGMENTS", 1)
    mapping_result = map_curated_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(
            mode=MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK,
            semantic_source_rows=demo_entry.DEFAULT_SEMANTIC_SOURCE_ROWS,
            semantic_max_judgments=demo_entry.DEFAULT_SEMANTIC_MAX_JUDGMENTS,
        ),
        catalog,
        ConservativeInterestJudge(),
    )
    structuring_before = mapping_result.structuring_result.to_dict()

    report, failure_count = demo_entry.build_semantic_console_report(mapping_result)

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
        report["Effective Metric Resolution"]["CONFIRMED 语义映射数"]
        == 0
    )
    assert mapping_result.structuring_result.to_dict() == structuring_before


def test_right_click_defaults_are_semantic_and_bounded() -> None:
    assert demo_entry.DEFAULT_MODE == "semantic"
    assert demo_entry.DEFAULT_TEST_PATH.name == "一级子公司A_利润表_2025-01.xlsx"
    assert demo_entry.DEFAULT_SEMANTIC_USE_LLM
    assert demo_entry.DEFAULT_SEMANTIC_SOURCE_ROWS == (13, 37, 43, 44, 58)
    assert demo_entry.DEFAULT_SEMANTIC_MAX_JUDGMENTS == 12
    assert demo_entry.DEFAULT_SEMANTIC_CANDIDATE_PREVIEW == 3
