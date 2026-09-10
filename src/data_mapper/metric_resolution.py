"""Formal production Metric Resolution orchestration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .candidate_retrieval import retrieve_candidates_for_decision
from .deterministic_resolution import resolve_metrics_deterministically
from .metric_resolution_contracts import (
    ExecutionStatus,
    MetricMatchStatus,
    MetricResolutionConfigurationError,
    MetricResolutionMode,
    MetricResolutionReport,
    MetricResolutionRequest,
    MetricResolutionResult,
    OntologyCatalog,
    SemanticStatus,
)
from .observation_contracts import ObservationStructuringResult, RowRole
from .semantic_resolution import (
    SemanticJudge,
    build_ontology_change_proposal,
    run_semantic_judgment,
)


def resolve_metrics(
    structuring_result: ObservationStructuringResult,
    request: MetricResolutionRequest,
    catalog: OntologyCatalog,
    judge: SemanticJudge | None = None,
) -> MetricResolutionResult:
    """Resolve Metrics without changing structure, row roles, or Drafts."""

    if (
        structuring_result.observation_schema_fingerprint
        != catalog.observation_schema.fingerprint
    ):
        raise MetricResolutionConfigurationError(
            "ObservationStructuringResult 与 OntologyCatalog schema fingerprint 不一致"
        )
    if request.mode is MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK:
        if judge is None:
            raise MetricResolutionConfigurationError(
                "Semantic fallback 已启用，但未提供可用 Judge"
            )
    decisions = resolve_metrics_deterministically(
        structuring_result.row_subjects,
        request,
        catalog,
    )
    candidate_sets = []
    semantic_resolutions = []
    proposals = []
    if request.mode is MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK:
        assert judge is not None
        eligible = [
            decision
            for decision in decisions
            if (
                decision.subject.row_role is RowRole.METRIC
                and decision.status
                in {MetricMatchStatus.UNMATCHED, MetricMatchStatus.AMBIGUOUS}
            )
        ]
        if request.semantic_source_rows:
            selected_rows = set(request.semantic_source_rows)
            eligible = [
                decision
                for decision in eligible
                if decision.subject.source_row in selected_rows
            ]
        if request.semantic_max_judgments is not None:
            eligible = eligible[: request.semantic_max_judgments]
        for decision in eligible:
            candidate_set = retrieve_candidates_for_decision(
                decision,
                structuring_result,
                decisions,
                catalog,
                top_k=request.retrieval_top_k,
                context_window=request.semantic_context_window,
            )
            candidate_sets.append(candidate_set)
            resolution = run_semantic_judgment(candidate_set, catalog, judge)
            semantic_resolutions.append(resolution)
            proposal = build_ontology_change_proposal(resolution, candidate_set)
            if proposal is not None:
                proposals.append(proposal)

    resolution_run_id = _stable_id(
        "resolution-run",
        {
            "structuring_run_id": structuring_result.structuring_run_id,
            "ontology_revision": catalog.ontology_revision,
            "request": request.to_dict(),
            "deterministic_decision_ids": [item.decision_id for item in decisions],
        },
    )
    deterministic_counts = {status.value: 0 for status in MetricMatchStatus}
    for decision in decisions:
        deterministic_counts[decision.status.value] += 1
    execution_counts = {status.value: 0 for status in ExecutionStatus}
    semantic_counts = {status.value: 0 for status in SemanticStatus}
    for resolution in semantic_resolutions:
        execution_counts[resolution.execution_status.value] += 1
        if resolution.semantic_status is not None:
            semantic_counts[resolution.semantic_status.value] += 1
    report = MetricResolutionReport(
        resolution_run_id=resolution_run_id,
        deterministic_status_counts=deterministic_counts,
        candidate_set_count=len(candidate_sets),
        semantic_execution_counts=execution_counts,
        semantic_status_counts=semantic_counts,
        ontology_change_proposal_count=len(proposals),
    )
    return MetricResolutionResult(
        resolution_run_id=resolution_run_id,
        structuring_run_id=structuring_result.structuring_run_id,
        ontology_revision=catalog.ontology_revision,
        request=request,
        deterministic_decisions=decisions,
        candidate_sets=tuple(candidate_sets),
        semantic_resolutions=tuple(semantic_resolutions),
        ontology_change_proposals=tuple(proposals),
        report=report,
    )


def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{namespace}:" + hashlib.sha256(encoded).hexdigest()
