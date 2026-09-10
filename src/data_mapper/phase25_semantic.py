"""Phase 2.5 P2/P3：语义 Judge 边界、派生 Mapping 与本体建议。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .mapping_contracts import Evidence, MappingPlan, MetricMatchStatus, OntologyCatalog
from .phase25_semantic_contracts import (
    EffectiveMappingView,
    EffectiveMetricMapping,
    ExecutionStatus,
    JudgeOutput,
    MetricCandidateSet,
    OntologyChangeProposal,
    ProposalKind,
    ResolutionReviewStatus,
    SemanticResolution,
    SemanticStatus,
)


class JudgeUnavailableError(RuntimeError):
    """Judge 服务明确不可用。"""


class SemanticJudge(Protocol):
    name: str
    version: str
    prompt_version: str | None
    model: str | None

    def judge(self, candidate_set: MetricCandidateSet) -> JudgeOutput | Mapping[str, Any]:
        """判断业务等价；允许拒绝全部候选。"""


def run_semantic_judgment(
    candidate_set: MetricCandidateSet,
    catalog: OntologyCatalog,
    judge: SemanticJudge,
) -> SemanticResolution:
    """执行一次 Judge，并把技术失败与业务结论严格分离。"""

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
        raw_output = judge.judge(candidate_set)
        output = _parse_judge_output(raw_output)
        _validate_judge_output(output, candidate_set, catalog)
    except TimeoutError as exc:
        return _failed_resolution(candidate_set, metadata, "TIMEOUT", exc)
    except JudgeUnavailableError as exc:
        return _failed_resolution(candidate_set, metadata, "UNAVAILABLE", exc)
    except (TypeError, ValueError, KeyError) as exc:
        return _failed_resolution(candidate_set, metadata, "INVALID_OUTPUT", exc)
    except Exception as exc:  # Judge 是外部边界，异常必须转成技术状态。
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
    """显式确认或拒绝成功的语义结论；技术失败不能被业务确认。"""

    if resolution.execution_status is not ExecutionStatus.SUCCEEDED:
        raise ValueError("FAILED/SKIPPED Resolution 不能进入确认状态")
    if status is ResolutionReviewStatus.PROPOSED:
        raise ValueError("review_resolution 只接受 CONFIRMED 或 REJECTED")
    return replace(resolution, review_status=status)


def build_effective_mapping_view(
    plan: MappingPlan,
    resolutions: tuple[SemanticResolution, ...],
    catalog: OntologyCatalog,
) -> EffectiveMappingView:
    """构造只读派生视图；不修改 Phase 2 MappingPlan。"""

    if plan.ontology_catalog.ontology_revision != catalog.ontology_revision:
        raise ValueError("MappingPlan 与 OntologyCatalog revision 不一致")
    by_decision: dict[str, SemanticResolution] = {}
    for resolution in resolutions:
        if resolution.ontology_revision != catalog.ontology_revision:
            raise ValueError("Resolution 与 OntologyCatalog revision 不一致")
        if resolution.source_metric_decision_id in by_decision:
            raise ValueError("同一 MetricDecision 只能提供一个 Resolution")
        by_decision[resolution.source_metric_decision_id] = resolution

    items: list[EffectiveMetricMapping] = []
    for decision in plan.metric_decisions:
        if decision.status is MetricMatchStatus.MATCHED:
            assert decision.selected_metric is not None
            items.append(
                EffectiveMetricMapping(
                    source_metric_decision_id=decision.decision_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status="MAPPED",
                    current_metric_id=decision.selected_metric.current_metric_id,
                    source="PHASE_2",
                    source_resolution_id=None,
                    evidence=decision.evidence,
                )
            )
            continue

        resolution = by_decision.get(decision.decision_id)
        if (
            resolution is not None
            and resolution.execution_status is ExecutionStatus.SUCCEEDED
            and resolution.semantic_status is SemanticStatus.MAP_EXISTING
            and resolution.review_status is ResolutionReviewStatus.CONFIRMED
        ):
            items.append(
                EffectiveMetricMapping(
                    source_metric_decision_id=decision.decision_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status="MAPPED",
                    current_metric_id=resolution.selected_metric_id,
                    source="PHASE_2_5_CONFIRMED",
                    source_resolution_id=resolution.resolution_id,
                    evidence=resolution.supporting_evidence,
                )
            )
        else:
            items.append(
                EffectiveMetricMapping(
                    source_metric_decision_id=decision.decision_id,
                    ontology_revision=decision.ontology_revision,
                    effective_status="UNRESOLVED",
                    current_metric_id=None,
                    source="PHASE_2_UNRESOLVED",
                    source_resolution_id=(resolution.resolution_id if resolution else None),
                )
            )
    return EffectiveMappingView(
        mapping_run_id=plan.mapping_run_id,
        ontology_revision=catalog.ontology_revision,
        items=tuple(items),
    )


def build_ontology_change_proposal(
    resolution: SemanticResolution,
    candidate_set: MetricCandidateSet,
    *,
    alias_context_independent: bool = False,
) -> OntologyChangeProposal | None:
    """生成不可执行的 draft Proposal；AMBIGUOUS/失败/跳过不生成。"""

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
        return value
    if not isinstance(value, Mapping):
        raise TypeError("Judge 输出必须是 JudgeOutput 或 object")
    status = SemanticStatus(value["semantic_status"])
    return JudgeOutput(
        semantic_status=status,
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
            continue
        if not isinstance(item, Mapping):
            raise TypeError("evidence item 必须是 object")
        items.append(
            Evidence(
                code=str(item["code"]),
                source=str(item["source"]),
                message=str(item["message"]),
                details=dict(item.get("details") or {}),
            )
        )
    return tuple(items)


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
