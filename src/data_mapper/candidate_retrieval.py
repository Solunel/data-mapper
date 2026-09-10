"""Deterministic, explainable production Metric candidate retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Mapping

from .metric_resolution_contracts import (
    CandidateRouteScores,
    MetricCandidate,
    MetricCandidateSet,
    MetricDecision,
    MetricMatchStatus,
    MetricResolutionConfigurationError,
    OntologyCatalog,
    OntologyMetric,
)
from .observation_contracts import (
    Evidence,
    ObservationStructuringResult,
    RowRole,
)


RETRIEVAL_VERSION = "phase2.5-retrieval-v2"
_PUNCTUATION = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def build_semantic_context(
    decision: MetricDecision,
    structuring_result: ObservationStructuringResult,
    decisions: tuple[MetricDecision, ...],
    *,
    context_window: int = 2,
) -> Mapping[str, Any]:
    """Compose full table context without reading actual observation values."""

    if context_window < 0:
        raise MetricResolutionConfigurationError("context_window 不能小于 0")
    positions = {
        subject.subject_id: index
        for index, subject in enumerate(structuring_result.row_subjects)
    }
    position = positions.get(decision.subject.subject_id)
    if position is None:
        raise MetricResolutionConfigurationError(
            "MetricDecision 不属于 ObservationStructuringResult"
        )
    previous = structuring_result.row_subjects[
        max(0, position - context_window) : position
    ]
    following = structuring_result.row_subjects[
        position + 1 : position + 1 + context_window
    ]
    by_subject = {item.subject.subject_id: item for item in decisions}
    table_plan = structuring_result.table_mapping_plan
    return {
        "nearest_group": decision.subject.context.get("group_label"),
        "source_context": dict(decision.subject.context),
        "previous_subjects": [_subject_snapshot(item) for item in previous],
        "following_subjects": [_subject_snapshot(item) for item in following],
        "same_table_metric_subjects": [
            _same_table_metric_snapshot(item, by_subject.get(item.subject_id))
            for item in structuring_result.row_subjects
            if item.row_role is RowRole.METRIC
            and item.subject_id != decision.subject.subject_id
        ],
        "report_notes": [
            _subject_snapshot(item)
            for item in structuring_result.row_subjects
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
            for item in table_plan.value_fields
        ],
        "structuring_run_id": structuring_result.structuring_run_id,
        "curated_id": structuring_result.curated_id,
        "raw_dataset_id": structuring_result.raw_dataset_id,
        "raw_version_id": structuring_result.raw_version_id,
        "structuring_rule_version": (
            structuring_result.request.structuring_rule_version
        ),
    }


def retrieve_candidates_for_decision(
    decision: MetricDecision,
    structuring_result: ObservationStructuringResult,
    decisions: tuple[MetricDecision, ...],
    catalog: OntologyCatalog,
    *,
    top_k: int = 5,
    context_window: int = 2,
) -> MetricCandidateSet:
    """Build table context and retrieve candidates for one eligible decision."""

    if decision.subject.row_role is not RowRole.METRIC or decision.status not in {
        MetricMatchStatus.UNMATCHED,
        MetricMatchStatus.AMBIGUOUS,
    }:
        raise MetricResolutionConfigurationError(
            "MetricDecision 不满足 Semantic fallback eligibility"
        )
    context = build_semantic_context(
        decision,
        structuring_result,
        decisions,
        context_window=context_window,
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
    """Retrieve ranked candidates without producing an equivalence decision."""

    if ontology_revision != catalog.ontology_revision:
        raise MetricResolutionConfigurationError(
            "Candidate Retrieval 不允许跨 ontology revision"
        )
    if top_k <= 0:
        raise MetricResolutionConfigurationError("top_k 必须大于 0")
    subject_name = metric_subject.get("comparison_name")
    if not isinstance(subject_name, str) or not subject_name.strip():
        raise MetricResolutionConfigurationError(
            "metric_subject.comparison_name 不能为空"
        )

    context_names = _context_names(semantic_context)
    ranked = [
        _score_metric(subject_name, context_names, metric)
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


def _score_metric(
    subject: str,
    context_names: tuple[str, ...],
    metric: OntologyMetric,
) -> tuple[CandidateRouteScores, OntologyMetric, tuple[str, ...], tuple[Evidence, ...]]:
    queries = _retrieval_variants(subject)
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
    normalized = _normalize(value)
    variants = [normalized]
    for source, target in (
        ("费用", "支出"),
        ("支出", "费用"),
        ("总计", "合计"),
        ("合计", "总计"),
    ):
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
