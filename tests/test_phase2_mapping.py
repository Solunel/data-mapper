from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from data_mapper import (
    MetricMatchStatus,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    OntologyCatalogError,
    RowRole,
    ScalarBinding,
    StructureStatus,
    ValueFieldBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
)
from data_mapper.contracts import CuratedRow
from data_mapper.metric_resolution_contracts import OntologyMetric
from data_mapper.ontology_catalog import build_ontology_catalog
from data_mapper.observation_structuring import marker_aware_comparison_name


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
PROFIT_FIXTURE = FIXTURES / "财务快报-利润表.xlsx"
COST_FIXTURE = FIXTURES / "财务快报-成本费用表.xlsx"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


def _profit_curated(row_specs, *, curated_id: str = "phase2-profit-synthetic"):
    """复用“模拟数据空表”的利润表列布局，只替换少量 synthetic 值。"""

    base = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    rows = []
    for original, spec in zip(base.rows, row_specs, strict=False):
        values = dict(original.values)
        values.update(spec)
        rows.append(CuratedRow(source_row=original.source_row, values=values))
    return replace(base, curated_id=curated_id, rows=tuple(rows))


def _renamed_profit_curated(name_map, row_values, *, curated_id: str):
    base = _profit_curated(row_values, curated_id=curated_id)
    headers = tuple(
        replace(
            item,
            original_name=name_map.get(item.normalized_name, item.original_name),
            normalized_name=name_map.get(item.normalized_name, item.normalized_name),
            base_normalized_name=name_map.get(
                item.normalized_name, item.base_normalized_name
            ),
        )
        for item in base.header_mapping
    )
    columns = tuple(
        replace(
            item,
            original_name=name_map.get(item.normalized_name, item.original_name),
            normalized_name=name_map.get(item.normalized_name, item.normalized_name),
        )
        for item in base.data_schema.columns
    )
    rows = tuple(
        CuratedRow(
            source_row=row.source_row,
            values={name_map.get(key, key): value for key, value in row.values.items()},
        )
        for row in base.rows
    )
    return replace(
        base,
        header_mapping=headers,
        data_schema=replace(base.data_schema, columns=columns),
        rows=rows,
    )


def _map(curated, structuring, catalog, resolution=None):
    return map_curated_observations(
        curated,
        structuring,
        resolution or MetricResolutionRequest(),
        catalog,
    )


def test_json_loader_exposes_revision_constraints_and_does_not_modify_assets() -> None:
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (DEFINITION, KNOWLEDGE)
    }

    first = load_ontology_catalog(DEFINITION, KNOWLEDGE)
    second = load_ontology_catalog(DEFINITION, KNOWLEDGE)

    assert first.ontology_revision == second.ontology_revision
    assert first.ontology_revision.startswith("sha256:")
    assert len(first.metrics) == 331
    assert first.organization_ids == (
        "org.group_company",
        "org.level1_subsidiary_a",
        "org.level1_subsidiary_b",
        "org.level2_subsidiary_c",
        "org.level2_subsidiary_d",
    )
    assert [item.name_cn for item in first.organizations] == [
        "集团总公司",
        "一级子公司A",
        "一级子公司B",
        "二级子公司C",
        "二级子公司D",
    ]
    assert set(first.period_basis_values) == {
        "PERIOD_VALUE",
        "YEAR_TO_DATE",
        "PERIOD_BEGIN",
        "PERIOD_END",
    }
    assert {
        "business_scope",
        "period",
        "actual_value",
        "unit",
    }.issubset(first.actual_observation_required_fields)
    assert all(metric.status == "DRAFT" for metric in first.metrics)
    json.dumps(first.to_dict(), ensure_ascii=False)
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (DEFINITION, KNOWLEDGE)
    }


def test_loader_rejects_duplicate_metric_id() -> None:
    definition = json.loads(DEFINITION.read_text(encoding="utf-8"))
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    knowledge["Metric"].append(copy.deepcopy(knowledge["Metric"][0]))

    with pytest.raises(OntologyCatalogError, match="Metric id 重复"):
        build_ontology_catalog(definition, knowledge)

    alias_conflict = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    alias_conflict["Metric"][-1]["aliases"] = [alias_conflict["Metric"][0]["name_cn"]]
    with pytest.raises(OntologyCatalogError, match="alias 与正式名称冲突"):
        build_ontology_catalog(definition, alias_conflict)


def test_profit_style_ready_mapping_is_replayable_and_keeps_empty_decisions(
    catalog,
) -> None:
    curated = _profit_curated(
        [
            {"项      目": "主营业务收入", "本月数": 10, "本年累计数": 70},
            {"项      目": "从业人员平均人数", "本月数": None, "本年累计数": 20},
            {"项      目": "未知指标", "本月数": None, "本年累计数": None},
            {"项      目": "新增业务指标", "本月数": 3, "本年累计数": None},
        ]
    )
    request = ObservationStructuringRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
        metric_row_hints=(6,),
    )
    resolution = MetricResolutionRequest(ontology_gap_confirmations=(6,))

    first = _map(curated, request, catalog, resolution)
    second = _map(curated, request, catalog, resolution)

    assert first.structuring_result.report.structure_status is StructureStatus.READY
    assert first.metric_resolution_result.resolution_run_id == second.metric_resolution_result.resolution_run_id
    assert [item.observation_draft_id for item in first.structuring_result.observation_drafts] == [
        item.observation_draft_id for item in second.structuring_result.observation_drafts
    ]
    assert [item.to_dict() for item in first.metric_resolution_result.deterministic_decisions] == [
        item.to_dict() for item in second.metric_resolution_result.deterministic_decisions
    ]
    assert len(first.metric_resolution_result.deterministic_decisions) == 4
    assert len(first.structuring_result.observation_drafts) == 4
    assert first.structuring_result.report.empty_value_row_count == 1
    assert first.metric_resolution_result.report.deterministic_status_counts == {
        "MATCHED": 2,
        "AMBIGUOUS": 0,
        "UNMATCHED": 1,
        "ONTOLOGY_GAP": 1,
    }

    first_row = [
        item for item in first.structuring_result.observation_drafts if item.source_row == 3
    ]
    assert {item.period_basis for item in first_row} == {
        "PERIOD_VALUE",
        "YEAR_TO_DATE",
    }
    assert {item.period_key for item in first_row} == {"2025-07"}
    assert len({item.observation_draft_id for item in first_row}) == 2
    assert all(item.business_scope == "公司整体" for item in first_row)
    assert all(item.unit_normalized == "CNY_10K" for item in first_row)
    assert all(item.observation_draft_id.startswith("observation-draft:") for item in first_row)
    assert all(
        {binding.role for binding in item.role_bindings}
        == {
            "metric_name",
            "actual_value",
            "organization",
            "business_scope",
            "period_type",
            "period_key",
            "period_basis",
            "unit",
        }
        for item in first_row
    )
    assert all(item.organization_id is None for item in first_row)
    decisions = first.metric_resolution_result.deterministic_decisions
    assert decisions[0].selected_metric.status == "DRAFT"
    assert "exact_alias" in {
        item.code for item in decisions[1].evidence
    }
    assert decisions[2].status is MetricMatchStatus.UNMATCHED
    assert not decisions[2].ontology_gap_candidate
    assert decisions[3].status is MetricMatchStatus.ONTOLOGY_GAP
    assert decisions[3].selected_metric is None
    assert decisions[3].ontology_gap_candidate
    json.dumps(first.to_dict(), ensure_ascii=False)

    changed_rule = _map(
        curated,
        replace(request, structuring_rule_version="phase2-v3"),
        catalog,
        replace(resolution, deterministic_rule_version="phase2-v3"),
    )
    assert changed_rule.metric_resolution_result.resolution_run_id != first.metric_resolution_result.resolution_run_id
    assert changed_rule.structuring_result.observation_drafts[0].observation_draft_id != first.structuring_result.observation_drafts[0].observation_draft_id


def test_report_label_extraction_and_minimal_row_roles(catalog) -> None:
    curated = _profit_curated(
        [
            {"项      目": "（一）营业总收入", "本月数": 1, "本年累计数": None},
            {"项      目": "其中：主营业务收入", "本月数": 2, "本年累计数": None},
            {
                "项      目": "减：重大水利工程建设基金",
                "本月数": 3,
                "本年累计数": None,
            },
            {"项      目": "1.主营业务收入净额", "本月数": 4, "本年累计数": None},
            {
                "项      目": "（五）净利润（净亏损以“－”号填列）",
                "本月数": 5,
                "本年累计数": None,
            },
            {
                "项      目": "1.按所有权归属分类：",
                "本月数": 999,
                "本年累计数": None,
            },
            {
                "项      目": "注:这是报表展示说明",
                "本月数": 999,
                "本年累计数": None,
            },
            {"项      目": "其他", "本月数": 6, "本年累计数": None},
        ],
        curated_id="phase2-row-subject-extraction",
    )
    original_labels = tuple(row.values["项      目"] for row in curated.rows)

    result = _map(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        catalog,
    )

    subjects = {item.raw_label: item for item in result.structuring_result.row_subjects}
    expected_comparisons = {
        "（一）营业总收入": "营业总收入",
        "其中：主营业务收入": "主营业务收入",
        "减：重大水利工程建设基金": "重大水利工程建设基金",
        "1.主营业务收入净额": "主营业务收入净额",
        "（五）净利润（净亏损以“－”号填列）": "净利润",
    }
    for raw_label, expected in expected_comparisons.items():
        subject = subjects[raw_label]
        assert subject.raw_label == raw_label
        assert subject.comparison_name == expected
        assert subject.row_role is RowRole.METRIC
        assert "metric_comparison_name" in {item.code for item in subject.evidence}

    assert subjects["1.按所有权归属分类："].row_role is RowRole.GROUP
    assert subjects["注:这是报表展示说明"].row_role is RowRole.NOTE
    assert subjects["其他"].row_role is RowRole.UNKNOWN
    assert result.structuring_result.report.row_role_counts == {
        "METRIC": 5,
        "GROUP": 1,
        "NOTE": 1,
        "UNKNOWN": 1,
    }

    decisions = {
        item.subject.raw_label: item
        for item in result.metric_resolution_result.deterministic_decisions
    }
    assert "1.按所有权归属分类：" not in decisions
    assert "注:这是报表展示说明" not in decisions
    assert all(
        decisions[label].status is MetricMatchStatus.MATCHED
        for label in expected_comparisons
    )
    assert decisions["其他"].status is MetricMatchStatus.MATCHED
    assert not decisions["其他"].ontology_gap_candidate
    assert {item.actual_value for item in result.structuring_result.observation_drafts} == {
        1,
        2,
        3,
        4,
        5,
        6,
    }
    assert original_labels == tuple(row.values["项      目"] for row in curated.rows)


def test_real_profit_display_prefixes_do_not_create_false_unmatched(catalog) -> None:
    curated = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    result = _map(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        catalog,
    )

    decisions = {
        item.subject.raw_label: item
        for item in result.metric_resolution_result.deterministic_decisions
    }
    expected_matches = {
        "（一）营业总收入": "营业总收入",
        "其中：主营业务收入": "主营业务收入",
        "减：重大水利工程建设基金": "重大水利工程建设基金",
        "1.主营业务收入净额": "主营业务收入净额",
        "（五）净利润（净亏损以“－”号填列）": "净利润",
    }
    assert result.structuring_result.report.structure_status is StructureStatus.READY
    for raw_label, comparison in expected_matches.items():
        assert decisions[raw_label].subject.comparison_name == comparison
        assert decisions[raw_label].status is MetricMatchStatus.MATCHED
    expected_marker_matches = {
        "3.△利息收入": ("利息收入", "qc.interest_income"),
        "4.▲已赚保费": ("已赚保费", "qc.earned_premiums"),
        "*少数股东损益": ("少数股东损益", "qc.minority_interest_in_profit_or_loss"),
        "其中：利息收入": ("利息收入", "qc.finance_expense_interest_income"),
    }
    for raw_label, (comparison, current_metric_id) in expected_marker_matches.items():
        decision = decisions[raw_label]
        assert decision.subject.comparison_name == comparison
        assert decision.status is MetricMatchStatus.MATCHED
        assert decision.selected_metric.current_metric_id == current_metric_id
    assert "exact_marker_aware_formal_name" in {
        item.code for item in decisions["3.△利息收入"].evidence
    }
    assert "exact_marker_aware_formal_name" in {
        item.code for item in decisions["4.▲已赚保费"].evidence
    }
    assert "exact_marker_aware_formal_name" in {
        item.code for item in decisions["*少数股东损益"].evidence
    }
    assert "1.按所有权归属分类：" not in decisions
    assert "2.按经营持续性分类：" not in decisions
    assert not any(label.startswith("注:") for label in decisions)
    assert result.metric_resolution_result.report.deterministic_status_counts["MATCHED"] > 7
    assert not any(
        decision.ontology_gap_candidate
        for decision in result.metric_resolution_result.deterministic_decisions
        if decision.status is MetricMatchStatus.UNMATCHED
    )


@pytest.mark.parametrize(
    ("raw_label", "expected"),
    (
        ("3.△利息收入", "△利息收入"),
        ("4.▲已赚保费", "▲已赚保费"),
        ("*其中：子公司吸收少数股东投资收到的现金", "*子公司吸收少数股东投资收到的现金"),
        ("＊少数股东损益", "*少数股东损益"),
        ("△汇兑收益（损失以“-”号填列）", "△汇兑收益"),
        ("其中：利息收入", "利息收入"),
    ),
)
def test_marker_aware_comparison_retains_only_business_marker(
    raw_label, expected
) -> None:
    assert marker_aware_comparison_name(raw_label) == expected


def test_year_begin_uses_year_anchor_and_period_end_keeps_month(catalog) -> None:
    curated = _renamed_profit_curated(
        {"本月数": "年初数", "本年累计数": "期末数"},
        [
            {"项      目": "净利润", "本月数": 100, "本年累计数": 120},
        ],
        curated_id="phase2-balance-synthetic",
    )
    result = _map(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id, unit=ScalarBinding(constant="万元")),
        catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.READY
    by_basis = {
        candidate.period_basis: candidate
        for candidate in result.structuring_result.observation_drafts
    }
    assert (by_basis["PERIOD_BEGIN"].period_type, by_basis["PERIOD_BEGIN"].period_key) == (
        "YEAR",
        "2025",
    )
    assert (by_basis["PERIOD_END"].period_type, by_basis["PERIOD_END"].period_key) == (
        "MONTH",
        "2025-07",
    )


def test_unclear_year_amount_needs_binding_and_is_not_projected(catalog) -> None:
    curated = _renamed_profit_curated(
        {"本月数": "本年金额", "本年累计数": "备用金额"},
        [{"项      目": "净利润", "本月数": 1, "本年累计数": 2}],
        curated_id="phase2-unclear-period-synthetic",
    )
    result = _map(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id, unit=ScalarBinding(constant="万元")),
        catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.NEEDS_BINDING
    assert result.metric_resolution_result.deterministic_decisions[0].status is MetricMatchStatus.MATCHED
    assert not result.structuring_result.observation_drafts
    assert {item.field for item in result.structuring_result.report.unresolved_bindings} == {
        None,
        "本年金额",
        "备用金额",
    }


def test_cost_style_explicit_bindings_project_multiple_business_scopes(catalog) -> None:
    base = curate_file(COST_FIXTURE).curated_datasets[0]
    first = base.rows[0]
    values = dict(first.values)
    values.update({"成本项目": "折旧费", "发电成本": 10, "购电成本": 20})
    columns = tuple(
        replace(column, data_type="number", nullable=False)
        if column.normalized_name in {"发电成本", "购电成本"}
        else column
        for column in base.data_schema.columns
    )
    curated = replace(
        base,
        curated_id="phase2-cost-synthetic",
        rows=(CuratedRow(source_row=first.source_row, values=values),),
        data_schema=replace(base.data_schema, columns=columns),
    )
    request = ObservationStructuringRequest(
        curated_id=curated.curated_id,
        value_bindings=(
            ValueFieldBinding(
                value_field="发电成本",
                business_scope=ScalarBinding(constant="发电成本"),
                period_type="YEAR",
                period_key=ScalarBinding(field="时间"),
                period_basis="PERIOD_VALUE",
                unit=ScalarBinding(constant="元"),
            ),
            ValueFieldBinding(
                value_field="购电成本",
                business_scope=ScalarBinding(constant="购电成本"),
                period_type="YEAR",
                period_key=ScalarBinding(field="时间"),
                period_basis="PERIOD_VALUE",
                unit=ScalarBinding(constant="元"),
            ),
        ),
    )

    result = _map(curated, request, catalog)

    assert result.structuring_result.report.structure_status is StructureStatus.READY
    assert len(result.metric_resolution_result.deterministic_decisions) == 1
    assert {item.business_scope for item in result.structuring_result.observation_drafts} == {
        "发电成本",
        "购电成本",
    }
    assert {item.value_field for item in result.structuring_result.observation_drafts} == {
        "发电成本",
        "购电成本",
    }
    subject_id = result.metric_resolution_result.deterministic_decisions[0].subject.subject_id
    assert all(item.metric_subject_id == subject_id for item in result.structuring_result.observation_drafts)
    assert all(item.unit_raw == "元" and item.unit_normalized is None for item in result.structuring_result.observation_drafts)


def test_same_name_rows_are_separate_subjects_and_normalized_collision_is_ambiguous(
    catalog,
) -> None:
    curated = _profit_curated(
        [
            {"项      目": "收入 - 合计", "本月数": 1, "本年累计数": None},
            {"项      目": "收入 - 合计", "本月数": 2, "本年累计数": None},
        ],
        curated_id="phase2-ambiguous-synthetic",
    )
    additions = (
        OntologyMetric("synthetic.a", "收入-合计", (), "", (), "NUMBER", "DRAFT", "1"),
        OntologyMetric("synthetic.b", "收入—合计", (), "", (), "NUMBER", "DRAFT", "1"),
    )
    ambiguous_catalog = replace(
        catalog,
        ontology_revision="synthetic:ambiguous-catalog-v1",
        metrics=catalog.metrics + additions,
    )

    result = _map(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id, unit=ScalarBinding(constant="万元")),
        ambiguous_catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.READY
    decisions = result.metric_resolution_result.deterministic_decisions
    assert [item.status for item in decisions] == [
        MetricMatchStatus.AMBIGUOUS,
        MetricMatchStatus.AMBIGUOUS,
    ]
    assert len({item.decision_id for item in decisions}) == 2
    assert len({item.subject.subject_id for item in decisions}) == 2
    assert all(len(item.candidates) == 2 for item in decisions)


def test_restricted_key_and_valid_override_are_explainable_and_storage_agnostic(
    catalog,
) -> None:
    curated = _profit_curated(
        [
            {"项      目": "roe", "本月数": 0.1, "本年累计数": None},
            {"项      目": "人工确认名称", "本月数": 2, "本年累计数": None},
        ],
        curated_id="phase2-override-synthetic",
    )
    net_profit = next(
        metric.current_metric_id for metric in catalog.metrics if metric.name_cn == "净利润"
    )
    catalog_with_org = replace(catalog, organization_ids=("org.group",))
    result = _map(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="PERCENT"),
            organization_id="org.group",
            metric_row_hints=(4,),
        ),
        catalog_with_org,
        MetricResolutionRequest(metric_overrides={4: net_profit}),
    )

    assert result.structuring_result.report.structure_status is StructureStatus.READY
    decisions = result.metric_resolution_result.deterministic_decisions
    assert decisions[0].status is MetricMatchStatus.MATCHED
    assert "restricted_comparison_key" in {
        item.code for item in decisions[0].evidence
    }
    assert decisions[1].status is MetricMatchStatus.MATCHED
    assert "confirmed_metric_override" in {
        item.code for item in decisions[1].evidence
    }
    assert all(
        item.organization_id == "org.group"
        for item in result.structuring_result.observation_drafts
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    for storage_detail in (
        "json_path",
        "neo4j_node_id",
        "elementId",
        "cypher",
        "database_table",
    ):
        assert storage_detail not in serialized
    assert "actual_observation_id" not in serialized


def test_invalid_curated_contract_is_blocked(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "净利润", "本月数": 1, "本年累计数": 2}],
        curated_id="phase2-invalid-curated",
    )
    invalid_row = replace(
        curated.rows[0],
        values={key: value for key, value in curated.rows[0].values.items() if key != "行次"},
    )

    result = _map(
        replace(curated, rows=(invalid_row,)),
        ObservationStructuringRequest(curated_id=curated.curated_id),
        catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.BLOCKED
    assert not result.structuring_result.observation_drafts
    assert any("行键" in item.reason for item in result.structuring_result.report.unresolved_bindings)


def test_unresolved_organization_prevents_observation_candidates(catalog) -> None:
    curated = _renamed_profit_curated(
        {"公司名称": "未知组织列"},
        [{"项      目": "净利润", "本月数": 1, "本年累计数": None}],
        curated_id="phase2-unresolved-organization",
    )

    result = _map(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id, unit=ScalarBinding(constant="万元")),
        catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.NEEDS_BINDING
    assert result.metric_resolution_result.deterministic_decisions[0].status is MetricMatchStatus.MATCHED
    assert not result.structuring_result.observation_drafts
    assert result.structuring_result.report.unprojected_values[0]["reason"] == "organization_binding_unresolved"


def test_quality_failure_and_invalid_override_are_blocked_without_candidates(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "净利润", "本月数": 1, "本年累计数": 2}],
        curated_id="phase2-blocked-synthetic",
    )
    failed_quality = replace(curated.quality_report, passed=False)
    failed = _map(
        replace(curated, quality_report=failed_quality),
        ObservationStructuringRequest(curated_id=curated.curated_id),
        catalog,
    )
    assert failed.structuring_result.report.structure_status is StructureStatus.BLOCKED
    assert not failed.metric_resolution_result.deterministic_decisions
    assert not failed.structuring_result.observation_drafts

    with pytest.raises(ValueError, match="metric_overrides\\[3\\]"):
        _map(
            curated,
            ObservationStructuringRequest(
                curated_id=curated.curated_id,
                metric_row_hints=(3,),
            ),
            catalog,
            MetricResolutionRequest(
                metric_overrides={3: "invented.metric.id"}
            ),
        )


def test_non_numeric_value_is_reported_without_success_candidate(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "净利润", "本月数": "not-a-number", "本年累计数": None}],
        curated_id="phase2-type-conflict-synthetic",
    )
    result = _map(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id, unit=ScalarBinding(constant="万元")),
        catalog,
    )

    assert result.structuring_result.report.structure_status is StructureStatus.READY
    assert not result.structuring_result.observation_drafts
    assert result.structuring_result.report.unprojected_values[0]["reason"] == "value_not_numeric"
