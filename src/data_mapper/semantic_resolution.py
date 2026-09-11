"""Production semantic Judge boundary and pure resolution derivations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .metric_resolution_contracts import (
    EffectiveMetricResolution,
    ExecutionStatus,
    JudgeOutput,
    MetricCandidateSet,
    MetricDecision,
    MetricMatchStatus,
    OntologyCatalog,
    OntologyChangeProposal,
    ProposalKind,
    ResolutionReviewStatus,
    SemanticResolution,
    SemanticStatus,
)
from .observation_contracts import Evidence


class JudgeUnavailableError(RuntimeError):
    """Judge service is explicitly unavailable."""


class SemanticJudge(Protocol):
    name: str
    version: str
    prompt_version: str | None
    model: str | None

    def judge(self, candidate_set: MetricCandidateSet) -> JudgeOutput | Mapping[str, Any]:
        """Judge business equivalence and allow rejecting every candidate."""


def run_semantic_judgment(
    candidate_set: MetricCandidateSet,
    catalog: OntologyCatalog,
    judge: SemanticJudge,
) -> SemanticResolution:
    """Run one Judge call while separating technical and semantic outcomes."""

    metadata = _judge_metadata(judge)
    if candidate_set.ontology_revision != catalog.ontology_revision:
        return _resolution(
            candidate_set=candidate_set,
            execution_status=ExecutionStatus.SKIPPED,
            semantic_status=None,
            review_status=None,
            selected_metric_id=None,
            reason="输入不适用于当前 ontology revision",
            failure_stage="PRE_EXECUTION",
            error_code="ONTOLOGY_REVISION_MISMATCH",
            error_message=None,
            supporting_evidence=(),
            counter_evidence=(),
            **metadata,
        )
    try:
        output = _parse_judge_output(judge.judge(candidate_set))
        _validate_judge_output(output, candidate_set, catalog)
    except TimeoutError as exc:
        return _failed_resolution(candidate_set, metadata, "TIMEOUT", exc)
    except JudgeUnavailableError as exc:
        return _failed_resolution(candidate_set, metadata, "UNAVAILABLE", exc)
    except (TypeError, ValueError, KeyError) as exc:
        return _failed_resolution(candidate_set, metadata, "INVALID_OUTPUT", exc)
    except Exception as exc:  # The provider boundary must become explicit state.
        return _failed_resolution(candidate_set, metadata, "JUDGE_EXCEPTION", exc)
    return _resolution(
        candidate_set=candidate_set,
        execution_status=ExecutionStatus.SUCCEEDED,
        semantic_status=output.semantic_status,
        review_status=ResolutionReviewStatus.PROPOSED,
        selected_metric_id=output.selected_metric_id,
        reason=output.reason,
        failure_stage=None,
        error_code=None,
        error_message=None,
        supporting_evidence=output.supporting_evidence,
        counter_evidence=output.counter_evidence,
        **metadata,
    )


def review_resolution(
    resolution: SemanticResolution,
    status: ResolutionReviewStatus,
) -> SemanticResolution:
    """Explicitly confirm or reject a successfully executed resolution."""

    if resolution.execution_status is not ExecutionStatus.SUCCEEDED:
        raise ValueError("FAILED/SKIPPED Resolution 不能进入确认状态")
    if status is ResolutionReviewStatus.PROPOSED:
        raise ValueError("review_resolution 只接受 CONFIRMED 或 REJECTED")
    return replace(resolution, review_status=status)


def derive_effective_metric_resolutions(
    decisions: tuple[MetricDecision, ...],
    resolutions: tuple[SemanticResolution, ...],
    catalog: OntologyCatalog,
) -> tuple[EffectiveMetricResolution, ...]:
    """Derive current effective mappings; PROPOSED never becomes effective."""

    by_decision: dict[str, SemanticResolution] = {}
    known_decisions = {decision.decision_id for decision in decisions}
    for resolution in resolutions:
        if resolution.ontology_revision != catalog.ontology_revision:
            raise ValueError("Resolution 与 OntologyCatalog revision 不一致")
        if resolution.source_metric_decision_id not in known_decisions:
            raise ValueError("Resolution 不属于当前 MetricDecision 集合")
        if resolution.source_metric_decision_id in by_decision:
            raise ValueError("同一 MetricDecision 只能提供一个 Resolution")
        by_decision[resolution.source_metric_decision_id] = resolution

    effective: list[EffectiveMetricResolution] = []
    for decision in decisions:
        if decision.ontology_revision != catalog.ontology_revision:
            raise ValueError("MetricDecision 与 OntologyCatalog revision 不一致")
        resolution = by_decision.get(decision.decision_id)
        if (
            resolution is not None
            and resolution.execution_status is ExecutionStatus.SUCCEEDED
            and resolution.semantic_status is SemanticStatus.MAP_EXISTING
            and resolution.review_status is ResolutionReviewStatus.CONFIRMED
        ):
            effective.append(
                EffectiveMetricResolution(
                    source_metric_decision_id=decision.decision_id,
                    metric_subject_id=decision.subject.subject_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status="MAPPED",
                    current_metric_id=resolution.selected_metric_id,
                    source="SEMANTIC_CONFIRMED",
                    source_resolution_id=resolution.resolution_id,
                    evidence=resolution.supporting_evidence,
                )
            )
        elif decision.status is MetricMatchStatus.MATCHED:
            assert decision.selected_metric is not None
            effective.append(
                EffectiveMetricResolution(
                    source_metric_decision_id=decision.decision_id,
                    metric_subject_id=decision.subject.subject_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status="MAPPED",
                    current_metric_id=decision.selected_metric.current_metric_id,
                    source="DETERMINISTIC",
                    source_resolution_id=None,
                    evidence=decision.evidence,
                )
            )
        else:
            effective.append(
                EffectiveMetricResolution(
                    source_metric_decision_id=decision.decision_id,
                    metric_subject_id=decision.subject.subject_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status=(
                        "ONTOLOGY_GAP"
                        if decision.status is MetricMatchStatus.ONTOLOGY_GAP
                        else "UNRESOLVED"
                    ),
                    current_metric_id=None,
                    source="DETERMINISTIC_UNRESOLVED",
                    source_resolution_id=(
                        resolution.resolution_id if resolution is not None else None
                    ),
                )
            )
    return tuple(effective)


def build_ontology_change_proposal(
    resolution: SemanticResolution,
    candidate_set: MetricCandidateSet,
    *,
    alias_context_independent: bool = False,
) -> OntologyChangeProposal | None:
    """Build a non-executable proposal; ambiguous or failed runs yield none."""

    if resolution.execution_status is not ExecutionStatus.SUCCEEDED:
        return None
    if resolution.candidate_set_id != candidate_set.candidate_set_id:
        raise ValueError("Resolution 与 CandidateSet 不一致")
    if resolution.ontology_revision != candidate_set.ontology_revision:
        raise ValueError("Resolution 与 CandidateSet revision 不一致")
    if resolution.semantic_status is SemanticStatus.AMBIGUOUS:
        return None
    context = candidate_set.semantic_context
    subject = context.get("metric_subject") or {}
    raw_label = str(subject.get("raw_label") or "")
    comparison_name = str(subject.get("comparison_name") or "")
    related_ids = tuple(
        item.metric.current_metric_id for item in candidate_set.candidates[:3]
    )
    if resolution.semantic_status is SemanticStatus.MAP_EXISTING:
        if not alias_context_independent:
            return None
        proposal_kind = ProposalKind.ADD_ALIAS
        target_metric_id = resolution.selected_metric_id
        suggested_alias = comparison_name
        suggested_name = None
    else:
        proposal_kind = ProposalKind.ADD_METRIC
        target_metric_id = None
        suggested_alias = None
        suggested_name = comparison_name
    stable_payload = {
        "proposal_kind": proposal_kind.value,
        "source_resolution_id": resolution.resolution_id,
        "source_metric_decision_id": resolution.source_metric_decision_id,
        "ontology_revision": resolution.ontology_revision,
        "target_metric_id": target_metric_id,
        "suggested_alias": suggested_alias,
        "suggested_name_cn": suggested_name,
    }
    return OntologyChangeProposal(
        proposal_id=_stable_id("ontology-change-proposal", stable_payload),
        proposal_kind=proposal_kind,
        source_resolution_id=resolution.resolution_id,
        source_metric_decision_id=resolution.source_metric_decision_id,
        ontology_revision=resolution.ontology_revision,
        source=dict(context.get("source") or {}),
        raw_label=raw_label,
        comparison_name=comparison_name,
        context=context,
        suggested_name_cn=suggested_name,
        suggested_definition_cn=None,
        suggested_value_semantics=None,
        target_metric_id=target_metric_id,
        suggested_alias=suggested_alias,
        related_metric_ids=related_ids,
        reason=resolution.reason,
        evidence=resolution.supporting_evidence + resolution.counter_evidence,
        review_status=ResolutionReviewStatus.PROPOSED,
    )


def _parse_judge_output(value: JudgeOutput | Mapping[str, Any]) -> JudgeOutput:
    if isinstance(value, JudgeOutput):
        return replace(
            value,
            supporting_evidence=value.supporting_evidence[:2],
            counter_evidence=value.counter_evidence[:2],
        )
    if not isinstance(value, Mapping):
        raise TypeError("Judge 输出必须是 JudgeOutput 或 object")
    return JudgeOutput(
        semantic_status=SemanticStatus(value["semantic_status"]),
        selected_metric_id=_optional_text(value.get("selected_metric_id")),
        reason=str(value.get("reason") or ""),
        supporting_evidence=_parse_evidence(value.get("supporting_evidence", ())),
        counter_evidence=_parse_evidence(value.get("counter_evidence", ())),
    )


def _parse_evidence(value: Any) -> tuple[Evidence, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("evidence 必须是数组")
    items: list[Evidence] = []
    for item in value:
        if isinstance(item, Evidence):
            items.append(item)
        elif isinstance(item, Mapping):
            items.append(
                Evidence(
                    code=str(item["code"]),
                    source=str(item["source"]),
                    message=str(item["message"]),
                    details=dict(item.get("details") or {}),
                )
            )
        else:
            raise TypeError("evidence item 必须是 object")
    return tuple(items[:2])


def _validate_judge_output(
    output: JudgeOutput,
    candidate_set: MetricCandidateSet,
    catalog: OntologyCatalog,
) -> None:
    if not output.reason.strip():
        raise ValueError("Judge 成功输出必须提供 reason")
    if not output.supporting_evidence and not output.counter_evidence:
        raise ValueError("Judge 成功输出必须提供 supporting/counter evidence")
    candidate_ids = {
        item.metric.current_metric_id for item in candidate_set.candidates
    }
    if output.semantic_status is SemanticStatus.MAP_EXISTING:
        if output.selected_metric_id is None:
            raise ValueError("MAP_EXISTING 必须提供 selected_metric_id")
        if output.selected_metric_id not in candidate_ids:
            raise ValueError("selected_metric_id 不在 CandidateSet 白名单")
        if catalog.metric_by_id(output.selected_metric_id) is None:
            raise ValueError("selected_metric_id 不属于当前 ontology revision")
    elif output.selected_metric_id is not None:
        raise ValueError("NO_EQUIVALENT/AMBIGUOUS 不得提供 selected_metric_id")


def _failed_resolution(
    candidate_set: MetricCandidateSet,
    metadata: Mapping[str, str | None],
    error_code: str,
    exc: Exception,
) -> SemanticResolution:
    return _resolution(
        candidate_set=candidate_set,
        execution_status=ExecutionStatus.FAILED,
        semantic_status=None,
        review_status=None,
        selected_metric_id=None,
        reason="语义判断未产生业务结论",
        failure_stage="SEMANTIC_JUDGMENT",
        error_code=error_code,
        error_message=str(exc),
        supporting_evidence=(),
        counter_evidence=(),
        **metadata,
    )


def _resolution(
    *,
    candidate_set: MetricCandidateSet,
    execution_status: ExecutionStatus,
    semantic_status: SemanticStatus | None,
    review_status: ResolutionReviewStatus | None,
    selected_metric_id: str | None,
    reason: str,
    failure_stage: str | None,
    error_code: str | None,
    error_message: str | None,
    supporting_evidence: tuple[Evidence, ...],
    counter_evidence: tuple[Evidence, ...],
    name: str,
    version: str,
    prompt_version: str | None,
    model: str | None,
) -> SemanticResolution:
    return SemanticResolution(
        resolution_id=f"semantic-resolution:{uuid4()}",
        source_metric_decision_id=candidate_set.source_metric_decision_id,
        candidate_set_id=candidate_set.candidate_set_id,
        ontology_revision=candidate_set.ontology_revision,
        execution_status=execution_status,
        semantic_status=semantic_status,
        review_status=review_status,
        selected_metric_id=selected_metric_id,
        reason=reason,
        failure_stage=failure_stage,
        error_code=error_code,
        error_message=error_message,
        supporting_evidence=supporting_evidence,
        counter_evidence=counter_evidence,
        judge=name,
        judge_version=version,
        prompt_version=prompt_version,
        model=model,
    )


def _judge_metadata(judge: SemanticJudge) -> dict[str, str | None]:
    return {
        "name": str(getattr(judge, "name", type(judge).__name__)),
        "version": str(getattr(judge, "version", "unknown")),
        "prompt_version": _optional_text(getattr(judge, "prompt_version", None)),
        "model": _optional_text(getattr(judge, "model", None)),
    }


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{namespace}:" + hashlib.sha256(encoded).hexdigest()
