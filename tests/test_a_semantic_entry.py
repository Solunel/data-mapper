from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import a as demo_entry

from data_mapper import (
    Evidence,
    JudgeOutput,
    MetricResolutionMode,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ScalarBinding,
    SemanticStatus,
    StructureStatus,
    curate_file,
    instantiate_observations,
    load_ontology_catalog,
    map_curated_observations,
)
from data_mapper.contracts import CuratedRow
from gold_catalog import load_metric_gold_catalog


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
    catalog = load_metric_gold_catalog(
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


def test_right_click_defaults_are_instantiation_full_and_bounded() -> None:
    assert demo_entry.DEFAULT_MODE == "instantiation"
    assert demo_entry.DEFAULT_FULL_OUTPUT
    assert demo_entry.DEFAULT_SAVE_OUTPUT_JSON
    assert demo_entry.DEFAULT_INSTANTIATION_ONLY
    assert demo_entry.DEFAULT_OUTPUT_DIRECTORY == ROOT / "outputs"
    assert demo_entry.DEFAULT_TEST_PATH.name == "一级子公司A_利润表_2025-01.xlsx"
    assert demo_entry.DEFAULT_SEMANTIC_USE_LLM
    assert demo_entry.DEFAULT_SEMANTIC_SOURCE_ROWS == (13, 37, 43, 44, 58)
    assert demo_entry.DEFAULT_SEMANTIC_MAX_JUDGMENTS == 12
    assert demo_entry.DEFAULT_SEMANTIC_CANDIDATE_PREVIEW == 3


def test_entry_auto_binds_known_cost_expense_template() -> None:
    source = ROOT / "tests" / "fixtures" / "财务快报-成本费用表.xlsx"
    catalog = load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )
    base = curate_file(source).curated_datasets[0]
    first = base.rows[0]
    values = dict(first.values)
    values.update(
        {
            "公司名称": "集团总公司",
            "成本项目": "折旧费",
            "发电成本": 10,
            "购电成本": 20,
        }
    )
    columns = tuple(
        replace(column, data_type="number", nullable=False)
        if column.normalized_name in {"发电成本", "购电成本"}
        else column
        for column in base.data_schema.columns
    )
    curated = replace(
        base,
        curated_id="entry-cost-expense-auto-binding",
        rows=(CuratedRow(source_row=first.source_row, values=values),),
        data_schema=replace(base.data_schema, columns=columns),
    )
    args = SimpleNamespace(
        unit="万元",
        organization=None,
        period_key=None,
        period_type=None,
        metric_name_field=None,
    )

    structuring_request, resolution_request = demo_entry.build_mapping_requests(
        curated,
        args,
        semantic_fallback=False,
    )
    result = map_curated_observations(
        curated,
        structuring_request,
        resolution_request,
        catalog,
    )
    instantiated = instantiate_observations((result,), catalog, status="DRAFT")

    assert len(structuring_request.value_bindings) == 8
    assert {
        binding.business_scope.constant
        for binding in structuring_request.value_bindings
        if binding.business_scope is not None
    } == set(demo_entry._COST_EXPENSE_SCOPE_FIELDS)
    assert all(
        binding.period_type == "YEAR"
        and binding.period_key == ScalarBinding(field="时间")
        and binding.period_basis == "PERIOD_VALUE"
        for binding in structuring_request.value_bindings
    )
    assert result.structuring_result.report.structure_status is StructureStatus.READY
    assert len(result.structuring_result.observation_drafts) == 2
    assert len(instantiated.actual_observations) == 2
    assert not instantiated.unresolved_metrics
    assert not instantiated.blocked_observations


def test_output_flags_can_override_the_config_default(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["a.py"])
    assert demo_entry.parse_arguments().full is None
    assert demo_entry.parse_arguments().instantiation_only is None

    monkeypatch.setattr("sys.argv", ["a.py", "--full"])
    assert demo_entry.parse_arguments().full is True

    monkeypatch.setattr("sys.argv", ["a.py", "--overview"])
    assert demo_entry.parse_arguments().full is False

    monkeypatch.setattr("sys.argv", ["a.py", "--save-output-json"])
    assert demo_entry.parse_arguments().save_output_json is True

    monkeypatch.setattr("sys.argv", ["a.py", "--no-save-output-json"])
    assert demo_entry.parse_arguments().save_output_json is False

    monkeypatch.setattr(
        "sys.argv",
        ["a.py", "--mode", "instantiation", "--instantiation-only"],
    )
    assert demo_entry.parse_arguments().instantiation_only is True

    monkeypatch.setattr(
        "sys.argv",
        ["a.py", "--mode", "instantiation", "--include-mapping"],
    )
    assert demo_entry.parse_arguments().instantiation_only is False


def test_save_output_json_is_exclusive_and_never_overwrites(tmp_path) -> None:
    first = demo_entry.save_output_json(
        '{"run": 1}',
        tmp_path,
        "deterministic",
        timestamp="20260911-120000-000000",
    )
    second = demo_entry.save_output_json(
        '{"run": 2}',
        tmp_path,
        "deterministic",
        timestamp="20260911-120000-000000",
    )

    assert first.name == "data-mapper-deterministic-20260911-120000-000000.json"
    assert second.name == "data-mapper-deterministic-20260911-120000-000000_v2.json"
    assert first.read_text(encoding="utf-8") == '{"run": 1}\n'
    assert second.read_text(encoding="utf-8") == '{"run": 2}\n'
