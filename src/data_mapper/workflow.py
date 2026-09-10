"""Formal CuratedDataset to ResolvedObservation workflow."""

from __future__ import annotations

from dataclasses import replace

from .contracts import CuratedDataset
from .metric_resolution import resolve_metrics
from .metric_resolution_contracts import (
    DataMappingResult,
    EffectiveMetricResolution,
    ExecutionStatus,
    MetricResolutionReplayError,
    MetricResolutionRequest,
    OntologyCatalog,
    ResolutionReviewStatus,
    ResolvedObservation,
    SemanticResolution,
    SemanticStatus,
)
from .observation_contracts import (
    ObservationDraft,
    ObservationStructuringRequest,
)
from .observation_structuring import structure_observations
from .semantic_resolution import SemanticJudge, derive_effective_metric_resolutions


def map_curated_observations(
    curated: CuratedDataset,
    structuring_request: ObservationStructuringRequest,
    resolution_request: MetricResolutionRequest,
    catalog: OntologyCatalog,
    judge: SemanticJudge | None = None,
) -> DataMappingResult:
    """Run the one-way Structuring → Resolution → composition chain."""

    structuring_result = structure_observations(
        curated,
        structuring_request,
        catalog.observation_schema,
    )
    metric_resolution_result = resolve_metrics(
        structuring_result,
        resolution_request,
        catalog,
        judge,
    )
    effective = derive_effective_metric_resolutions(
        metric_resolution_result.deterministic_decisions,
        metric_resolution_result.semantic_resolutions,
        catalog,
    )
    return DataMappingResult(
        structuring_result=structuring_result,
        metric_resolution_result=metric_resolution_result,
        effective_metric_resolutions=effective,
        resolved_observations=_compose_observations(
            structuring_result.observation_drafts,
            effective,
        ),
    )


def apply_reviewed_resolutions(
    mapping_result: DataMappingResult,
    reviewed_resolutions: tuple[SemanticResolution, ...],
    catalog: OntologyCatalog,
) -> DataMappingResult:
    """Purely replay explicit reviews without rerunning any upstream stage."""

    resolution_result = mapping_result.metric_resolution_result
    if resolution_result.ontology_revision != catalog.ontology_revision:
        raise MetricResolutionReplayError(
            "DataMappingResult 与 OntologyCatalog revision 不一致"
        )
    decisions = {
        item.decision_id: item for item in resolution_result.deterministic_decisions
    }
    candidate_sets = {
        item.source_metric_decision_id: item
        for item in resolution_result.candidate_sets
    }
    originals = {
        item.source_metric_decision_id: item
        for item in resolution_result.semantic_resolutions
    }
    reviewed_by_decision: dict[str, SemanticResolution] = {}
    for reviewed in reviewed_resolutions:
        decision_id = reviewed.source_metric_decision_id
        if decision_id in reviewed_by_decision:
            raise MetricResolutionReplayError(
                "同一 MetricDecision 只能提供一个 reviewed Resolution"
            )
        decision = decisions.get(decision_id)
        candidate_set = candidate_sets.get(decision_id)
        original = originals.get(decision_id)
        if decision is None or candidate_set is None or original is None:
            raise MetricResolutionReplayError(
                "reviewed Resolution 不属于当前 DataMappingResult"
            )
        if reviewed.resolution_id != original.resolution_id:
            raise MetricResolutionReplayError(
                "reviewed Resolution 未保持原始 resolution_id"
            )
        if reviewed.candidate_set_id != candidate_set.candidate_set_id:
            raise MetricResolutionReplayError(
                "reviewed Resolution 与 CandidateSet 不一致"
            )
        if reviewed.ontology_revision != catalog.ontology_revision:
            raise MetricResolutionReplayError(
                "reviewed Resolution 与 OntologyCatalog revision 不一致"
            )
        if reviewed.execution_status is not ExecutionStatus.SUCCEEDED:
            raise MetricResolutionReplayError(
                "只有成功执行的 Resolution 可以回放人工审核"
            )
        if reviewed.review_status not in {
            ResolutionReviewStatus.CONFIRMED,
            ResolutionReviewStatus.REJECTED,
        }:
            raise MetricResolutionReplayError(
                "Resolution 必须经过显式 CONFIRMED 或 REJECTED 审核"
            )
        if replace(reviewed, review_status=original.review_status) != original:
            raise MetricResolutionReplayError(
                "人工审核只能改变 review_status，不能重写语义结论或证据"
            )
        _validate_reviewed_selection(reviewed, candidate_set, catalog)
        reviewed_by_decision[decision_id] = reviewed

    merged = tuple(
        reviewed_by_decision.get(item.source_metric_decision_id, item)
        for item in resolution_result.semantic_resolutions
    )
    replayed_resolution_result = replace(
        resolution_result,
        semantic_resolutions=merged,
    )
    effective = derive_effective_metric_resolutions(
        replayed_resolution_result.deterministic_decisions,
        merged,
        catalog,
    )
    return DataMappingResult(
        structuring_result=mapping_result.structuring_result,
        metric_resolution_result=replayed_resolution_result,
        effective_metric_resolutions=effective,
        resolved_observations=_compose_observations(
            mapping_result.structuring_result.observation_drafts,
            effective,
        ),
    )


def _validate_reviewed_selection(resolution, candidate_set, catalog) -> None:
    candidate_ids = {
        item.metric.current_metric_id for item in candidate_set.candidates
    }
    if resolution.semantic_status is SemanticStatus.MAP_EXISTING:
        if resolution.selected_metric_id not in candidate_ids:
            raise MetricResolutionReplayError(
                "reviewed selected_metric_id 不在 CandidateSet 白名单"
            )
        if catalog.metric_by_id(resolution.selected_metric_id) is None:
            raise MetricResolutionReplayError(
                "reviewed selected_metric_id 不属于当前 ontology revision"
            )
    elif resolution.selected_metric_id is not None:
        raise MetricResolutionReplayError(
            "NO_EQUIVALENT/AMBIGUOUS Resolution 不得选择 Metric"
        )


def _compose_observations(
    drafts: tuple[ObservationDraft, ...],
    effective: tuple[EffectiveMetricResolution, ...],
) -> tuple[ResolvedObservation, ...]:
    by_subject = {item.metric_subject_id: item for item in effective}
    resolved = []
    for draft in drafts:
        resolution = by_subject.get(draft.metric_subject_id)
        if resolution is None:
            raise MetricResolutionReplayError(
                "ObservationDraft 缺少对应 EffectiveMetricResolution"
            )
        resolved.append(
            ResolvedObservation(
                observation=draft,
                metric_resolution=resolution,
            )
        )
    return tuple(resolved)
