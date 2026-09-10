"""Pure deterministic Metric Resolution over structured row subjects."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .metric_resolution_contracts import (
    MetricDecision,
    MetricMatchStatus,
    MetricResolutionRequest,
    OntologyCatalog,
    OntologyMetric,
)
from .observation_contracts import Evidence, MetricSubject, RowRole
from .observation_structuring import comparison_key, comparison_name


def resolution_request_errors(
    subjects: tuple[MetricSubject, ...],
    request: MetricResolutionRequest,
    catalog: OntologyCatalog,
) -> tuple[str, ...]:
    """Validate explicit resolution inputs without reading source table storage."""

    errors: list[str] = []
    if not request.deterministic_rule_version.strip():
        errors.append("deterministic_rule_version 不能为空")
    if request.retrieval_top_k <= 0:
        errors.append("retrieval_top_k 必须大于 0")
    if request.semantic_context_window < 0:
        errors.append("semantic_context_window 不能小于 0")
    if request.semantic_max_judgments is not None and request.semantic_max_judgments < 0:
        errors.append("semantic_max_judgments 不能小于 0")
    rows = {subject.source_row for subject in subjects}
    missing_override_rows = sorted(set(request.metric_overrides).difference(rows))
    if missing_override_rows:
        errors.append(
            "metric_overrides 未指向有效指标主体："
            + ", ".join(map(str, missing_override_rows))
        )
    missing_gap_rows = sorted(set(request.ontology_gap_confirmations).difference(rows))
    if missing_gap_rows:
        errors.append(
            "ontology_gap_confirmations 未指向有效指标主体："
            + ", ".join(map(str, missing_gap_rows))
        )
    conflicts = sorted(
        set(request.metric_overrides).intersection(request.ontology_gap_confirmations)
    )
    if conflicts:
        errors.append("同一行不能同时 override 与确认本体缺口：" + ", ".join(map(str, conflicts)))
    for row, current_metric_id in sorted(request.metric_overrides.items()):
        if catalog.metric_by_id(current_metric_id) is None:
            errors.append(
                f"metric_overrides[{row}] 不属于当前 ontology_revision：{current_metric_id}"
            )
    return tuple(errors)


def resolve_metrics_deterministically(
    subjects: tuple[MetricSubject, ...],
    request: MetricResolutionRequest,
    catalog: OntologyCatalog,
) -> tuple[MetricDecision, ...]:
    """Resolve row subjects with the frozen Phase 2 deterministic rules."""

    errors = resolution_request_errors(subjects, request, catalog)
    if errors:
        raise ValueError("; ".join(errors))
    return tuple(
        _match_metric(subject, request, catalog)
        for subject in subjects
        if subject.row_role not in {RowRole.GROUP, RowRole.NOTE}
    )


def _match_metric(
    subject: MetricSubject,
    request: MetricResolutionRequest,
    catalog: OntologyCatalog,
) -> MetricDecision:
    subject_evidence = subject.evidence
    override = request.metric_overrides.get(subject.source_row)
    if override:
        metric = catalog.metric_by_id(override)
        assert metric is not None
        return _decision(
            subject,
            MetricMatchStatus.MATCHED,
            catalog,
            metric,
            (metric,),
            subject_evidence
            + (
                Evidence(
                    code="confirmed_metric_override",
                    source="MappingRequest",
                    message="使用当前 ontology_revision 内已确认的 Metric override",
                    details={"current_metric_id": override},
                ),
            ),
        )

    if subject.source_row in request.ontology_gap_confirmations:
        return _decision(
            subject,
            MetricMatchStatus.ONTOLOGY_GAP,
            catalog,
            None,
            (),
            subject_evidence
            + (
                Evidence(
                    code="confirmed_ontology_gap",
                    source="MappingRequest",
                    message="人工确认当前业务指标无法由本体表达；未虚构 Metric ID",
                ),
            ),
            ontology_gap_candidate=True,
        )

    exact_id = [m for m in catalog.metrics if m.current_metric_id == subject.comparison_name]
    exact_name = [m for m in catalog.metrics if m.name_cn == subject.comparison_name]
    exact_alias = [m for m in catalog.metrics if subject.comparison_name in m.aliases]
    if exact_id:
        matches, code = exact_id, "exact_current_metric_id"
    elif exact_name:
        matches, code = exact_name, "exact_formal_name"
    elif exact_alias:
        matches, code = exact_alias, "exact_alias"
    else:
        matches = [
            metric
            for metric in catalog.metrics
            if comparison_key(metric.name_cn) == subject.comparison_key
            or any(comparison_key(alias) == subject.comparison_key for alias in metric.aliases)
        ]
        code = "restricted_comparison_key"

    matches = sorted(
        {metric.current_metric_id: metric for metric in matches}.values(),
        key=lambda metric: metric.current_metric_id,
    )
    accepted: list[OntologyMetric] = []
    rejected: list[OntologyMetric] = []
    for metric in matches:
        if _value_semantics_conflict(subject.comparison_name, metric):
            rejected.append(metric)
        else:
            accepted.append(metric)

    evidence = list(subject_evidence)
    if matches:
        evidence.append(
            Evidence(
                code=code,
                source="OntologyCatalog",
                message=f"确定性索引得到 {len(matches)} 个 Metric 候选",
                details={
                    "current_metric_ids": [m.current_metric_id for m in matches],
                    "ontology_revision": catalog.ontology_revision,
                },
            )
        )
    if rejected:
        evidence.append(
            Evidence(
                code="value_semantics_conflict",
                source="deterministic_rule",
                message="明显的比率/普通数值语义冲突阻止自动命中",
                details={
                    "rejected_current_metric_ids": [m.current_metric_id for m in rejected]
                },
            )
        )
    if len(accepted) == 1:
        return _decision(
            subject,
            MetricMatchStatus.MATCHED,
            catalog,
            accepted[0],
            tuple(accepted),
            tuple(evidence),
        )
    if len(accepted) > 1:
        evidence.append(
            Evidence(
                code="multiple_metric_candidates",
                source="OntologyCatalog",
                message="多个候选无法由当前确定性证据消歧",
            )
        )
        return _decision(
            subject,
            MetricMatchStatus.AMBIGUOUS,
            catalog,
            None,
            tuple(accepted),
            tuple(evidence),
        )
    evidence.append(
        Evidence(
            code="no_reliable_metric_candidate",
            source="deterministic_rule",
            message="没有可靠已有 Metric；自动未匹配不升级为本体缺口",
        )
    )
    return _decision(
        subject,
        MetricMatchStatus.UNMATCHED,
        catalog,
        None,
        (),
        tuple(evidence),
    )


def _decision(
    subject: MetricSubject,
    status: MetricMatchStatus,
    catalog: OntologyCatalog,
    selected: OntologyMetric | None,
    candidates: tuple[OntologyMetric, ...],
    evidence: tuple[Evidence, ...],
    ontology_gap_candidate: bool = False,
) -> MetricDecision:
    return MetricDecision(
        decision_id=_stable_id(
            "metric-decision",
            {
                "subject_id": subject.subject_id,
                "ontology_revision": catalog.ontology_revision,
                "status": status.value,
                "selected": selected.current_metric_id if selected else None,
                "candidates": [metric.current_metric_id for metric in candidates],
                "evidence": [item.to_dict() for item in evidence],
            },
        ),
        subject=subject,
        status=status,
        ontology_revision=catalog.ontology_revision,
        selected_metric=selected,
        candidates=candidates,
        evidence=evidence,
        ontology_gap_candidate=ontology_gap_candidate,
    )


def _value_semantics_conflict(raw_name: str, metric: OntologyMetric) -> bool:
    name = comparison_name(raw_name)
    source_is_ratio = name.endswith(("率", "比例", "比率")) or "%" in name
    return source_is_ratio and metric.value_semantics == "NUMBER"


def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return f"{namespace}:" + hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"无法稳定序列化 {type(value).__name__}")
