"""R0 业务基线的可重复构造器。

该模块只提取跨 DTO 迁移仍需保持的业务语义；实时 LLM 自由文本和随机
resolution_id 不进入 parity。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from data_mapper import (
    Evidence,
    JudgeOutput,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ResolutionReviewStatus,
    ScalarBinding,
    SemanticStatus,
    ValueFieldBinding,
    build_ontology_change_proposal,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
    retrieve_metric_candidates,
    review_resolution,
    run_semantic_judgment,
)
from data_mapper.contracts import CuratedRow
from data_mapper.evaluation import (
    evaluate_retrieval_on_gold,
    resolve_gold_case_semantic_context,
)
from data_mapper.candidate_retrieval import retrieve_candidates_for_decision
from data_mapper.metric_resolution_contracts import OntologyMetric
from data_mapper.semantic_resolution import derive_effective_metric_resolutions
from gold_catalog import load_metric_gold_catalog


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
PROFIT_FIXTURE = FIXTURES / "财务快报-利润表.xlsx"
COST_FIXTURE = FIXTURES / "财务快报-成本费用表.xlsx"
GOLD = FIXTURES / "phase25" / "phase25_p0_gold_truth.json"


class FixedJudge:
    name = "r0-fixed-judge"
    version = "r0-v1"
    prompt_version = "r0-prompt-v1"
    model = None

    def __init__(self, output: JudgeOutput):
        self.output = output

    def judge(self, _candidate_set):
        return self.output


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profit_curated(row_specs, *, curated_id: str):
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


def _phase1_dataset_snapshot(path: Path) -> dict[str, Any]:
    result = curate_file(path)
    dataset_payloads = []
    for dataset in result.curated_datasets:
        payload = dataset.to_dict()
        payload.pop("generated_at")
        dataset_payloads.append((dataset, payload))
    return {
        "raw_dataset_id": result.raw_dataset.dataset_id,
        "raw_version_id": result.raw_dataset.version_id,
        "raw_sha256": result.raw_dataset.sha256,
        "datasets": [
            {
                "curated_id": dataset.curated_id,
                "sheet_name": dataset.sheet_name,
                "header_rows": list(dataset.header_rows),
                "columns": [
                    {
                        "name": column.normalized_name,
                        "type": column.data_type,
                        "nullable": column.nullable,
                    }
                    for column in dataset.data_schema.columns
                ],
                "source_row_count": len(dataset.rows),
                "source_row_bounds": (
                    [dataset.rows[0].source_row, dataset.rows[-1].source_row]
                    if dataset.rows
                    else []
                ),
                "quality_passed": dataset.quality_report.passed,
                "business_snapshot_sha256": _canonical_hash(payload),
            }
            for dataset, payload in dataset_payloads
        ],
    }


def _mapping_snapshot(result) -> dict[str, Any]:
    structuring = result.structuring_result
    resolution = result.metric_resolution_result
    effective = {
        item.metric_subject_id: item for item in result.effective_metric_resolutions
    }
    return {
        "structure_status": structuring.report.structure_status.value,
        "structuring_run_id": structuring.structuring_run_id,
        "resolution_run_id": resolution.resolution_run_id,
        "subjects": [
            {
                "subject_id": item.subject_id,
                "raw_label": item.raw_label,
                "comparison_name": item.comparison_name,
                "row_role": item.row_role.value,
                "evidence_codes": [evidence.code for evidence in item.evidence],
            }
            for item in structuring.row_subjects
        ],
        "decisions": [
            {
                "decision_id": item.decision_id,
                "subject_id": item.subject.subject_id,
                "status": item.status.value,
                "selected_metric_id": (
                    item.selected_metric.current_metric_id
                    if item.selected_metric is not None
                    else None
                ),
                "candidate_metric_ids": [
                    metric.current_metric_id for metric in item.candidates
                ],
                "ontology_gap_candidate": item.ontology_gap_candidate,
                "evidence_codes": [evidence.code for evidence in item.evidence],
            }
            for item in resolution.deterministic_decisions
        ],
        "drafts": [
            {
                "observation_draft_id": item.observation_draft_id,
                "metric_subject_id": item.metric_subject_id,
                "current_metric_id": effective[item.metric_subject_id].current_metric_id,
                "actual_value": item.actual_value,
                "business_scope": item.business_scope,
                "period_type": item.period_type,
                "period_key": item.period_key,
                "period_basis": item.period_basis,
                "unit_raw": item.unit_raw,
                "unit_normalized": item.unit_normalized,
                "source_row": item.source_row,
                "value_field": item.value_field,
                "role_bindings": [binding.role for binding in item.role_bindings],
            }
            for item in structuring.observation_drafts
        ],
        "row_role_counts": dict(structuring.report.row_role_counts),
        "metric_status_counts": dict(resolution.report.deterministic_status_counts),
        "unresolved_bindings": [
            item.to_dict() for item in structuring.report.unresolved_bindings
        ],
        "unprojected_values": list(structuring.report.unprojected_values),
    }


def _build_phase2_snapshot(catalog) -> dict[str, Any]:
    ready_curated = _profit_curated(
        [
            {"项      目": "主营业务收入", "本月数": 10, "本年累计数": 70},
            {"项      目": "从业人员平均人数", "本月数": None, "本年累计数": 20},
            {"项      目": "未知指标", "本月数": None, "本年累计数": None},
            {"项      目": "新增业务指标", "本月数": 3, "本年累计数": None},
            {"项      目": "1.按所有权归属分类：", "本月数": 999, "本年累计数": None},
            {"项      目": "注:这是基线说明", "本月数": 999, "本年累计数": None},
            {"项      目": "其他", "本月数": 6, "本年累计数": None},
        ],
        curated_id="r0-ready",
    )
    ready = map_curated_observations(
        ready_curated,
        ObservationStructuringRequest(
            curated_id=ready_curated.curated_id,
            unit=ScalarBinding(constant="万元"),
            metric_row_hints=(6,),
        ),
        MetricResolutionRequest(ontology_gap_confirmations=(6,)),
        catalog,
    )

    partial_curated = _renamed_profit_curated(
        {"本年累计数": "本年金额"},
        [{"项      目": "净利润", "本月数": 10, "本年累计数": 80}],
        curated_id="r0-needs-binding-partial",
    )
    partial = map_curated_observations(
        partial_curated,
        ObservationStructuringRequest(
            curated_id=partial_curated.curated_id,
            value_bindings=(
                ValueFieldBinding(
                    value_field="本月数",
                    business_scope=ScalarBinding(constant="公司整体"),
                    period_type="MONTH",
                    period_key=ScalarBinding(field="时间"),
                    period_basis="PERIOD_VALUE",
                    unit=ScalarBinding(constant="万元"),
                ),
                ValueFieldBinding(
                    value_field="本年金额",
                    business_scope=ScalarBinding(constant="公司整体"),
                    period_type="MONTH",
                    period_key=ScalarBinding(field="时间"),
                    unit=ScalarBinding(constant="万元"),
                ),
            ),
        ),
        MetricResolutionRequest(),
        catalog,
    )

    blocked = map_curated_observations(
        replace(
            partial_curated,
            quality_report=replace(partial_curated.quality_report, passed=False),
        ),
        ObservationStructuringRequest(curated_id=partial_curated.curated_id),
        MetricResolutionRequest(),
        catalog,
    )

    anchors_curated = _renamed_profit_curated(
        {"本月数": "年初数", "本年累计数": "期末数"},
        [{"项      目": "净利润", "本月数": 100, "本年累计数": 120}],
        curated_id="r0-period-anchors",
    )
    anchors = map_curated_observations(
        anchors_curated,
        ObservationStructuringRequest(
            curated_id=anchors_curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(),
        catalog,
    )

    cost_base = curate_file(COST_FIXTURE).curated_datasets[0]
    cost_row = cost_base.rows[0]
    cost_values = dict(cost_row.values)
    cost_values.update({"成本项目": "折旧费", "发电成本": 10, "购电成本": 20})
    cost_columns = tuple(
        replace(column, data_type="number", nullable=False)
        if column.normalized_name in {"发电成本", "购电成本"}
        else column
        for column in cost_base.data_schema.columns
    )
    cost_curated = replace(
        cost_base,
        curated_id="r0-multi-scope",
        rows=(CuratedRow(source_row=cost_row.source_row, values=cost_values),),
        data_schema=replace(cost_base.data_schema, columns=cost_columns),
    )
    multi_scope = map_curated_observations(
        cost_curated,
        ObservationStructuringRequest(
            curated_id=cost_curated.curated_id,
            value_bindings=tuple(
                ValueFieldBinding(
                    value_field=field,
                    business_scope=ScalarBinding(constant=field),
                    period_type="YEAR",
                    period_key=ScalarBinding(field="时间"),
                    period_basis="PERIOD_VALUE",
                    unit=ScalarBinding(constant="元"),
                )
                for field in ("发电成本", "购电成本")
            ),
        ),
        MetricResolutionRequest(),
        catalog,
    )

    ambiguous_curated = _profit_curated(
        [{"项      目": "收入 - 合计", "本月数": 1, "本年累计数": None}],
        curated_id="r0-ambiguous",
    )
    ambiguous_catalog = replace(
        catalog,
        ontology_revision="synthetic:r0-ambiguous-v1",
        metrics=catalog.metrics
        + (
            OntologyMetric(
                "synthetic.a", "收入-合计", (), "", (), "NUMBER", "DRAFT", "1"
            ),
            OntologyMetric(
                "synthetic.b", "收入—合计", (), "", (), "NUMBER", "DRAFT", "1"
            ),
        ),
    )
    ambiguous = map_curated_observations(
        ambiguous_curated,
        ObservationStructuringRequest(
            curated_id=ambiguous_curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(),
        ambiguous_catalog,
    )

    ready_snapshot = _mapping_snapshot(ready)
    partial_snapshot = _mapping_snapshot(partial)
    blocked_snapshot = _mapping_snapshot(blocked)
    anchors_snapshot = _mapping_snapshot(anchors)
    multi_scope_snapshot = _mapping_snapshot(multi_scope)
    ambiguous_snapshot = _mapping_snapshot(ambiguous)
    return {
        "ready_four_metric_states": {
            "snapshot_sha256": _canonical_hash(ready_snapshot),
            "structure_status": ready_snapshot["structure_status"],
            "row_roles": [item["row_role"] for item in ready_snapshot["subjects"]],
            "decision_statuses": [
                item["status"] for item in ready_snapshot["decisions"]
            ],
            "subject_ids": [item["subject_id"] for item in ready_snapshot["subjects"]],
            "decision_ids": [
                item["decision_id"] for item in ready_snapshot["decisions"]
            ],
            "observation_draft_ids": [
                item["observation_draft_id"] for item in ready_snapshot["drafts"]
            ],
            "candidate_period_bases": [
                item["period_basis"] for item in ready_snapshot["drafts"]
            ],
            "metric_status_counts": ready_snapshot["metric_status_counts"],
        },
        "needs_binding_partial_projection": {
            "snapshot_sha256": _canonical_hash(partial_snapshot),
            "structure_status": partial_snapshot["structure_status"],
            "projected": partial_snapshot["drafts"],
            "unprojected_values": partial_snapshot["unprojected_values"],
            "unresolved_bindings": partial_snapshot["unresolved_bindings"],
        },
        "blocked": {
            "snapshot_sha256": _canonical_hash(blocked_snapshot),
            "structure_status": blocked_snapshot["structure_status"],
            "draft_count": len(blocked_snapshot["drafts"]),
            "decision_count": len(blocked_snapshot["decisions"]),
        },
        "period_begin_end": {
            "snapshot_sha256": _canonical_hash(anchors_snapshot),
            "periods": [
                {
                    "period_type": item["period_type"],
                    "period_key": item["period_key"],
                    "period_basis": item["period_basis"],
                }
                for item in anchors_snapshot["drafts"]
            ],
        },
        "multi_business_scope": {
            "snapshot_sha256": _canonical_hash(multi_scope_snapshot),
            "scopes": [
                item["business_scope"] for item in multi_scope_snapshot["drafts"]
            ],
            "value_fields": [
                item["value_field"] for item in multi_scope_snapshot["drafts"]
            ],
        },
        "ambiguous": {
            "snapshot_sha256": _canonical_hash(ambiguous_snapshot),
            "status": ambiguous_snapshot["decisions"][0]["status"],
            "candidate_metric_ids": ambiguous_snapshot["decisions"][0][
                "candidate_metric_ids"
            ],
        },
    }


def _semantic_resolution_snapshot(resolution) -> dict[str, Any]:
    value = resolution.to_dict()
    value.pop("resolution_id")
    return value


def _effective_item_snapshot(item) -> dict[str, Any]:
    return {
        "source_metric_decision_id": item.source_metric_decision_id,
        "effective_status": item.effective_status,
        "current_metric_id": item.current_metric_id,
        "source": item.source,
        "evidence_codes": [evidence.code for evidence in item.evidence],
    }


def _build_phase25_snapshot(catalog) -> dict[str, Any]:
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    evaluation = evaluate_retrieval_on_gold(payload, catalog, top_k=5)
    interest_case = next(
        item
        for item in payload["cases"]
        if item["metric_subject"]["raw_label"] == "利息费用"
    )
    interest_context = resolve_gold_case_semantic_context(payload, interest_case)
    interest_candidates = retrieve_metric_candidates(
        source_metric_decision_id=interest_case["source_metric_decision_id"],
        ontology_revision=interest_case["ontology_revision"],
        metric_subject=interest_case["metric_subject"],
        semantic_context=interest_context,
        source=interest_case["source"],
        catalog=catalog,
        top_k=5,
    )
    interest_resolution = run_semantic_judgment(
        interest_candidates,
        catalog,
        FixedJudge(
            JudgeOutput(
                SemanticStatus.AMBIGUOUS,
                reason="同表独立利息支出构成反证，当前证据不足",
                counter_evidence=(
                    Evidence(
                        "same_table_independent_metric",
                        "r0-fixed-response",
                        "同表存在独立利息支出",
                    ),
                ),
            )
        ),
    )

    effective_curated = _profit_curated(
        [
            {"项      目": "资 产 总 计", "本月数": 1, "本年累计数": None},
            {"项      目": "其中：主营业务利润", "本月数": 2, "本年累计数": None},
        ],
        curated_id="r0-phase25-effective",
    )
    effective_result = map_curated_observations(
        effective_curated,
        ObservationStructuringRequest(
            curated_id=effective_curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(),
        catalog,
    )
    structuring_before = effective_result.structuring_result.to_dict()
    decisions = effective_result.metric_resolution_result.deterministic_decisions
    asset_decision = next(
        item
        for item in decisions
        if item.subject.raw_label == "资 产 总 计"
    )
    asset_candidates = retrieve_candidates_for_decision(
        asset_decision,
        effective_result.structuring_result,
        decisions,
        catalog,
    )
    asset_proposed = run_semantic_judgment(
        asset_candidates,
        catalog,
        FixedJudge(
            JudgeOutput(
                SemanticStatus.MAP_EXISTING,
                selected_metric_id="qc.total_assets",
                reason="展示空格不改变资产总计口径",
                supporting_evidence=(
                    Evidence("same_business_concept", "r0-fixed-response", "口径一致"),
                ),
            )
        ),
    )
    before_review = derive_effective_metric_resolutions(
        decisions, (asset_proposed,), catalog
    )
    asset_confirmed = review_resolution(
        asset_proposed, ResolutionReviewStatus.CONFIRMED
    )
    after_review = derive_effective_metric_resolutions(
        decisions, (asset_confirmed,), catalog
    )

    profit_decision = next(
        item
        for item in decisions
        if item.subject.comparison_name == "主营业务利润"
    )
    profit_candidates = retrieve_candidates_for_decision(
        profit_decision,
        effective_result.structuring_result,
        decisions,
        catalog,
    )
    no_equivalent = run_semantic_judgment(
        profit_candidates,
        catalog,
        FixedJudge(
            JudgeOutput(
                SemanticStatus.NO_EQUIVALENT,
                reason="主营业务口径与营业利润口径不同",
                counter_evidence=(
                    Evidence("scope_mismatch", "r0-fixed-response", "业务范围不同"),
                ),
            )
        ),
    )
    proposal = build_ontology_change_proposal(no_equivalent, profit_candidates)
    assert proposal is not None
    proposal_snapshot = {
        "proposal_kind": proposal.proposal_kind.value,
        "source_metric_decision_id": proposal.source_metric_decision_id,
        "ontology_revision": proposal.ontology_revision,
        "raw_label": proposal.raw_label,
        "comparison_name": proposal.comparison_name,
        "suggested_name_cn": proposal.suggested_name_cn,
        "target_metric_id": proposal.target_metric_id,
        "suggested_alias": proposal.suggested_alias,
        "related_metric_ids": list(proposal.related_metric_ids),
        "reason": proposal.reason,
        "evidence_codes": [item.code for item in proposal.evidence],
        "review_status": proposal.review_status.value,
    }

    interest_candidate_snapshot = {
        "candidate_set_id": interest_candidates.candidate_set_id,
        "source_metric_decision_id": interest_candidates.source_metric_decision_id,
        "ontology_revision": interest_candidates.ontology_revision,
        "retrieval_version": interest_candidates.retrieval_version,
        "top_k": interest_candidates.top_k,
        "semantic_context": {
            "fingerprint": _canonical_hash(interest_candidates.semantic_context),
            "fields": sorted(interest_candidates.semantic_context),
            "nearest_group": interest_candidates.semantic_context["nearest_group"],
            "previous_subjects": interest_candidates.semantic_context[
                "previous_subjects"
            ],
            "following_subjects": interest_candidates.semantic_context[
                "following_subjects"
            ],
            "report_notes": interest_candidates.semantic_context["report_notes"],
            "same_table_candidate_conflicts": interest_candidates.semantic_context[
                "same_table_candidate_conflicts"
            ],
            "table_value_context": interest_candidates.semantic_context[
                "table_value_context"
            ],
            "source": interest_candidates.semantic_context["source"],
        },
        "candidates": [
            {
                "rank": item.rank,
                "current_metric_id": item.metric.current_metric_id,
                "scores": item.scores.to_dict(),
                "matched_fields": list(item.matched_fields),
                "evidence_codes": [evidence.code for evidence in item.evidence],
            }
            for item in interest_candidates.candidates
        ],
    }

    return {
        "retrieval_evaluation": evaluation.to_dict(),
        "interest_expense_candidate_set": interest_candidate_snapshot,
        "interest_expense_fixed_judgment": _semantic_resolution_snapshot(
            interest_resolution
        ),
        "effective_mapping": {
            "before_review": [
                _effective_item_snapshot(item) for item in before_review
            ],
            "after_review": [
                _effective_item_snapshot(item) for item in after_review
            ],
            "structuring_unchanged": (
                effective_result.structuring_result.to_dict()
                == structuring_before
            ),
        },
        "proposal": proposal_snapshot,
    }


def build_clean_refactor_business_snapshot() -> dict[str, Any]:
    catalog = load_ontology_catalog(DEFINITION, KNOWLEDGE)
    gold_catalog = load_metric_gold_catalog(DEFINITION, KNOWLEDGE)
    return {
        "format_version": "clean-refactor-r3-v1",
        "assets": {
            "definition_sha256": _file_hash(DEFINITION),
            "knowledge_sha256": _file_hash(KNOWLEDGE),
            "gold_sha256": _file_hash(GOLD),
            "ontology_revision": catalog.ontology_revision,
        },
        "phase1": {
            "csv": _phase1_dataset_snapshot(FIXTURES / "synthetic_sales_bom.csv"),
            "profit_xlsx": _phase1_dataset_snapshot(PROFIT_FIXTURE),
            "cost_xlsx": _phase1_dataset_snapshot(COST_FIXTURE),
        },
        "phase2": _build_phase2_snapshot(catalog),
        "phase25": _build_phase25_snapshot(gold_catalog),
    }
