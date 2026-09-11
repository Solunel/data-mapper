from __future__ import annotations

from pathlib import Path

from data_mapper import (
    Evidence,
    ExecutionStatus,
    JudgeOutput,
    MetricMatchStatus,
    MetricResolutionMode,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ProposalKind,
    ResolutionReviewStatus,
    ScalarBinding,
    SemanticStatus,
    apply_reviewed_resolutions,
    build_ontology_change_proposal,
    curate_file,
    map_curated_observations,
    review_resolution,
)
from gold_catalog import load_metric_gold_catalog


ROOT = Path(__file__).parents[1]
REPORT = ROOT / "reports" / "集团总公司_利润表_2025-07.xlsx"
BALANCE_REPORT = ROOT / "reports" / "集团总公司_资产负债表_2025-07.xlsx"
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"


class StaticJudge:
    name = "p3-oracle"
    version = "gold-v1"
    prompt_version = None
    model = None

    def __init__(self, output: JudgeOutput):
        self.output = output

    def judge(self, candidate_set):
        return self.output


def _base_result(report=REPORT):
    catalog = load_metric_gold_catalog(DEFINITION, KNOWLEDGE)
    curated = curate_file(report).curated_datasets[0]
    structuring = ObservationStructuringRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
    )
    result = map_curated_observations(
        curated,
        structuring,
        MetricResolutionRequest(),
        catalog,
    )
    return curated, structuring, result, catalog


def _decision(result, raw_label: str):
    return next(
        item
        for item in result.metric_resolution_result.deterministic_decisions
        if item.subject.raw_label == raw_label
    )


def _semantic_result(report, raw_label: str, output: JudgeOutput):
    curated, structuring, base, catalog = _base_result(report)
    decision = _decision(base, raw_label)
    result = map_curated_observations(
        curated,
        structuring,
        MetricResolutionRequest(
            mode=MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK,
            semantic_source_rows=(decision.subject.source_row,),
        ),
        catalog,
        StaticJudge(output),
    )
    return base, result, decision, catalog


def test_confirmed_semantic_mapping_enters_effective_view_without_mutating_structuring() -> None:
    base, proposed_result, decision, catalog = _semantic_result(
        BALANCE_REPORT,
        "资 产 总 计",
        JudgeOutput(
            SemanticStatus.MAP_EXISTING,
            selected_metric_id="qc.total_assets",
            reason="Gold oracle：展示空格不改变资产总计口径",
            supporting_evidence=(
                Evidence("gold_equivalence", "gold-v2", "业务口径一致"),
            ),
        ),
    )
    proposed = proposed_result.metric_resolution_result.semantic_resolutions[0]
    before_item = next(
        item
        for item in proposed_result.effective_metric_resolutions
        if item.source_metric_decision_id == decision.decision_id
    )
    confirmed = review_resolution(proposed, ResolutionReviewStatus.CONFIRMED)
    confirmed_result = apply_reviewed_resolutions(
        proposed_result, (confirmed,), catalog
    )
    after_item = next(
        item
        for item in confirmed_result.effective_metric_resolutions
        if item.source_metric_decision_id == decision.decision_id
    )

    assert decision.status is MetricMatchStatus.UNMATCHED
    assert before_item.effective_status == "UNRESOLVED"
    assert after_item.effective_status == "MAPPED"
    assert after_item.current_metric_id == "qc.total_assets"
    assert after_item.source == "SEMANTIC_CONFIRMED"
    assert proposed_result.structuring_result.to_dict() == (
        base.structuring_result.to_dict()
    )


def test_related_but_different_metric_stays_unresolved_and_forms_draft_proposal() -> None:
    definition_before = DEFINITION.read_bytes()
    knowledge_before = KNOWLEDGE.read_bytes()
    _, proposed_result, decision, catalog = _semantic_result(
        REPORT,
        "其中：主营业务利润",
        JudgeOutput(
            SemanticStatus.NO_EQUIVALENT,
            reason="主营业务口径小于营业利润口径",
            counter_evidence=(
                Evidence(
                    "scope_mismatch",
                    "gold-v1",
                    "qc.operating_profit 包含更广损益项目",
                ),
            ),
        ),
    )
    confirmed = review_resolution(
        proposed_result.metric_resolution_result.semantic_resolutions[0],
        ResolutionReviewStatus.CONFIRMED,
    )
    replayed = apply_reviewed_resolutions(proposed_result, (confirmed,), catalog)
    item = next(
        value
        for value in replayed.effective_metric_resolutions
        if value.source_metric_decision_id == decision.decision_id
    )
    proposal = proposed_result.metric_resolution_result.ontology_change_proposals[0]

    assert item.effective_status == "UNRESOLVED"
    assert proposal.proposal_kind is ProposalKind.ADD_METRIC
    assert proposal.suggested_name_cn == "主营业务利润"
    assert proposal.review_status is ResolutionReviewStatus.PROPOSED
    assert DEFINITION.read_bytes() == definition_before
    assert KNOWLEDGE.read_bytes() == knowledge_before


def test_ambiguous_or_failed_resolution_never_generates_proposal() -> None:
    _, result, _, _ = _semantic_result(
        REPORT,
        "其中：交易性金融资产（金融负债）",
        JudgeOutput(
            SemanticStatus.AMBIGUOUS,
            reason="报表行合并表达资产与负债，证据不足以选择单一 Metric",
            counter_evidence=(
                Evidence(
                    "multiple_business_objects",
                    "gold-v1",
                    "同一行同时出现资产和负债两个对象",
                ),
            ),
        ),
    )

    resolution = result.metric_resolution_result.semantic_resolutions[0]
    assert resolution.execution_status is ExecutionStatus.SUCCEEDED
    assert result.metric_resolution_result.ontology_change_proposals == ()


def test_add_alias_requires_explicit_context_independence_guard() -> None:
    _, result, _, _ = _semantic_result(
        BALANCE_REPORT,
        "资 产 总 计",
        JudgeOutput(
            SemanticStatus.MAP_EXISTING,
            selected_metric_id="qc.total_assets",
            reason="展示空格不改变核算对象",
            supporting_evidence=(
                Evidence("same_business_concept", "gold-v2", "口径一致"),
            ),
        ),
    )
    candidate_set = result.metric_resolution_result.candidate_sets[0]
    resolution = result.metric_resolution_result.semantic_resolutions[0]

    assert build_ontology_change_proposal(resolution, candidate_set) is None
    proposal = build_ontology_change_proposal(
        resolution,
        candidate_set,
        alias_context_independent=True,
    )
    assert proposal is not None
    assert proposal.proposal_kind is ProposalKind.ADD_ALIAS
    assert proposal.target_metric_id == "qc.total_assets"
    assert proposal.suggested_alias == "资 产 总 计"
