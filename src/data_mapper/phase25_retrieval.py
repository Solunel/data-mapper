"""Phase 2.5 P1：小规模、确定性、可解释的 Metric Candidate Retrieval。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Mapping

from .mapping_contracts import (
    Evidence,
    MappingPlan,
    MetricDecision,
    MetricMatchStatus,
    OntologyCatalog,
    OntologyMetric,
    RowRole,
)
from .phase25 import (
    Phase25ReviewError,
    resolve_gold_case_semantic_context,
    validate_gold_review_payload,
)
from .phase25_semantic_contracts import (
    CandidateRouteScores,
    GoldRetrievalCaseResult,
    MetricCandidate,
    MetricCandidateSet,
    RetrievalEvaluationReport,
)


RETRIEVAL_VERSION = "phase2.5-retrieval-v2"
_PUNCTUATION = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def build_semantic_context(
    decision: MetricDecision,
    plan: MappingPlan,
    *,
    context_window: int = 2,
) -> Mapping[str, Any]:
    """从冻结 Phase 2 产物稳定组合语义上下文，不读取实际数值。"""

    if context_window < 0:
        raise Phase25ReviewError("context_window 不能小于 0")
    if decision.ontology_revision != plan.ontology_catalog.ontology_revision:
        raise Phase25ReviewError("MetricDecision 与 MappingPlan revision 不一致")
    positions = {
        subject.subject_id: index for index, subject in enumerate(plan.row_subjects)
    }
    position = positions.get(decision.subject.subject_id)
    if position is None:
        raise Phase25ReviewError("MetricDecision 不属于 MappingPlan")
    previous = plan.row_subjects[max(0, position - context_window) : position]
    following = plan.row_subjects[position + 1 : position + 1 + context_window]
    decisions = {item.subject.subject_id: item for item in plan.metric_decisions}
    return {
        "nearest_group": decision.subject.context.get("group_label"),
        "source_context": dict(decision.subject.context),
        "previous_subjects": [_subject_snapshot(item) for item in previous],
        "following_subjects": [_subject_snapshot(item) for item in following],
        "same_table_metric_subjects": [
            _same_table_metric_snapshot(item, decisions.get(item.subject_id))
            for item in plan.row_subjects
            if item.row_role is RowRole.METRIC
            and item.subject_id != decision.subject.subject_id
        ],
        "report_notes": [
            _subject_snapshot(item)
            for item in plan.row_subjects
            if item.row_role is RowRole.NOTE
        ],
        "table_value_context": [
            {
                "value_field": item.value_field,
                "business_scope": (
                    item.business_scope.to_dict() if item.business_scope else None
                ),
                "period_type": item.period_type,
                "period_basis": item.period_basis,
                "unit": item.unit.to_dict() if item.unit else None,
                "binding_complete": item.binding_complete,
            }
            for item in plan.table_mapping_plan.value_fields
        ],
        "mapping_run_id": plan.mapping_run_id,
        "curated_id": plan.curated_id,
        "raw_dataset_id": plan.raw_dataset_id,
        "raw_version_id": plan.raw_version_id,
        "mapping_rule_version": plan.request.mapping_rule_version,
    }


def retrieve_candidates_for_decision(
    decision: MetricDecision,
    plan: MappingPlan,
    catalog: OntologyCatalog,
    *,
    top_k: int = 5,
    context_window: int = 2,
) -> MetricCandidateSet:
    """对一个 eligible Phase 2 MetricDecision 构造上下文并召回候选。"""

    if decision.subject.row_role is not RowRole.METRIC or decision.status not in {
        MetricMatchStatus.UNMATCHED,
        MetricMatchStatus.AMBIGUOUS,
    }:
        raise Phase25ReviewError("MetricDecision 不满足 Phase 2.5 Eligibility")
    context = build_semantic_context(
        decision, plan, context_window=context_window
    )
    subject = decision.subject
    return retrieve_metric_candidates(
        source_metric_decision_id=decision.decision_id,
        ontology_revision=decision.ontology_revision,
        metric_subject={
            "subject_id": subject.subject_id,
            "raw_label": subject.raw_label,
            "comparison_name": subject.comparison_name,
            "row_role": subject.row_role.value,
            "extraction_evidence": [item.to_dict() for item in subject.evidence],
        },
        semantic_context=context,
        source={
            "source_file": subject.source_file,
            "sheet_name": subject.sheet_name,
            "source_row": subject.source_row,
            "source_column": subject.source_column,
        },
        catalog=catalog,
        top_k=top_k,
    )


def retrieve_metric_candidates(
    *,
    source_metric_decision_id: str,
    ontology_revision: str,
    metric_subject: Mapping[str, Any],
    semantic_context: Mapping[str, Any],
    catalog: OntologyCatalog,
    source: Mapping[str, Any] | None = None,
    top_k: int = 5,
) -> MetricCandidateSet:
    """召回候选并保留各路分数；不产生任何语义匹配结论。"""

    if ontology_revision != catalog.ontology_revision:
        raise Phase25ReviewError("Candidate Retrieval 不允许跨 ontology revision")
    if top_k <= 0:
        raise Phase25ReviewError("top_k 必须大于 0")
    comparison_name = metric_subject.get("comparison_name")
    if not isinstance(comparison_name, str) or not comparison_name.strip():
        raise Phase25ReviewError("metric_subject.comparison_name 不能为空")

    context_names = _context_names(semantic_context)
    ranked = [
        _score_metric(comparison_name, context_names, metric)
        for metric in catalog.metrics
    ]
    ranked.sort(
        key=lambda item: (
            -item[0].total,
            -item[0].name_sequence,
            -item[0].name_ngram,
            item[1].current_metric_id,
        )
    )
    candidates = tuple(
        MetricCandidate(
            rank=index,
            metric=metric,
            scores=scores,
            matched_fields=matched_fields,
            evidence=evidence,
        )
        for index, (scores, metric, matched_fields, evidence) in enumerate(
            ranked[: min(top_k, len(ranked))], 1
        )
    )
    candidate_ids = {
        candidate.metric.current_metric_id for candidate in candidates
    }
    same_table_candidate_conflicts = [
        dict(item)
        for item in semantic_context.get("same_table_metric_subjects") or ()
        if isinstance(item, Mapping)
        and item.get("phase2_status") == MetricMatchStatus.MATCHED.value
        and item.get("current_metric_id") in candidate_ids
    ]
    stable_context = {
        "metric_subject": dict(metric_subject),
        "source": dict(source or {}),
        "nearest_group": semantic_context.get("nearest_group"),
        "previous_subjects": list(semantic_context.get("previous_subjects") or ()),
        "following_subjects": list(semantic_context.get("following_subjects") or ()),
        "same_table_candidate_conflicts": same_table_candidate_conflicts,
        "report_notes": list(semantic_context.get("report_notes") or ()),
        "table_value_context": list(
            semantic_context.get("table_value_context") or ()
        ),
    }
    candidate_set_id = _stable_id(
        "candidate-set",
        {
            "source_metric_decision_id": source_metric_decision_id,
            "ontology_revision": ontology_revision,
            "retrieval_version": RETRIEVAL_VERSION,
            "top_k": top_k,
            "semantic_context": stable_context,
            "candidates": [item.to_dict() for item in candidates],
        },
    )
    return MetricCandidateSet(
        candidate_set_id=candidate_set_id,
        source_metric_decision_id=source_metric_decision_id,
        ontology_revision=ontology_revision,
        retrieval_version=RETRIEVAL_VERSION,
        top_k=top_k,
        semantic_context=stable_context,
        candidates=candidates,
    )


def evaluate_retrieval_on_gold(
    payload: Mapping[str, Any],
    catalog: OntologyCatalog,
    *,
    top_k: int = 5,
) -> RetrievalEvaluationReport:
    """只读评估已确认且纳入 Gold Set 的案例。"""

    if payload.get("ontology_revision") != catalog.ontology_revision:
        raise Phase25ReviewError("Gold Truth 与 OntologyCatalog revision 不一致")
    validation = validate_gold_review_payload(payload, catalog)
    if not validation.ready_for_p1:
        codes = ", ".join(item.code for item in validation.issues)
        raise Phase25ReviewError(f"Gold Truth 未通过 P1 门禁：{codes}")
    if top_k < 5:
        raise Phase25ReviewError("Recall@3/Recall@5 评测要求 top_k 至少为 5")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise Phase25ReviewError("Gold Truth cases 必须是数组")

    results: list[GoldRetrievalCaseResult] = []
    replay_stable = True
    map_ranks: list[int | None] = []
    for case in cases:
        review = case.get("human_review") or {}
        if not (
            review.get("review_status") == "CONFIRMED"
            and review.get("include_in_gold_set") is True
        ):
            continue
        candidate_set = retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision=case["ontology_revision"],
            metric_subject=case["metric_subject"],
            semantic_context=resolve_gold_case_semantic_context(payload, case),
            catalog=catalog,
            source=case.get("source"),
            top_k=top_k,
        )
        replay = retrieve_metric_candidates(
            source_metric_decision_id=case["source_metric_decision_id"],
            ontology_revision=case["ontology_revision"],
            metric_subject=case["metric_subject"],
            semantic_context=resolve_gold_case_semantic_context(payload, case),
            catalog=catalog,
            source=case.get("source"),
            top_k=top_k,
        )
        replay_stable = replay_stable and candidate_set == replay
        ranks = {
            candidate.metric.current_metric_id: candidate.rank
            for candidate in candidate_set.candidates
        }
        expected_metric_id = review.get("expected_metric_id")
        expected_rank = ranks.get(expected_metric_id) if expected_metric_id else None
        if review.get("expected_semantic_status") == "MAP_EXISTING":
            map_ranks.append(expected_rank)
        results.append(
            GoldRetrievalCaseResult(
                case_id=case["case_id"],
                report_family=case["report_family"],
                expected_semantic_status=review["expected_semantic_status"],
                expected_metric_id=expected_metric_id,
                expected_metric_rank=expected_rank,
                hard_negative_ranks={
                    metric_id: ranks.get(metric_id)
                    for metric_id in review.get("hard_negative_metric_ids") or ()
                },
            )
        )

    if not map_ranks:
        raise Phase25ReviewError("Gold Truth 没有 MAP_EXISTING 案例，无法计算 Recall@K")
    recall_at_3 = sum(rank is not None and rank <= 3 for rank in map_ranks) / len(
        map_ranks
    )
    recall_at_5 = sum(rank is not None and rank <= 5 for rank in map_ranks) / len(
        map_ranks
    )
    return RetrievalEvaluationReport(
        ontology_revision=catalog.ontology_revision,
        retrieval_version=RETRIEVAL_VERSION,
        top_k=top_k,
        included_case_count=len(results),
        map_existing_case_count=len(map_ranks),
        recall_at_3=recall_at_3,
        recall_at_5=recall_at_5,
        stable_replay=replay_stable,
        case_results=tuple(results),
    )


def _score_metric(
    subject: str,
    context_names: tuple[str, ...],
    metric: OntologyMetric,
) -> tuple[CandidateRouteScores, OntologyMetric, tuple[str, ...], tuple[Evidence, ...]]:
    queries = _retrieval_variants(subject)
    query = queries[0]
    name = _normalize(metric.name_cn)
    name_sequence = max(_sequence(item, name) for item in queries)
    name_ngram = max(_dice(_ngrams(item), _ngrams(name)) for item in queries)

    alias_pairs = [
        (
            max(_sequence(item, _normalize(alias)) for item in queries),
            max(
                _dice(_ngrams(item), _ngrams(_normalize(alias)))
                for item in queries
            ),
            alias,
        )
        for alias in metric.aliases
    ]
    best_alias = max(alias_pairs, default=(0.0, 0.0, ""))
    definition_overlap = max(
        (
            _dice(_ngrams(item), _ngrams(_normalize(metric.definition_cn)))
            for item in queries
        ),
        default=0.0,
    )
    business_label_overlap = max(
        (
            _dice(_ngrams(item), _ngrams(_normalize(label)))
            for label in metric.business_labels
            for item in queries
        ),
        default=0.0,
    )
    context_similarity = max(
        (_sequence(_normalize(item), name) for item in context_names),
        default=0.0,
    )
    total = round(
        0.48 * name_sequence
        + 0.27 * name_ngram
        + 0.10 * max(best_alias[0], best_alias[1])
        + 0.08 * definition_overlap
        + 0.03 * business_label_overlap
        + 0.04 * context_similarity,
        8,
    )
    scores = CandidateRouteScores(
        name_sequence=round(name_sequence, 8),
        name_ngram=round(name_ngram, 8),
        alias_sequence=round(best_alias[0], 8),
        alias_ngram=round(best_alias[1], 8),
        definition_overlap=round(definition_overlap, 8),
        business_label_overlap=round(business_label_overlap, 8),
        context_similarity=round(context_similarity, 8),
        total=total,
    )
    matched_fields = ["name_cn"]
    if len(queries) > 1:
        matched_fields.append("retrieval_name_variant")
    if best_alias[2]:
        matched_fields.append(f"alias:{best_alias[2]}")
    if metric.definition_cn:
        matched_fields.append("definition_cn")
    if metric.business_labels:
        matched_fields.append("business_labels")
    evidence = (
        Evidence(
            code="candidate_route_scores",
            source=RETRIEVAL_VERSION,
            message="候选只按可解释的名称、alias、定义、业务标签和邻近上下文信号排序",
            details={**scores.to_dict(), "query_variants": list(queries)},
        ),
    )
    return scores, metric, tuple(matched_fields), evidence


def _context_names(context: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    group = context.get("nearest_group")
    if isinstance(group, str) and group.strip():
        values.append(group)
    for field in ("previous_subjects", "following_subjects"):
        for item in context.get(field) or ():
            if isinstance(item, Mapping):
                name = item.get("comparison_name")
                if isinstance(name, str) and name.strip():
                    values.append(name)
    return tuple(values)


def _subject_snapshot(subject: Any) -> dict[str, Any]:
    return {
        "raw_label": subject.raw_label,
        "comparison_name": subject.comparison_name,
        "row_role": subject.row_role.value,
        "source_row": subject.source_row,
    }


def _same_table_metric_snapshot(
    subject: Any,
    decision: MetricDecision | None,
) -> dict[str, Any]:
    selected_metric = decision.selected_metric if decision is not None else None
    return {
        "subject_id": subject.subject_id,
        "raw_label": subject.raw_label,
        "comparison_name": subject.comparison_name,
        "row_role": subject.row_role.value,
        "source_row": subject.source_row,
        "phase2_status": decision.status.value if decision is not None else None,
        "current_metric_id": (
            selected_metric.current_metric_id if selected_metric is not None else None
        ),
    }


def _retrieval_variants(value: str) -> tuple[str, ...]:
    """只扩展少量可解释的财务词尾；扩展仅影响召回，不产生等价结论。"""

    normalized = _normalize(value)
    variants = [normalized]
    suffix_pairs = (
        ("费用", "支出"),
        ("支出", "费用"),
        ("总计", "合计"),
        ("合计", "总计"),
    )
    for source, target in suffix_pairs:
        if normalized.endswith(source):
            variants.append(normalized[: -len(source)] + target)
    return tuple(dict.fromkeys(variants))


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _PUNCTUATION.sub("", normalized)


def _sequence(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _ngrams(value: str, size: int = 2) -> set[str]:
    if not value:
        return set()
    if len(value) < size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{namespace}:" + hashlib.sha256(encoded).hexdigest()
