from __future__ import annotations

from pathlib import Path

from data_mapper import (
    Evidence,
    ExecutionStatus,
    JudgeOutput,
    MappingRequest,
    MetricMatchStatus,
    ProposalKind,
    ResolutionReviewStatus,
    ScalarBinding,
    SemanticStatus,
    build_effective_mapping_view,
    build_ontology_change_proposal,
    curate_file,
    load_ontology_catalog,
    map_curated_dataset,
    retrieve_candidates_for_decision,
    review_resolution,
    run_semantic_judgment,
)


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


def _real_plan(report=REPORT):
    catalog = load_ontology_catalog(DEFINITION, KNOWLEDGE)
    curated = curate_file(report).curated_datasets[0]
    result = map_curated_dataset(
        curated,
        MappingRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        catalog,
    )
    return result.plan, catalog


def _decision(plan, raw_label: str):
    return next(
        item for item in plan.metric_decisions if item.subject.raw_label == raw_label
    )


def test_confirmed_phase25_mapping_enters_effective_view_without_mutating_phase2() -> None:
    plan, catalog = _real_plan(BALANCE_REPORT)
    decision = _decision(plan, "资 产 总 计")
    phase2_before = plan.to_dict()
    candidate_set = retrieve_candidates_for_decision(decision, plan, catalog)
    proposed = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
            JudgeOutput(
                SemanticStatus.MAP_EXISTING,
                selected_metric_id="qc.total_assets",
                reason="Gold oracle：展示空格不改变资产总计口径",
                supporting_evidence=(
                    Evidence("gold_equivalence", "gold-v2", "业务口径一致"),
                ),
            )
        ),
    )
    before_confirmation = build_effective_mapping_view(plan, (proposed,), catalog)
    confirmed = review_resolution(proposed, ResolutionReviewStatus.CONFIRMED)
    after_confirmation = build_effective_mapping_view(plan, (confirmed,), catalog)
    before_item = next(
        item
        for item in before_confirmation.items
        if item.source_metric_decision_id == decision.decision_id
    )
    after_item = next(
        item
        for item in after_confirmation.items
        if item.source_metric_decision_id == decision.decision_id
    )

    assert decision.status is MetricMatchStatus.UNMATCHED
    assert before_item.effective_status == "UNRESOLVED"
    assert after_item.effective_status == "MAPPED"
    assert after_item.current_metric_id == "qc.total_assets"
    assert after_item.source == "PHASE_2_5_CONFIRMED"
    assert plan.to_dict() == phase2_before


def test_related_but_different_metric_stays_unresolved_and_forms_draft_proposal() -> None:
    plan, catalog = _real_plan()
    definition_before = DEFINITION.read_bytes()
    knowledge_before = KNOWLEDGE.read_bytes()
    decision = _decision(plan, "其中：主营业务利润")
    candidate_set = retrieve_candidates_for_decision(decision, plan, catalog)
    resolution = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
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
            )
        ),
    )
    confirmed = review_resolution(resolution, ResolutionReviewStatus.CONFIRMED)
    view = build_effective_mapping_view(plan, (confirmed,), catalog)
    proposal = build_ontology_change_proposal(confirmed, candidate_set)
    item = next(
        value
        for value in view.items
        if value.source_metric_decision_id == decision.decision_id
    )

    assert item.effective_status == "UNRESOLVED"
    assert proposal is not None
    assert proposal.proposal_kind is ProposalKind.ADD_METRIC
    assert proposal.suggested_name_cn == "主营业务利润"
    assert proposal.review_status is ResolutionReviewStatus.PROPOSED
    assert DEFINITION.read_bytes() == definition_before
    assert KNOWLEDGE.read_bytes() == knowledge_before


def test_ambiguous_or_failed_resolution_never_generates_proposal() -> None:
    plan, catalog = _real_plan()
    decision = _decision(plan, "其中：交易性金融资产（金融负债）")
    candidate_set = retrieve_candidates_for_decision(decision, plan, catalog)
    ambiguous = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
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
            )
        ),
    )

    assert ambiguous.execution_status is ExecutionStatus.SUCCEEDED
    assert build_ontology_change_proposal(ambiguous, candidate_set) is None


def test_add_alias_requires_explicit_context_independence_guard() -> None:
    plan, catalog = _real_plan(BALANCE_REPORT)
    decision = _decision(plan, "资 产 总 计")
    candidate_set = retrieve_candidates_for_decision(decision, plan, catalog)
    resolution = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
            JudgeOutput(
                SemanticStatus.MAP_EXISTING,
                selected_metric_id="qc.total_assets",
                reason="展示空格不改变核算对象",
                supporting_evidence=(
                    Evidence("same_business_concept", "gold-v2", "口径一致"),
                ),
            )
        ),
    )

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
