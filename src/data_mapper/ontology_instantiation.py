"""Pure ResolvedObservation to ActualObservation instantiation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from typing import Iterable
import unicodedata

from .metric_resolution_contracts import (
    DataMappingResult,
    MetricCandidateSet,
    MetricDecision,
    OntologyCatalog,
    ResolvedObservation,
)
from .observation_contracts import ActualObservation, JsonContract, Period


class OntologyInstantiationError(ValueError):
    """The supplied mapping result cannot be instantiated safely."""


@dataclass(frozen=True)
class InstantiationIssue(JsonContract):
    code: str
    field: str | None
    message: str


@dataclass(frozen=True)
class BlockedObservation(JsonContract):
    resolved_observation: ResolvedObservation
    reasons: tuple[InstantiationIssue, ...]


@dataclass(frozen=True)
class UnresolvedMetricItem(JsonContract):
    metric_subject_id: str
    metric_name: str
    comparison_name: str
    effective_status: str
    source_metric_decision_id: str
    source_file: str
    sheet_name: str
    source_row: int
    organization_value: str | None
    organization_id: str | None
    observation_count: int
    candidate_metric_ids: tuple[str, ...] = ()
    source_resolution_id: str | None = None


@dataclass(frozen=True)
class OntologyInstantiationResult(JsonContract):
    ontology_revision: str
    observation_schema_fingerprint: str
    actual_observations: tuple[ActualObservation, ...]
    unresolved_metrics: tuple[UnresolvedMetricItem, ...]
    blocked_observations: tuple[BlockedObservation, ...]
    deduplicated_observation_count: int


@dataclass(frozen=True)
class _InstantiationCandidate:
    resolved: ResolvedObservation
    actual: ActualObservation


_PERIOD_KEY_PATTERNS = {
    "MONTH": re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$"),
    "QUARTER": re.compile(r"^\d{4}-Q[1-4]$"),
    "YEAR": re.compile(r"^\d{4}$"),
}


def instantiate_observations(
    mapping_results: Sequence[DataMappingResult],
    catalog: OntologyCatalog,
    *,
    status: str,
) -> OntologyInstantiationResult:
    """Validate and project one immutable mapping-result batch without persistence."""

    if not mapping_results:
        raise OntologyInstantiationError("实例化批次至少需要一个 DataMappingResult")
    for mapping_result in mapping_results:
        if not isinstance(mapping_result, DataMappingResult):
            raise OntologyInstantiationError(
                "实例化批次只能包含 DataMappingResult"
            )
        _validate_run(mapping_result, catalog, status)
    _validate_batch_subject_consistency(mapping_results)
    unresolved_metrics = _build_unresolved_metrics(mapping_results)
    blocked: list[BlockedObservation] = []
    candidates_by_id: dict[str, list[_InstantiationCandidate]] = defaultdict(list)

    for mapping_result in mapping_results:
        for resolved in mapping_result.resolved_observations:
            reasons = _gate_reasons(resolved, catalog)
            if reasons:
                blocked.append(BlockedObservation(resolved, reasons))
                continue
            actual = _build_actual_observation(resolved, status)
            candidates_by_id[actual.id].append(
                _InstantiationCandidate(resolved, actual)
            )

    actual_observations: list[ActualObservation] = []
    deduplicated_count = 0
    for identifier in sorted(candidates_by_id):
        group = candidates_by_id[identifier]
        payloads = {(item.actual.actual_value, item.actual.unit) for item in group}
        if len(payloads) > 1:
            issue = InstantiationIssue(
                code="CONFLICTING_BUSINESS_IDENTITY",
                field=None,
                message=f"同一业务身份存在 {len(group)} 条数值或单位冲突的观测",
            )
            blocked.extend(
                BlockedObservation(item.resolved, (issue,)) for item in group
            )
            continue

        representative = min(group, key=_representative_key)
        actual_observations.append(representative.actual)
        deduplicated_count += len(group) - 1

    return OntologyInstantiationResult(
        ontology_revision=catalog.ontology_revision,
        observation_schema_fingerprint=catalog.observation_schema.fingerprint,
        actual_observations=tuple(actual_observations),
        unresolved_metrics=unresolved_metrics,
        blocked_observations=tuple(blocked),
        deduplicated_observation_count=deduplicated_count,
    )


def actual_observation_id(
    *,
    organization_id: str,
    metric_id: str,
    business_scope: str,
    period: Period,
) -> str:
    """Return the stable ID for the frozen business identity."""

    payload = {
        "organization_id": organization_id,
        "metric_id": metric_id,
        "business_scope": normalize_identity_text(business_scope),
        "period": {
            "period_type": period.period_type,
            "period_key": period.period_key,
            "period_basis": period.period_basis,
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "actual-observation:sha256:" + hashlib.sha256(canonical).hexdigest()


def normalize_identity_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _validate_run(
    mapping_result: DataMappingResult,
    catalog: OntologyCatalog,
    status: str,
) -> None:
    structuring = mapping_result.structuring_result
    resolution = mapping_result.metric_resolution_result
    if resolution.ontology_revision != catalog.ontology_revision:
        raise OntologyInstantiationError(
            "DataMappingResult 与 OntologyCatalog revision 不一致"
        )
    if structuring.observation_schema_fingerprint != catalog.observation_schema.fingerprint:
        raise OntologyInstantiationError(
            "DataMappingResult 与 OntologyCatalog ObservationSchema fingerprint 不一致"
        )
    if not isinstance(status, str) or status not in catalog.observation_schema.status_values:
        raise OntologyInstantiationError("status 不属于当前 Definition Status 值域")
    if catalog.unit_storage_semantics.get("PERCENT") != "0_to_1":
        raise OntologyInstantiationError(
            "OntologyCatalog 缺少受支持的 PERCENT storage semantics"
        )
    if resolution.structuring_run_id != structuring.structuring_run_id:
        raise OntologyInstantiationError("Mapping 结果的 structuring_run_id 不一致")

    decisions = _unique_by(
        resolution.deterministic_decisions,
        lambda item: item.decision_id,
        "MetricDecision.decision_id",
    )
    subjects = _unique_by(
        structuring.row_subjects,
        lambda item: item.subject_id,
        "MetricSubject.subject_id",
    )
    _unique_by(
        resolution.candidate_sets,
        lambda item: item.source_metric_decision_id,
        "MetricCandidateSet.source_metric_decision_id",
    )
    _unique_by(
        resolution.semantic_resolutions,
        lambda item: item.source_metric_decision_id,
        "SemanticResolution.source_metric_decision_id",
    )
    effective = _unique_by(
        mapping_result.effective_metric_resolutions,
        lambda item: item.metric_subject_id,
        "EffectiveMetricResolution.metric_subject_id",
    )
    if set(decisions) != {
        item.source_metric_decision_id for item in effective.values()
    }:
        raise OntologyInstantiationError(
            "MetricDecision 与 EffectiveMetricResolution 关联不完整"
        )
    for item in effective.values():
        decision = decisions[item.source_metric_decision_id]
        if subjects.get(decision.subject.subject_id) != decision.subject:
            raise OntologyInstantiationError(
                "MetricDecision 未引用当前 Structuring 的 MetricSubject"
            )
        if item.metric_subject_id != decision.subject.subject_id:
            raise OntologyInstantiationError(
                "EffectiveMetricResolution 与 MetricSubject 关联不一致"
            )
        if item.ontology_revision != catalog.ontology_revision:
            raise OntologyInstantiationError(
                "EffectiveMetricResolution 与 OntologyCatalog revision 不一致"
            )

    drafts = _unique_by(
        structuring.observation_drafts,
        lambda item: item.observation_draft_id,
        "ObservationDraft.observation_draft_id",
    )
    resolved = _unique_by(
        mapping_result.resolved_observations,
        lambda item: item.observation.observation_draft_id,
        "ResolvedObservation.observation_draft_id",
    )
    if set(drafts) != set(resolved):
        raise OntologyInstantiationError(
            "ObservationDraft 与 ResolvedObservation 关联不完整"
        )
    for draft_id, item in resolved.items():
        if item.observation != drafts[draft_id]:
            raise OntologyInstantiationError(
                "ResolvedObservation 未保留原 ObservationDraft"
            )
        expected = effective.get(item.observation.metric_subject_id)
        if expected is None or item.metric_resolution != expected:
            raise OntologyInstantiationError(
                "ResolvedObservation 与 EffectiveMetricResolution 关联不一致"
            )
        if item.metric_id != item.metric_resolution.current_metric_id:
            raise OntologyInstantiationError(
                "ResolvedObservation.metric_id 与有效 Metric 不一致"
            )


def _gate_reasons(
    resolved: ResolvedObservation,
    catalog: OntologyCatalog,
) -> tuple[InstantiationIssue, ...]:
    draft = resolved.observation
    reasons: list[InstantiationIssue] = []
    metric_id = resolved.metric_id
    if not isinstance(metric_id, str) or not metric_id:
        reasons.append(_issue("METRIC_ID_MISSING", "metric_id", "Metric ID 缺失"))
    if resolved.metric_resolution.effective_status != "MAPPED":
        reasons.append(
            _issue(
                "METRIC_STATUS_NOT_MAPPED",
                "metric_id",
                "有效 Metric 状态不是 MAPPED",
            )
        )
    if isinstance(metric_id, str) and metric_id and catalog.metric_by_id(metric_id) is None:
        reasons.append(
            _issue(
                "METRIC_REFERENCE_NOT_FOUND",
                "metric_id",
                "Metric 引用不属于当前 Catalog",
            )
        )

    organization_id = resolved.organization_id
    if not isinstance(organization_id, str) or not organization_id:
        reasons.append(
            _issue("ORGANIZATION_ID_MISSING", "organization_id", "Organization ID 缺失")
        )
    elif organization_id not in catalog.organization_ids:
        reasons.append(
            _issue(
                "ORGANIZATION_REFERENCE_NOT_FOUND",
                "organization_id",
                "Organization 引用不属于当前 Catalog",
            )
        )

    if not isinstance(draft.business_scope, str) or not normalize_identity_text(
        draft.business_scope
    ):
        reasons.append(
            _issue("BUSINESS_SCOPE_INVALID", "business_scope", "business_scope 必须非空")
        )
    if draft.period_type not in catalog.period_type_values:
        reasons.append(_issue("PERIOD_TYPE_INVALID", "period.period_type", "period_type 无效"))
    pattern = _PERIOD_KEY_PATTERNS.get(draft.period_type)
    if not isinstance(draft.period_key, str) or pattern is None or not pattern.fullmatch(
        draft.period_key
    ):
        reasons.append(_issue("PERIOD_KEY_INVALID", "period.period_key", "period_key 格式无效"))
    if draft.period_basis not in catalog.period_basis_values:
        reasons.append(
            _issue("PERIOD_BASIS_INVALID", "period.period_basis", "period_basis 无效")
        )

    valid_number = isinstance(draft.actual_value, (int, float)) and not isinstance(
        draft.actual_value, bool
    )
    if valid_number and isinstance(draft.actual_value, float):
        valid_number = math.isfinite(draft.actual_value)
    if not valid_number:
        reasons.append(_issue("ACTUAL_VALUE_INVALID", "actual_value", "actual_value 必须是有限数值"))

    unit = draft.unit_normalized
    if not isinstance(unit, str) or not unit:
        reasons.append(_issue("UNIT_MISSING", "unit", "规范单位缺失"))
    elif unit not in catalog.unit_values:
        reasons.append(_issue("UNIT_INVALID", "unit", "单位不属于当前 Definition Unit 值域"))
    elif unit == "PERCENT" and valid_number and not 0 <= draft.actual_value <= 1:
        reasons.append(
            _issue(
                "PERCENT_VALUE_OUT_OF_RANGE",
                "actual_value",
                "PERCENT 数值必须遵守 0_to_1 存储语义",
            )
        )

    if not isinstance(draft.sheet_name, str) or not normalize_identity_text(draft.sheet_name):
        reasons.append(_issue("SOURCE_INVALID", "source", "source 表名必须非空"))
    return tuple(reasons)


def _build_actual_observation(
    resolved: ResolvedObservation,
    status: str,
) -> ActualObservation:
    draft = resolved.observation
    period = Period(draft.period_type, draft.period_key, draft.period_basis)
    business_scope = normalize_identity_text(draft.business_scope)
    return ActualObservation(
        id=actual_observation_id(
            organization_id=resolved.organization_id,
            metric_id=resolved.metric_id,
            business_scope=business_scope,
            period=period,
        ),
        organization_id=resolved.organization_id,
        metric_id=resolved.metric_id,
        business_scope=business_scope,
        source=normalize_identity_text(draft.sheet_name),
        period=period,
        actual_value=draft.actual_value,
        unit=draft.unit_normalized,
        status=status,
    )


def _build_unresolved_metrics(
    mapping_results: Sequence[DataMappingResult],
) -> tuple[UnresolvedMetricItem, ...]:
    by_subject: dict[str, UnresolvedMetricItem] = {}
    for mapping_result in mapping_results:
        for item in _unresolved_metrics_for_result(mapping_result):
            existing = by_subject.get(item.metric_subject_id)
            if existing is None:
                by_subject[item.metric_subject_id] = item
                continue
            if _unresolved_signature(existing) != _unresolved_signature(item):
                raise OntologyInstantiationError(
                    "同一 MetricSubject 在批次中存在不一致的未匹配结果"
                )
            by_subject[item.metric_subject_id] = replace(
                existing,
                observation_count=(
                    existing.observation_count + item.observation_count
                ),
            )
    return tuple(
        sorted(
            by_subject.values(),
            key=lambda item: (
                item.source_file,
                item.sheet_name,
                item.source_row,
                item.metric_subject_id,
            ),
        )
    )


def _unresolved_metrics_for_result(
    mapping_result: DataMappingResult,
) -> tuple[UnresolvedMetricItem, ...]:
    structuring = mapping_result.structuring_result
    resolution = mapping_result.metric_resolution_result
    subjects = {item.subject_id: item for item in structuring.row_subjects}
    decisions = {item.decision_id: item for item in resolution.deterministic_decisions}
    candidate_sets = {
        item.source_metric_decision_id: item for item in resolution.candidate_sets
    }
    resolved_by_subject: dict[str, list[ResolvedObservation]] = defaultdict(list)
    for item in mapping_result.resolved_observations:
        resolved_by_subject[item.observation.metric_subject_id].append(item)

    items: list[UnresolvedMetricItem] = []
    for effective in mapping_result.effective_metric_resolutions:
        if effective.current_metric_id is not None:
            continue
        decision = decisions[effective.source_metric_decision_id]
        subject = subjects[effective.metric_subject_id]
        related = resolved_by_subject.get(effective.metric_subject_id, [])
        candidate_ids = _candidate_metric_ids(
            candidate_sets.get(effective.source_metric_decision_id),
            decision,
        )
        items.append(
            UnresolvedMetricItem(
                metric_subject_id=subject.subject_id,
                metric_name=subject.raw_label,
                comparison_name=subject.comparison_name,
                effective_status=effective.effective_status,
                source_metric_decision_id=effective.source_metric_decision_id,
                source_file=subject.source_file,
                sheet_name=subject.sheet_name,
                source_row=subject.source_row,
                organization_value=_single_or_none(
                    item.observation.organization_value for item in related
                ),
                organization_id=_single_or_none(
                    item.organization_id for item in related
                ),
                observation_count=len(related),
                candidate_metric_ids=candidate_ids,
                source_resolution_id=effective.source_resolution_id,
            )
        )
    return tuple(items)


def _validate_batch_subject_consistency(
    mapping_results: Sequence[DataMappingResult],
) -> None:
    effective_by_subject = {}
    for mapping_result in mapping_results:
        for item in mapping_result.effective_metric_resolutions:
            existing = effective_by_subject.get(item.metric_subject_id)
            if existing is not None and existing != item:
                raise OntologyInstantiationError(
                    "同一 MetricSubject 在批次中存在不一致的有效解析"
                )
            effective_by_subject[item.metric_subject_id] = item


def _unresolved_signature(item: UnresolvedMetricItem) -> tuple:
    return (
        item.metric_subject_id,
        item.metric_name,
        item.comparison_name,
        item.effective_status,
        item.source_metric_decision_id,
        item.source_file,
        item.sheet_name,
        item.source_row,
        item.organization_value,
        item.organization_id,
        item.candidate_metric_ids,
        item.source_resolution_id,
    )


def _candidate_metric_ids(
    candidate_set: MetricCandidateSet | None,
    decision: MetricDecision,
) -> tuple[str, ...]:
    if candidate_set is not None:
        return tuple(item.metric.current_metric_id for item in candidate_set.candidates)
    return tuple(item.current_metric_id for item in decision.candidates)


def _representative_key(item: _InstantiationCandidate) -> tuple[str, str, int, str]:
    draft = item.resolved.observation
    return (
        normalize_identity_text(draft.sheet_name),
        draft.source_file,
        draft.source_row,
        draft.observation_draft_id,
    )


def _single_or_none(values: Iterable[str | None]) -> str | None:
    unique = {value for value in values if value is not None}
    return next(iter(unique)) if len(unique) == 1 else None


def _unique_by(items, key, label):
    result = {}
    for item in items:
        identifier = key(item)
        if identifier in result:
            raise OntologyInstantiationError(f"{label} 重复：{identifier}")
        result[identifier] = item
    return result


def _issue(code: str, field: str | None, message: str) -> InstantiationIssue:
    return InstantiationIssue(code=code, field=field, message=message)
