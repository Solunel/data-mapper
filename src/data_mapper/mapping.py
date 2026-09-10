"""R1 migration facade for the former mixed Phase 2 mapping entry point."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping

from .contracts import CuratedDataset
from .deterministic_resolution import resolve_metrics_deterministically
from .mapping_contracts import (
    MappingPlan,
    MappingReport,
    MappingRequest,
    MappingResult,
    MetricMatchStatus,
    ObservationCandidate,
    OntologyCatalog,
    RowRole,
    StructureStatus,
    UnresolvedBinding,
)
from .metric_resolution_contracts import (
    MetricResolutionMode,
    MetricResolutionRequest,
)
from .observation_contracts import ObservationStructuringRequest
from .observation_structuring import (
    comparison_key,
    comparison_name,
    structure_observations,
)


def split_mapping_request(
    request: MappingRequest,
) -> tuple[ObservationStructuringRequest, MetricResolutionRequest]:
    """Explicitly adapt the legacy mixed request into the two R1 requests."""

    hinted_rows = tuple(
        sorted(set(request.metric_overrides).union(request.ontology_gap_confirmations))
    )
    return (
        ObservationStructuringRequest(
            curated_id=request.curated_id,
            metric_name_field=request.metric_name_field,
            value_bindings=request.value_bindings,
            organization=request.organization,
            organization_id=request.organization_current_id,
            report_period_type=request.report_period_type,
            report_period_key=request.report_period_key,
            unit=request.unit,
            ignored_fields=request.ignored_fields,
            metric_row_hints=hinted_rows,
            structuring_rule_version=request.mapping_rule_version,
        ),
        MetricResolutionRequest(
            mode=MetricResolutionMode.DETERMINISTIC_ONLY,
            metric_overrides=request.metric_overrides,
            ontology_gap_confirmations=request.ontology_gap_confirmations,
            deterministic_rule_version=request.mapping_rule_version,
        ),
    )


def map_curated_dataset(
    curated: CuratedDataset,
    request: MappingRequest,
    catalog: OntologyCatalog,
) -> MappingResult:
    """Migration-only wrapper preserving the frozen R0 output contract."""

    run_id = _stable_id(
        "mapping-run",
        {
            "curated_id": curated.curated_id,
            "raw_dataset_id": curated.raw_dataset_id,
            "raw_version_id": curated.raw_version_id,
            "ontology_revision": catalog.ontology_revision,
            "request": request.to_dict(),
        },
    )
    structuring_request, resolution_request = split_mapping_request(request)
    structured = structure_observations(
        curated,
        structuring_request,
        catalog.observation_schema,
    )
    table_plan = structured.table_mapping_plan
    legacy_errors = _legacy_input_errors(curated, request, catalog, table_plan.metric_name_field)
    if legacy_errors:
        table_plan = replace(
            table_plan,
            structure_status=StructureStatus.BLOCKED,
            unresolved_bindings=(
                *table_plan.unresolved_bindings,
                *(UnresolvedBinding(None, "input", message) for message in legacy_errors),
            ),
        )

    row_subjects = ()
    decisions = ()
    candidates = ()
    unprojected: tuple[Mapping[str, Any], ...] = ()
    empty_rows = 0
    if table_plan.structure_status is not StructureStatus.BLOCKED:
        row_subjects = structured.row_subjects
        decisions = resolve_metrics_deterministically(row_subjects, resolution_request, catalog)
        candidates = _legacy_candidates(structured.observation_drafts, decisions, request, catalog)
        unprojected = structured.report.unprojected_values
        empty_rows = structured.report.empty_value_row_count

    counts = {status.value: 0 for status in MetricMatchStatus}
    for decision in decisions:
        counts[decision.status.value] += 1
    row_role_counts = {role.value: 0 for role in RowRole}
    for subject in row_subjects:
        row_role_counts[subject.row_role.value] += 1
    constraint_counts: dict[str, int] = {}
    for candidate in candidates:
        for field_name in candidate.instantiation_missing_fields:
            constraint_counts[field_name] = constraint_counts.get(field_name, 0) + 1

    warnings = [
        issue.message
        for issue in curated.quality_report.issues
        if issue.severity == "warning"
    ]
    if not catalog.organization_ids:
        warnings.append("OntologyCatalog 当前没有 Organization 实例；不会伪造 organization_id")
    if any(candidate.unit_raw and candidate.unit_normalized is None for candidate in candidates):
        warnings.append("存在当前 Unit 枚举无法表达的原始单位；原值已保留")

    plan = MappingPlan(
        mapping_run_id=run_id,
        curated_id=curated.curated_id,
        raw_dataset_id=curated.raw_dataset_id,
        raw_version_id=curated.raw_version_id,
        ontology_catalog=catalog.summary(),
        request=request,
        table_mapping_plan=table_plan,
        row_subjects=row_subjects,
        metric_decisions=decisions,
        observation_candidates=candidates,
    )
    report = MappingReport(
        mapping_run_id=run_id,
        structure_status=table_plan.structure_status,
        ontology_revision=catalog.ontology_revision,
        row_role_counts=row_role_counts,
        metric_status_counts=counts,
        observation_candidate_count=len(candidates),
        metric_decision_count=len(decisions),
        empty_value_row_count=empty_rows,
        unprojected_values=unprojected,
        ignored_fields=table_plan.ignored_fields,
        unresolved_bindings=table_plan.unresolved_bindings,
        constraint_issue_counts=constraint_counts,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return MappingResult(plan=plan, report=report)


def _legacy_candidates(drafts, decisions, request, catalog) -> tuple[ObservationCandidate, ...]:
    by_subject = {decision.subject.subject_id: decision for decision in decisions}
    candidates: list[ObservationCandidate] = []
    candidate_ids: set[str] = set()
    for draft in drafts:
        decision = by_subject[draft.metric_subject_id]
        current_metric_id = (
            decision.selected_metric.current_metric_id
            if decision.status is MetricMatchStatus.MATCHED
            and decision.selected_metric is not None
            else None
        )
        missing = ["id"]
        if request.organization_current_id is None:
            missing.append("organization_id")
        if current_metric_id is None:
            missing.append("metric_id")
        if draft.unit_normalized is None:
            missing.append("unit")
        missing.append("status")
        candidate_id = _stable_id(
            "observation-candidate",
            {
                "curated_id": draft.curated_id,
                "raw_dataset_id": draft.raw_dataset_id,
                "raw_version_id": draft.raw_version_id,
                "source_file": draft.source_file,
                "sheet_name": draft.sheet_name,
                "source_row": draft.source_row,
                "value_source_column": draft.value_source_column,
                "organization_value": draft.organization_value,
                "organization_current_id": request.organization_current_id,
                "ontology_revision": catalog.ontology_revision,
                "metric_subject_id": draft.metric_subject_id,
                "current_metric_id": current_metric_id,
                "business_scope": draft.business_scope,
                "period_type": draft.period_type,
                "period_key": draft.period_key,
                "period_basis": draft.period_basis,
                "unit_raw": draft.unit_raw,
                "unit_normalized": draft.unit_normalized,
                "mapping_rule_version": request.mapping_rule_version,
            },
        )
        if candidate_id in candidate_ids:
            continue
        candidate_ids.add(candidate_id)
        candidates.append(
            ObservationCandidate(
                candidate_id=candidate_id,
                candidate_identity_kind="SOURCE_MAPPING_CANDIDATE",
                metric_decision_id=decision.decision_id,
                metric_name=draft.metric_name,
                ontology_revision=catalog.ontology_revision,
                current_metric_id=current_metric_id,
                actual_value=draft.actual_value,
                business_scope=draft.business_scope,
                organization_value=draft.organization_value,
                organization_current_id=request.organization_current_id,
                period_type=draft.period_type,
                period_key=draft.period_key,
                period_basis=draft.period_basis,
                unit_raw=draft.unit_raw,
                unit_normalized=draft.unit_normalized,
                source=draft.source,
                source_file=draft.source_file,
                sheet_name=draft.sheet_name,
                source_row=draft.source_row,
                metric_source_column=draft.metric_source_column,
                value_source_column=draft.value_source_column,
                value_field=draft.value_field,
                curated_id=draft.curated_id,
                raw_dataset_id=draft.raw_dataset_id,
                raw_version_id=draft.raw_version_id,
                mapping_rule_version=request.mapping_rule_version,
                role_bindings=draft.role_bindings,
                binding_evidence=draft.binding_evidence,
                definition_constraints_satisfied=not missing,
                instantiation_missing_fields=tuple(missing),
            )
        )
    return tuple(candidates)


def _legacy_input_errors(
    curated: CuratedDataset,
    request: MappingRequest,
    catalog: OntologyCatalog,
    metric_field: str | None,
) -> tuple[str, ...]:
    errors: list[str] = []
    if not catalog.ontology_revision.strip():
        errors.append("OntologyCatalog.ontology_revision 不能为空")
    required_basis = {"PERIOD_VALUE", "YEAR_TO_DATE", "PERIOD_BEGIN", "PERIOD_END"}
    missing_basis = required_basis.difference(catalog.period_basis_values)
    if missing_basis:
        errors.append("OntologyCatalog 缺少 period_basis：" + ", ".join(sorted(missing_basis)))
    metric_ids = [metric.current_metric_id for metric in catalog.metrics]
    if len(metric_ids) != len(set(metric_ids)):
        errors.append("OntologyCatalog 存在重复 current_metric_id")
    metric_subject_rows = {
        row.source_row
        for row in curated.rows
        if metric_field is not None
        and row.values.get(metric_field) is not None
        and str(row.values.get(metric_field)).strip()
    }
    for row, current_metric_id in request.metric_overrides.items():
        if row not in metric_subject_rows:
            errors.append(f"Metric override 未指向有效指标主体：source_row={row}")
        if catalog.metric_by_id(current_metric_id) is None:
            errors.append(
                f"source_row={row} 的 Metric override 不存在于当前 ontology_revision："
                f"{current_metric_id}"
            )
    conflicting_rows = sorted(
        set(request.metric_overrides).intersection(request.ontology_gap_confirmations)
    )
    if conflicting_rows:
        errors.append(
            "同一指标主体不能同时 Metric override 和确认 ontology gap："
            + ", ".join(map(str, conflicting_rows))
        )
    missing_gap_rows = sorted(
        set(request.ontology_gap_confirmations).difference(metric_subject_rows)
    )
    if missing_gap_rows:
        errors.append(
            "ontology gap confirmation 未指向有效指标主体："
            + ", ".join(map(str, missing_gap_rows))
        )
    if (
        request.organization_current_id is not None
        and request.organization_current_id not in catalog.organization_ids
    ):
        errors.append(
            "organization_current_id 不存在于当前 ontology_revision："
            + request.organization_current_id
        )
    return tuple(errors)


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
