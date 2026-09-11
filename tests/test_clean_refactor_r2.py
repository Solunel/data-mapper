from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from data_mapper import (
    Evidence,
    ExecutionStatus,
    JudgeOutput,
    MetricResolutionMode,
    MetricResolutionConfigurationError,
    MetricResolutionReplayError,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ResolutionReviewStatus,
    RowRole,
    ScalarBinding,
    SemanticStatus,
    apply_reviewed_resolutions,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
    review_resolution,
)
from data_mapper.candidate_retrieval import retrieve_metric_candidates as retrieve_new
from data_mapper.contracts import CuratedRow
from data_mapper.evaluation import resolve_gold_case_semantic_context


ROOT = Path(__file__).parents[1]
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
PROFIT_FIXTURE = ROOT / "tests" / "fixtures" / "财务快报-利润表.xlsx"
GOLD = ROOT / "tests" / "fixtures" / "phase25" / "phase25_p0_gold_truth.json"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


def _profit_curated(row_specs, *, curated_id: str):
    base = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    rows = []
    for original, spec in zip(base.rows, row_specs, strict=False):
        values = dict(original.values)
        values.update(spec)
        rows.append(CuratedRow(source_row=original.source_row, values=values))
    return replace(base, curated_id=curated_id, rows=tuple(rows))


def _workflow_curated():
    return _profit_curated(
        [
            {"项      目": "主营业务收入", "本月数": 10, "本年累计数": None},
            {"项      目": "资 产 总 计", "本月数": 20, "本年累计数": None},
            {"项      目": "1.按所有权归属分类：", "本月数": 30, "本年累计数": None},
            {"项      目": "注:上下文说明", "本月数": 40, "本年累计数": None},
            {"项      目": "其他", "本月数": 50, "本年累计数": None},
            {"项      目": "新增业务指标", "本月数": 60, "本年累计数": None},
        ],
        curated_id="r2-workflow",
    )


class CountingJudge:
    name = "r2-counting-judge"
    version = "r2-v1"
    prompt_version = "r2-prompt-v1"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def judge(self, candidate_set):
        self.calls += 1
        selected = candidate_set.candidates[0].metric.current_metric_id
        return JudgeOutput(
            semantic_status=SemanticStatus.MAP_EXISTING,
            selected_metric_id=selected,
            reason="固定测试判断",
            supporting_evidence=(
                Evidence("fixed_equivalence", "r2-test", "固定等价证据"),
            ),
        )


class TimeoutJudge(CountingJudge):
    def judge(self, candidate_set):
        self.calls += 1
        raise TimeoutError("provider timeout")


class FirstTimeoutThenSuccessJudge(CountingJudge):
    def judge(self, candidate_set):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("first item timeout")
        return JudgeOutput(
            semantic_status=SemanticStatus.MAP_EXISTING,
            selected_metric_id=candidate_set.candidates[0].metric.current_metric_id,
            reason="第二项正常完成",
            supporting_evidence=(
                Evidence("second_item_succeeded", "r2-test", "失败未污染后续项"),
            ),
        )


def _structuring_request(curated):
    return ObservationStructuringRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
        metric_row_hints=(8,),
    )


def _semantic_request():
    return MetricResolutionRequest(
        mode=MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK,
        ontology_gap_confirmations=(8,),
    )


def test_deterministic_only_has_zero_retrieval_and_judge_calls(catalog) -> None:
    curated = _workflow_curated()
    judge = CountingJudge()
    result = map_curated_observations(
        curated,
        _structuring_request(curated),
        MetricResolutionRequest(ontology_gap_confirmations=(8,)),
        catalog,
        judge,
    )

    assert judge.calls == 0
    assert result.metric_resolution_result.candidate_sets == ()
    assert result.metric_resolution_result.semantic_resolutions == ()
    assert result.metric_resolution_result.report.candidate_set_count == 0
    assert result.resolved_observations


def test_semantic_fallback_requires_explicit_judge(catalog) -> None:
    curated = _workflow_curated()
    with pytest.raises(MetricResolutionConfigurationError, match="Judge"):
        map_curated_observations(
            curated,
            _structuring_request(curated),
            _semantic_request(),
            catalog,
        )


def test_fallback_only_handles_metric_unresolved_and_proposed_is_not_effective(
    catalog,
) -> None:
    curated = _workflow_curated()
    judge = CountingJudge()
    result = map_curated_observations(
        curated,
        _structuring_request(curated),
        _semantic_request(),
        catalog,
        judge,
    )

    assert judge.calls == 1
    candidate_set = result.metric_resolution_result.candidate_sets[0]
    assert candidate_set.semantic_context["metric_subject"]["raw_label"] == "资 产 总 计"
    assert result.metric_resolution_result.semantic_resolutions[0].review_status is (
        ResolutionReviewStatus.PROPOSED
    )
    asset_effective = next(
        item
        for item in result.effective_metric_resolutions
        if item.metric_subject_id
        == candidate_set.semantic_context["metric_subject"]["subject_id"]
    )
    assert asset_effective.effective_status == "UNRESOLVED"
    assert asset_effective.current_metric_id is None
    assert all(
        item.metric_id is None
        for item in result.resolved_observations
        if item.observation.metric_subject_id == asset_effective.metric_subject_id
    )
    assert {item.subject.row_role for item in result.metric_resolution_result.deterministic_decisions} >= {
        RowRole.METRIC,
        RowRole.UNKNOWN,
    }
    json.dumps(result.to_dict(), ensure_ascii=False)


def test_review_replay_is_pure_and_only_confirmed_map_existing_becomes_effective(
    catalog,
) -> None:
    curated = _workflow_curated()
    judge = CountingJudge()
    original = map_curated_observations(
        curated,
        _structuring_request(curated),
        _semantic_request(),
        catalog,
        judge,
    )
    original_payload = original.to_dict()
    proposed = original.metric_resolution_result.semantic_resolutions[0]
    confirmed = review_resolution(proposed, ResolutionReviewStatus.CONFIRMED)
    replayed = apply_reviewed_resolutions(original, (confirmed,), catalog)

    assert judge.calls == 1
    assert original.to_dict() == original_payload
    assert replayed.structuring_result is original.structuring_result
    assert replayed.metric_resolution_result.candidate_sets == (
        original.metric_resolution_result.candidate_sets
    )
    mapped = next(
        item
        for item in replayed.effective_metric_resolutions
        if item.source_metric_decision_id == confirmed.source_metric_decision_id
    )
    assert mapped.effective_status == "MAPPED"
    assert mapped.current_metric_id == confirmed.selected_metric_id
    assert mapped.source == "SEMANTIC_CONFIRMED"
    assert all(
        item.metric_id == confirmed.selected_metric_id
        for item in replayed.resolved_observations
        if item.observation.metric_subject_id == mapped.metric_subject_id
    )

    rejected = review_resolution(proposed, ResolutionReviewStatus.REJECTED)
    rejected_result = apply_reviewed_resolutions(original, (rejected,), catalog)
    rejected_effective = next(
        item
        for item in rejected_result.effective_metric_resolutions
        if item.source_metric_decision_id == rejected.source_metric_decision_id
    )
    assert rejected_effective.effective_status == "UNRESOLVED"
    with pytest.raises(MetricResolutionReplayError, match="显式"):
        apply_reviewed_resolutions(original, (proposed,), catalog)


def test_review_replay_rejects_revision_candidate_and_duplicate_mismatch(catalog) -> None:
    curated = _workflow_curated()
    result = map_curated_observations(
        curated,
        _structuring_request(curated),
        _semantic_request(),
        catalog,
        CountingJudge(),
    )
    confirmed = review_resolution(
        result.metric_resolution_result.semantic_resolutions[0],
        ResolutionReviewStatus.CONFIRMED,
    )
    with pytest.raises(MetricResolutionReplayError, match="revision"):
        apply_reviewed_resolutions(
            result,
            (confirmed,),
            replace(catalog, ontology_revision="test:other-revision"),
        )
    with pytest.raises(MetricResolutionReplayError, match="CandidateSet"):
        apply_reviewed_resolutions(
            result,
            (replace(confirmed, candidate_set_id="candidate-set:other"),),
            catalog,
        )
    with pytest.raises(MetricResolutionReplayError, match="只能提供一个"):
        apply_reviewed_resolutions(result, (confirmed, confirmed), catalog)
    with pytest.raises(MetricResolutionReplayError, match="只能改变"):
        apply_reviewed_resolutions(
            result,
            (replace(confirmed, reason="审核时篡改了原始结论"),),
            catalog,
        )


def test_provider_failure_is_recorded_without_mutating_other_layers(catalog) -> None:
    curated = _workflow_curated()
    judge = TimeoutJudge()
    result = map_curated_observations(
        curated,
        _structuring_request(curated),
        _semantic_request(),
        catalog,
        judge,
    )

    assert judge.calls == 1
    resolution = result.metric_resolution_result.semantic_resolutions[0]
    assert resolution.execution_status is ExecutionStatus.FAILED
    assert resolution.error_code == "TIMEOUT"
    assert all(
        item.source != "SEMANTIC_CONFIRMED"
        for item in result.effective_metric_resolutions
    )


def test_single_provider_failure_does_not_contaminate_later_resolution(catalog) -> None:
    curated = _profit_curated(
        [
            {"项      目": "资 产 总 计", "本月数": 1, "本年累计数": None},
            {"项      目": "营 业 总 收 入", "本月数": 2, "本年累计数": None},
        ],
        curated_id="r2-provider-isolation",
    )
    judge = FirstTimeoutThenSuccessJudge()
    result = map_curated_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(
            mode=MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK
        ),
        catalog,
        judge,
    )

    assert judge.calls == 2
    assert [item.execution_status for item in result.metric_resolution_result.semantic_resolutions] == [
        ExecutionStatus.FAILED,
        ExecutionStatus.SUCCEEDED,
    ]


def test_resolution_request_cannot_change_roles_or_drafts(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "其他", "本月数": 1, "本年累计数": None}],
        curated_id="r2-no-reverse-hints",
    )
    structuring = ObservationStructuringRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
    )
    plain = map_curated_observations(
        curated,
        structuring,
        MetricResolutionRequest(),
        catalog,
    )
    overridden = map_curated_observations(
        curated,
        structuring,
        MetricResolutionRequest(
            metric_overrides={3: "qc.core_business_revenue"}
        ),
        catalog,
    )

    assert plain.structuring_result.row_subjects[0].row_role is RowRole.UNKNOWN
    assert overridden.structuring_result.row_subjects[0].row_role is RowRole.UNKNOWN
    assert plain.structuring_result.to_dict() == overridden.structuring_result.to_dict()
    assert plain.resolved_observations[0].observation.observation_draft_id == (
        overridden.resolved_observations[0].observation.observation_draft_id
    )


def test_retrieval_preserves_frozen_gold_ranking_context_and_stable_id(catalog) -> None:
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    case = next(
        item for item in payload["cases"] if item["metric_subject"]["raw_label"] == "利息费用"
    )
    kwargs = {
        "source_metric_decision_id": case["source_metric_decision_id"],
        "ontology_revision": catalog.ontology_revision,
        "metric_subject": case["metric_subject"],
        "semantic_context": resolve_gold_case_semantic_context(payload, case),
        "source": case["source"],
        "catalog": catalog,
        "top_k": 5,
    }
    current = retrieve_new(**kwargs)

    assert current.candidate_set_id == (
        "candidate-set:a7a2321d08cc69a308263c99e1a33dd06ddb1baa57f71e7a2237ba2034331214"
    )
    assert [item.metric.current_metric_id for item in current.candidates] == [
        "qc.interest_expense",
        "qc.r_and_d_expense",
        "qc.interest_income",
        "qc.finance_expenses",
        "qc.policyholder_dividends_expense",
    ]
    assert current.semantic_context["same_table_candidate_conflicts"]
    assert current.semantic_context["report_notes"]
    assert current.semantic_context["table_value_context"]


def test_resolved_observation_is_only_draft_plus_effective_resolution(catalog) -> None:
    curated = _workflow_curated()
    result = map_curated_observations(
        curated,
        _structuring_request(curated),
        MetricResolutionRequest(ontology_gap_confirmations=(8,)),
        catalog,
    )

    payload = result.resolved_observations[0].to_dict()
    assert set(payload) == {
        "observation",
        "metric_resolution",
        "metric_id",
        "organization_id",
    }
    assert payload["metric_id"] == payload["metric_resolution"]["current_metric_id"]
    forbidden = {
        "InstantiationReadiness",
        "definition_constraints_satisfied",
        "instantiation_missing_fields",
    }
    assert forbidden.isdisjoint(payload)


def test_production_modules_do_not_import_evaluation() -> None:
    production = (
        "__init__.py",
        "candidate_retrieval.py",
        "semantic_resolution.py",
        "deepseek_judge.py",
        "metric_resolution.py",
        "workflow.py",
    )
    for filename in production:
        source = (ROOT / "src" / "data_mapper" / filename).read_text(encoding="utf-8")
        assert "phase25" not in source.casefold()
        assert "evaluation" not in source.casefold()
        assert "gold" not in source.casefold()

    entry_source = (ROOT / "a.py").read_text(encoding="utf-8")
    assert "map_curated_dataset" not in entry_source
    assert "MappingRequest" not in entry_source
    assert entry_source.count("map_curated_observations(") == 1
