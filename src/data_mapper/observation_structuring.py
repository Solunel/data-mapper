"""CuratedDataset 到 ObservationDraft 的确定性只读结构化。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .contracts import CuratedDataset, CuratedRow
from .observation_contracts import (
    Evidence,
    IgnoredField,
    MetricSubject,
    ObservationDraft,
    ObservationSchema,
    ObservationStructuringReport,
    ObservationStructuringRequest,
    ObservationStructuringResult,
    PlannedValueField,
    ResolvedBinding,
    RowRole,
    ScalarBinding,
    StructureStatus,
    TableMappingPlan,
    UnresolvedBinding,
    ValueFieldBinding,
)


_METRIC_FIELDS = {"项目", "科目名称", "费用明细", "成本项目", "指标名称"}
_ORGANIZATION_FIELDS = {"公司名称", "组织", "组织名称", "企业名称"}
_PERIOD_FIELDS = {"时间", "期间", "报告期", "报表期间"}
_UNIT_FIELDS = {"单位", "计量单位"}
_AUXILIARY_FIELDS = {"行次", "序号", "上级公司", "备注", "说明"}
_COMPARISON_TERMS = ("同期", "同比", "环比", "增减", "差异")
_AUTO_PERIOD_BASIS = {
    "本月数": "PERIOD_VALUE",
    "本期数": "PERIOD_VALUE",
    "本年累计数": "YEAR_TO_DATE",
    "期初数": "PERIOD_BEGIN",
    "期末数": "PERIOD_END",
    "年初数": "PERIOD_BEGIN",
}
_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "－": "-",
        "–": "-",
        "—": "-",
        "―": "-",
    }
)
_REPORT_NUMBERING = re.compile(
    r"^\s*(?:[（(][一二三四五六七八九十百\d]+[）)]|"
    r"\d+(?:\.\d+)*[.、]|[一二三四五六七八九十百]+[.、])\s*"
)
_HIERARCHY_PREFIX = re.compile(r"^\s*(其中|加|减)\s*[：:]\s*")
_DISPLAY_MARKER = re.compile(r"^\s*([*＊△▲])\s*")
_DISPLAY_INSTRUCTION = re.compile(
    r"\s*[（(](?:净?亏损|损失|净收益)以[“\"'‘’]?[－—-][”\"'‘’]?号?填列[）)]\s*$"
)
_NOTE_LABEL = re.compile(r"^\s*注\s*[：:]")
_GROUP_LABEL = re.compile(r"(?:分类|类别|分组)\s*[：:]\s*$")
_GENERIC_UNKNOWN_LABELS = {"其他"}


def structure_observations(
    curated: CuratedDataset,
    request: ObservationStructuringRequest,
    observation_schema: ObservationSchema,
) -> ObservationStructuringResult:
    """只理解源观测结构，不查询 Metric / Organization 目录。"""

    run_id = _stable_id(
        "structuring-run",
        {
            "curated_id": curated.curated_id,
            "raw_dataset_id": curated.raw_dataset_id,
            "raw_version_id": curated.raw_version_id,
            "observation_schema_fingerprint": observation_schema.fingerprint,
            "request": request.to_dict(),
        },
    )
    table_plan = _build_table_plan(curated, request, observation_schema)
    row_subjects: tuple[MetricSubject, ...] = ()
    drafts: tuple[ObservationDraft, ...] = ()
    unprojected: list[Mapping[str, Any]] = []
    empty_rows = 0
    if table_plan.structure_status is not StructureStatus.BLOCKED:
        row_subjects = _extract_metric_subjects(curated, request, table_plan)
        drafts, unprojected, empty_rows = _project_drafts(
            curated, request, observation_schema, table_plan, row_subjects
        )

    row_role_counts = {role.value: 0 for role in RowRole}
    for subject in row_subjects:
        row_role_counts[subject.row_role.value] += 1
    warnings = [
        issue.message
        for issue in curated.quality_report.issues
        if issue.severity == "warning"
    ]
    if any(draft.unit_raw and draft.unit_normalized is None for draft in drafts):
        warnings.append("存在当前 Unit 枚举无法表达的原始单位；原值已保留")
    report = ObservationStructuringReport(
        structuring_run_id=run_id,
        structure_status=table_plan.structure_status,
        row_role_counts=row_role_counts,
        observation_draft_count=len(drafts),
        metric_subject_count=len(row_subjects),
        empty_value_row_count=empty_rows,
        unprojected_values=tuple(unprojected),
        ignored_fields=table_plan.ignored_fields,
        unresolved_bindings=table_plan.unresolved_bindings,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return ObservationStructuringResult(
        structuring_run_id=run_id,
        curated_id=curated.curated_id,
        raw_dataset_id=curated.raw_dataset_id,
        raw_version_id=curated.raw_version_id,
        observation_schema_fingerprint=observation_schema.fingerprint,
        request=request,
        table_mapping_plan=table_plan,
        row_subjects=row_subjects,
        observation_drafts=drafts,
        report=report,
    )


def comparison_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def comparison_key(value: str) -> str:
    normalized = comparison_name(value).translate(_PUNCTUATION_TRANSLATION).casefold()
    return re.sub(r"\s*([,.:;()\-])\s*", r"\1", normalized)


def _build_table_plan(
    curated: CuratedDataset,
    request: ObservationStructuringRequest,
    schema: ObservationSchema,
) -> TableMappingPlan:
    fields = tuple(mapping.normalized_name for mapping in curated.header_mapping)
    positions = {
        mapping.normalized_name: mapping.source_position
        for mapping in curated.header_mapping
    }
    schemas = {column.normalized_name: column for column in curated.data_schema.columns}
    evidence: list[Evidence] = []
    unresolved: list[UnresolvedBinding] = []
    ignored: list[IgnoredField] = []
    fatal: list[str] = []

    fatal.extend(_curated_contract_errors(curated))
    fatal.extend(_schema_errors(schema))
    if request.curated_id != curated.curated_id:
        fatal.append(
            f"MappingRequest.curated_id 与输入不一致：{request.curated_id}"
        )
    if not request.structuring_rule_version.strip():
        fatal.append("structuring_rule_version 不能为空")
    if not curated.quality_report.passed:
        fatal.append("Curated quality_report.passed != true")

    metric_field = request.metric_name_field
    if metric_field is not None:
        if metric_field not in positions:
            fatal.append(f"指标名称字段不存在：{metric_field}")
            metric_field = None
        else:
            evidence.append(
                Evidence(
                    code="explicit_metric_name_field",
                    source="MappingRequest",
                    message=f"显式绑定指标名称字段：{metric_field}",
                )
            )
    else:
        metric_candidates = [
            field for field in fields if _structural_key(field) in _METRIC_FIELDS
        ]
        if len(metric_candidates) == 1:
            metric_field = metric_candidates[0]
            evidence.append(
                Evidence(
                    code="unique_metric_name_field",
                    source="deterministic_rule",
                    message=f"唯一明确的指标名称字段：{metric_field}",
                )
            )
        elif len(metric_candidates) > 1:
            unresolved.append(
                UnresolvedBinding(None, "metric_name", "存在多个合理的指标名称字段")
            )
        else:
            fatal.append("无法识别指标名称字段，且未提供显式 override")

    organization = _choose_table_scalar(
        request.organization,
        fields,
        _ORGANIZATION_FIELDS,
        "organization",
        "组织",
        evidence,
        fatal,
    )
    if organization is None and not fatal:
        unresolved.append(UnresolvedBinding(None, "organization", "无法唯一绑定组织来源"))

    report_period = _choose_table_scalar(
        request.report_period_key,
        fields,
        _PERIOD_FIELDS,
        "period_key",
        "报表期间",
        evidence,
        fatal,
    )
    table_unit = _choose_table_scalar(
        request.unit,
        fields,
        _UNIT_FIELDS,
        "unit",
        "单位",
        evidence,
        fatal,
        required=False,
    )
    context_unit_conflict = False
    unit_field_candidates = [
        field for field in fields if _structural_key(field) in _UNIT_FIELDS
    ]
    if request.unit is None and len(unit_field_candidates) > 1:
        context_unit_conflict = True
    if table_unit is None:
        context_unit, has_context_unit_conflict = _unit_from_context(curated)
        if not context_unit_conflict:
            table_unit = context_unit
        if has_context_unit_conflict:
            context_unit_conflict = True
    if table_unit is None and not context_unit_conflict:
        table_unit = ResolvedBinding(
            role="unit",
            kind="missing",
            evidence=(
                Evidence(
                    "unit_missing_reported",
                    "table_plan",
                    "没有可提取的原始单位；候选将保留单位约束缺口",
                ),
            ),
        )

    requested_value_fields = (
        list(request.value_bindings)
        if request.value_bindings
        else [
            ValueFieldBinding(value_field=field)
            for field in fields
            if _structural_key(field) in _AUTO_PERIOD_BASIS
        ]
    )
    duplicates = _duplicates(binding.value_field for binding in requested_value_fields)
    if duplicates:
        fatal.append("数值字段 binding 重复：" + ", ".join(duplicates))
    planned_values = [
        _plan_value_field(
            requested,
            request,
            curated,
            positions,
            schemas,
            report_period,
            table_unit,
            schema,
            fatal,
        )
        for requested in requested_value_fields
    ]
    if not planned_values and not fatal:
        unresolved.append(UnresolvedBinding(None, "actual_value", "没有可唯一识别的数值字段"))

    explicitly_ignored: set[str] = set()
    for field, reason in request.ignored_fields.items():
        if field not in positions:
            fatal.append(f"ignored field 不存在：{field}")
            continue
        if not str(reason).strip():
            fatal.append(f"ignored field 缺少原因：{field}")
            continue
        explicitly_ignored.add(field)
        ignored.append(IgnoredField(field, positions[field], str(reason)))

    used_fields = {metric_field} if metric_field else set()
    for binding in (organization, report_period, table_unit):
        if binding and binding.field:
            used_fields.add(binding.field)
    for planned in planned_values:
        used_fields.add(planned.value_field)
        for binding in (planned.business_scope, planned.period_key, planned.unit):
            if binding and binding.field:
                used_fields.add(binding.field)

    for field in fields:
        if field in used_fields or field in explicitly_ignored:
            continue
        name = _structural_key(field)
        data_schema = schemas[field]
        if name in _AUXILIARY_FIELDS:
            ignored.append(
                IgnoredField(field, positions[field], "已识别为辅助/行定位字段，不参与观测投影")
            )
        elif any(term in name for term in _COMPARISON_TERMS):
            ignored.append(
                IgnoredField(
                    field,
                    positions[field],
                    "比较语义超出当前范围",
                    disposition="out-of-scope",
                )
            )
        elif data_schema.data_type == "null":
            ignored.append(
                IgnoredField(field, positions[field], "当前 Curated 中该列全空，未作为值字段猜测")
            )
        elif data_schema.data_type in {"integer", "number"}:
            unresolved.append(
                UnresolvedBinding(field, "actual_value", "数值列语义无法由受限规则唯一确定")
            )
        else:
            ignored.append(
                IgnoredField(
                    field,
                    positions[field],
                    "非数值且未承担本次 Mapping 结构角色",
                    disposition="out-of-scope",
                )
            )

    contradictory_fields = sorted(used_fields.intersection(explicitly_ignored))
    if contradictory_fields:
        fatal.append(
            "字段不能同时承担 Mapping 角色和被 ignored：" + ", ".join(contradictory_fields)
        )
    for planned in planned_values:
        unresolved.extend(
            UnresolvedBinding(planned.value_field, "value_field", reason)
            for reason in planned.unresolved_reasons
        )

    metric_subject_rows = {
        row.source_row
        for row in curated.rows
        if metric_field is not None
        and row.values.get(metric_field) is not None
        and str(row.values.get(metric_field)).strip()
    }
    missing_hint_rows = sorted(set(request.metric_row_hints).difference(metric_subject_rows))
    if missing_hint_rows:
        fatal.append(
            "metric_row_hints 未指向有效指标主体："
            + ", ".join(map(str, missing_hint_rows))
        )

    if fatal:
        status = StructureStatus.BLOCKED
        unresolved.extend(UnresolvedBinding(None, "input", message) for message in fatal)
    elif unresolved:
        status = StructureStatus.NEEDS_BINDING
    else:
        status = StructureStatus.READY
    return TableMappingPlan(
        structure_status=status,
        metric_name_field=metric_field,
        metric_name_source_column=positions.get(metric_field) if metric_field else None,
        organization=organization,
        value_fields=tuple(planned_values),
        source_file=curated.source_file,
        sheet_name=curated.sheet_name,
        source_context=tuple(
            {
                "source_row": cell.source_row,
                "source_column": cell.source_column,
                "value": cell.value,
            }
            for cell in curated.context_cells
        ),
        ignored_fields=tuple(sorted(ignored, key=lambda item: item.source_column)),
        unresolved_bindings=tuple(unresolved),
        evidence=tuple(evidence),
    )


def _plan_value_field(
    requested: ValueFieldBinding,
    request: ObservationStructuringRequest,
    curated: CuratedDataset,
    positions: Mapping[str, int],
    schemas: Mapping[str, Any],
    report_period: ResolvedBinding | None,
    table_unit: ResolvedBinding | None,
    schema: ObservationSchema,
    fatal: list[str],
) -> PlannedValueField:
    field = requested.value_field
    evidence: list[Evidence] = []
    reasons: list[str] = []
    if field not in positions:
        fatal.append(f"数值字段不存在：{field}")
        return PlannedValueField(
            field, 0, None, None, None, None, None, False, unresolved_reasons=("数值字段不存在",)
        )
    evidence.append(
        Evidence(
            "explicit_value_field" if request.value_bindings else "recognized_period_value_field",
            "MappingRequest" if request.value_bindings else "deterministic_rule",
            ("显式选择数值字段：" if request.value_bindings else "按明确字段名识别数值字段：")
            + field,
        )
    )
    if schemas[field].data_type not in {"integer", "number", "null"}:
        reasons.append(f"Data Schema 类型 {schemas[field].data_type} 不是可接受数值类型")
    name = _structural_key(field)
    period_basis = requested.period_basis or _AUTO_PERIOD_BASIS.get(name)
    if requested.period_basis:
        evidence.append(
            Evidence(
                "explicit_period_basis",
                "MappingRequest",
                f"显式绑定 period_basis：{requested.period_basis}",
            )
        )
    elif period_basis:
        evidence.append(
            Evidence(
                "period_basis_by_exact_header",
                "deterministic_rule",
                f"按明确字段名绑定 period_basis：{period_basis}",
            )
        )
    if period_basis not in schema.period_basis_values:
        reasons.append("period_basis 缺失或不属于当前 OntologyCatalog")
    period_key = _requested_scalar(
        requested.period_key, positions, "period_key", fatal
    ) or report_period
    if period_key is None:
        reasons.append("period_key 无法绑定")
    period_type = requested.period_type or request.report_period_type
    if name == "年初数" and requested.period_type is None:
        period_type = "YEAR"
        evidence.append(
            Evidence(
                "year_begin_anchor",
                "deterministic_rule",
                "年初数锚定报表期间所属年度，而不是月报月份",
            )
        )
    elif period_type is None and period_key is not None:
        period_type = _infer_period_type_for_table(curated, period_key)
    if period_type not in schema.period_type_values:
        reasons.append("period_type 缺失、冲突或不属于当前 ObservationSchema")
    business_scope = _requested_scalar(
        requested.business_scope, positions, "business_scope", fatal
    )
    if business_scope is None and name in _AUTO_PERIOD_BASIS:
        business_scope = ResolvedBinding(
            "business_scope",
            "constant",
            value="公司整体",
            evidence=(
                Evidence(
                    "whole_company_no_scope_dimension",
                    "deterministic_rule",
                    "值字段表达期间口径且未发现额外业务细分，使用公司整体",
                ),
            ),
        )
    if business_scope is None:
        reasons.append("business_scope 无法绑定；不会从业务范围列头猜测")
    unit = _requested_scalar(requested.unit, positions, "unit", fatal) or table_unit
    if unit is None:
        reasons.append("unit 存在冲突且无法唯一绑定")
    return PlannedValueField(
        field,
        positions[field],
        business_scope,
        period_type,
        period_key,
        period_basis,
        unit,
        not reasons,
        tuple(evidence),
        tuple(reasons),
    )


def _extract_metric_subjects(
    curated: CuratedDataset,
    request: ObservationStructuringRequest,
    plan: TableMappingPlan,
) -> tuple[MetricSubject, ...]:
    if plan.metric_name_field is None or plan.metric_name_source_column is None:
        return ()
    value_fields = {item.value_field for item in plan.value_fields}
    subjects: list[MetricSubject] = []
    current_group: str | None = None
    for row in sorted(curated.rows, key=lambda item: item.source_row):
        raw = row.values.get(plan.metric_name_field)
        if raw is None or not str(raw).strip():
            continue
        raw_label = str(raw)
        extracted_name, extraction_evidence = _extract_comparison_name(raw_label)
        row_role, role_evidence = _classify_row_role(
            raw_label,
            extracted_name,
            explicitly_confirmed=row.source_row in request.metric_row_hints,
        )
        context = {
            name: value
            for name, value in row.values.items()
            if name != plan.metric_name_field and name not in value_fields and value is not None
        }
        if current_group is not None and row_role not in {RowRole.GROUP, RowRole.NOTE}:
            context["group_label"] = current_group
        subject = MetricSubject(
            subject_id=_stable_id(
                "metric-subject",
                {
                    "curated_id": curated.curated_id,
                    "source_file": curated.source_file,
                    "sheet_name": curated.sheet_name,
                    "source_row": row.source_row,
                    "source_column": plan.metric_name_source_column,
                    "raw_label": raw_label,
                    "row_role": row_role.value,
                    "comparison_name": extracted_name,
                    "context": context,
                },
            ),
            raw_label=raw_label,
            row_role=row_role,
            comparison_name=extracted_name,
            comparison_key=comparison_key(extracted_name),
            source_file=curated.source_file,
            sheet_name=curated.sheet_name,
            source_row=row.source_row,
            source_column=plan.metric_name_source_column,
            context=context,
            evidence=(*extraction_evidence, role_evidence),
        )
        subjects.append(subject)
        if row_role is RowRole.GROUP:
            current_group = raw_label
    return tuple(subjects)


def _extract_comparison_name(raw_label: str) -> tuple[str, tuple[Evidence, ...]]:
    current = comparison_name(raw_label)
    evidence: list[Evidence] = []
    rules = (
        (_REPORT_NUMBERING, "report_numbering_removed", "移除明确的报表编号前缀"),
        (_HIERARCHY_PREFIX, "hierarchy_prefix_removed", "移除明确的报表层级前缀"),
        (_DISPLAY_MARKER, "display_marker_removed", "移除已确认的报表展示标记"),
    )
    changed = True
    while changed and current:
        changed = False
        for pattern, code, message in rules:
            updated, count = pattern.subn("", current, count=1)
            updated = comparison_name(updated)
            if count and updated and updated != current:
                evidence.append(
                    Evidence(code, "deterministic_rule", message, {"before": current, "after": updated})
                )
                current = updated
                changed = True
    updated, count = _DISPLAY_INSTRUCTION.subn("", current, count=1)
    updated = comparison_name(updated)
    if count and updated and updated != current:
        evidence.append(
            Evidence(
                "display_instruction_removed",
                "deterministic_rule",
                "移除明确的正负号填列展示说明",
                {"before": current, "after": updated},
            )
        )
        current = updated
    evidence.append(
        Evidence(
            "metric_comparison_name",
            "row_subject_extraction",
            "生成只用于 Metric 匹配的 comparison name；Curated 原值保持不变",
            {"raw_label": raw_label, "comparison_name": current},
        )
    )
    return current, tuple(evidence)


def marker_aware_comparison_name(raw_label: str) -> str:
    """Normalize report syntax while retaining a leading business marker."""

    current = comparison_name(raw_label)
    marker = ""
    changed = True
    while changed and current:
        changed = False
        updated, count = _REPORT_NUMBERING.subn("", current, count=1)
        updated = comparison_name(updated)
        if count and updated and updated != current:
            current = updated
            changed = True

        if not marker:
            match = _DISPLAY_MARKER.match(current)
            if match is not None:
                marker = "*" if match.group(1) == "＊" else match.group(1)
                current = comparison_name(current[match.end() :])
                changed = True

        updated, count = _HIERARCHY_PREFIX.subn("", current, count=1)
        updated = comparison_name(updated)
        if count and updated and updated != current:
            current = updated
            changed = True

    current = comparison_name(_DISPLAY_INSTRUCTION.sub("", current, count=1))
    return marker + current


def _classify_row_role(
    raw_label: str,
    extracted_name: str,
    *,
    explicitly_confirmed: bool,
) -> tuple[RowRole, Evidence]:
    if explicitly_confirmed:
        role, code = RowRole.METRIC, "confirmed_metric_subject"
        message = "显式 Metric override 或本体缺口确认将该行确认为指标主体"
    elif _NOTE_LABEL.search(comparison_name(raw_label)):
        role, code, message = RowRole.NOTE, "explicit_note_row", "按明确的“注:”标记识别为注释行"
    elif _GROUP_LABEL.search(extracted_name):
        role, code, message = RowRole.GROUP, "explicit_group_heading", "按明确的分类/类别/分组结尾识别为分组标题"
    elif extracted_name in _GENERIC_UNKNOWN_LABELS:
        role, code, message = RowRole.UNKNOWN, "generic_row_label", "标签过于宽泛，保留为 UNKNOWN 并继续进行保守匹配"
    else:
        role, code, message = RowRole.METRIC, "metric_field_row", "位于已确认指标名称字段且未命中明确非指标规则"
    return role, Evidence(
        code,
        "deterministic_rule",
        message,
        {"raw_label": raw_label, "comparison_name": extracted_name},
    )


def _project_drafts(
    curated: CuratedDataset,
    request: ObservationStructuringRequest,
    schema: ObservationSchema,
    plan: TableMappingPlan,
    subjects: tuple[MetricSubject, ...],
) -> tuple[tuple[ObservationDraft, ...], list[Mapping[str, Any]], int]:
    rows = {row.source_row: row for row in curated.rows}
    drafts: list[ObservationDraft] = []
    unprojected: list[Mapping[str, Any]] = []
    draft_ids: set[str] = set()
    empty_rows = 0
    for subject in subjects:
        if subject.row_role in {RowRole.GROUP, RowRole.NOTE}:
            continue
        row = rows[subject.source_row]
        row_had_value = False
        for value_plan in plan.value_fields:
            raw_value = row.values.get(value_plan.value_field)
            if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                continue
            row_had_value = True
            if not value_plan.binding_complete:
                unprojected.append(_unprojected(row, value_plan, "value_binding_unresolved", raw_value))
                continue
            if not _acceptable_number(raw_value):
                unprojected.append(_unprojected(row, value_plan, "value_not_numeric", raw_value))
                continue
            if plan.organization is None:
                unprojected.append(_unprojected(row, value_plan, "organization_binding_unresolved", raw_value))
                continue
            scope = _resolve_binding(value_plan.business_scope, row)
            if scope is None or not str(scope).strip():
                unprojected.append(_unprojected(row, value_plan, "business_scope_missing", raw_value))
                continue
            period_raw = _resolve_binding(value_plan.period_key, row)
            parsed_period = _parse_period(period_raw, value_plan.period_type)
            if parsed_period is None:
                unprojected.append(_unprojected(row, value_plan, "period_key_invalid", raw_value))
                continue
            period_type, period_key = parsed_period
            organization_raw = _resolve_binding(plan.organization, row)
            organization_value = (
                str(organization_raw).strip()
                if organization_raw is not None and str(organization_raw).strip()
                else None
            )
            unit_value = _resolve_binding(value_plan.unit, row)
            unit_raw = (
                str(unit_value).strip()
                if unit_value is not None and str(unit_value).strip()
                else None
            )
            unit_normalized = _normalize_unit(unit_raw, schema.unit_values)
            identity = {
                "curated_id": curated.curated_id,
                "raw_dataset_id": curated.raw_dataset_id,
                "raw_version_id": curated.raw_version_id,
                "source_file": curated.source_file,
                "sheet_name": curated.sheet_name,
                "source_row": row.source_row,
                "value_source_column": value_plan.source_column,
                "metric_subject_id": subject.subject_id,
                "organization_value": organization_value,
                "organization_id": request.organization_id,
                "business_scope": str(scope).strip(),
                "period_type": period_type,
                "period_key": period_key,
                "period_basis": value_plan.period_basis,
                "unit_raw": unit_raw,
                "unit_normalized": unit_normalized,
                "structuring_rule_version": request.structuring_rule_version,
            }
            draft_id = _stable_id("observation-draft", identity)
            if draft_id in draft_ids:
                unprojected.append(_unprojected(row, value_plan, "duplicate_draft", raw_value))
                continue
            draft_ids.add(draft_id)
            binding_evidence = [*value_plan.evidence]
            for binding in (
                plan.organization,
                value_plan.business_scope,
                value_plan.period_key,
                value_plan.unit,
            ):
                if binding:
                    binding_evidence.extend(binding.evidence)
            role_bindings = tuple(
                binding
                for binding in (
                    ResolvedBinding("metric_name", "field", field=plan.metric_name_field),
                    ResolvedBinding(
                        "actual_value",
                        "field",
                        field=value_plan.value_field,
                        evidence=value_plan.evidence,
                    ),
                    plan.organization,
                    value_plan.business_scope,
                    ResolvedBinding("period_type", "resolved", value=period_type),
                    value_plan.period_key,
                    ResolvedBinding(
                        "period_basis",
                        "resolved",
                        value=value_plan.period_basis,
                        evidence=value_plan.evidence,
                    ),
                    value_plan.unit,
                )
                if binding is not None
            )
            drafts.append(
                ObservationDraft(
                    observation_draft_id=draft_id,
                    metric_subject_id=subject.subject_id,
                    metric_name=subject.raw_label,
                    actual_value=raw_value,
                    business_scope=str(scope).strip(),
                    organization_value=organization_value,
                    organization_id=request.organization_id,
                    period_type=period_type,
                    period_key=period_key,
                    period_basis=str(value_plan.period_basis),
                    unit_raw=unit_raw,
                    unit_normalized=unit_normalized,
                    source=f"{curated.source_file}#{curated.sheet_name}",
                    source_file=curated.source_file,
                    sheet_name=curated.sheet_name,
                    source_row=row.source_row,
                    metric_source_column=subject.source_column,
                    value_source_column=value_plan.source_column,
                    value_field=value_plan.value_field,
                    curated_id=curated.curated_id,
                    raw_dataset_id=curated.raw_dataset_id,
                    raw_version_id=curated.raw_version_id,
                    structuring_rule_version=request.structuring_rule_version,
                    role_bindings=role_bindings,
                    binding_evidence=tuple(binding_evidence),
                )
            )
        if not row_had_value:
            empty_rows += 1
    return tuple(drafts), unprojected, empty_rows


def _schema_errors(schema: ObservationSchema) -> list[str]:
    errors = []
    if not schema.fingerprint.strip():
        errors.append("ObservationSchema.fingerprint 不能为空")
    missing_basis = set(_AUTO_PERIOD_BASIS.values()).difference(schema.period_basis_values)
    if missing_basis:
        errors.append("ObservationSchema 缺少 period_basis：" + ", ".join(sorted(missing_basis)))
    fields = {item.name: item for item in schema.actual_observation_fields}
    required_shapes = {
        "organization_id": ("reference", "Organization.id"),
        "metric_id": ("reference", "Metric.id"),
        "period": ("struct", "Period"),
        "actual_value": ("number", None),
        "unit": ("enum", "Unit"),
        "status": ("enum", "Status"),
    }
    for name, shape in required_shapes.items():
        field = fields.get(name)
        if field is None or (field.value_type, field.target) != shape:
            errors.append(f"ObservationSchema.{name} 结构不符合 ActualObservation 投影")
    return errors


def _curated_contract_errors(curated: CuratedDataset) -> list[str]:
    errors = []
    if not curated.curated_id.strip():
        errors.append("CuratedDataset.curated_id 不能为空")
    if not curated.raw_dataset_id.strip() or not curated.raw_version_id.strip():
        errors.append("CuratedDataset 必须绑定 Raw dataset/version")
    header_fields = [item.normalized_name for item in curated.header_mapping]
    schema_fields = [item.normalized_name for item in curated.data_schema.columns]
    if len(header_fields) != len(set(header_fields)):
        errors.append("CuratedDataset header_mapping 存在重复键")
    if header_fields != schema_fields:
        errors.append("CuratedDataset header_mapping 与 DataSchema 列不一致")
    source_rows = [row.source_row for row in curated.rows]
    if len(source_rows) != len(set(source_rows)):
        errors.append("CuratedDataset source_row 必须唯一")
    expected = set(header_fields)
    if any(set(row.values) != expected for row in curated.rows):
        errors.append("CuratedDataset 行键必须与 DataSchema 完全一致")
    return errors


def _choose_table_scalar(
    requested: ScalarBinding | None,
    fields: tuple[str, ...],
    candidates: set[str],
    role: str,
    label: str,
    evidence: list[Evidence],
    fatal: list[str],
    *,
    required: bool = True,
) -> ResolvedBinding | None:
    positions = {field: index for index, field in enumerate(fields, start=1)}
    if requested is not None:
        resolved = _requested_scalar(requested, positions, role, fatal)
        if resolved:
            evidence.extend(resolved.evidence)
        return resolved
    matches = [field for field in fields if _structural_key(field) in candidates]
    if len(matches) == 1:
        resolved = ResolvedBinding(
            role,
            "field",
            field=matches[0],
            evidence=(
                Evidence(
                    f"unique_{role}_field",
                    "deterministic_rule",
                    f"唯一明确的{label}字段：{matches[0]}",
                ),
            ),
        )
        evidence.extend(resolved.evidence)
        return resolved
    if len(matches) > 1 or required:
        return None
    return None


def _requested_scalar(
    requested: ScalarBinding | None,
    positions: Mapping[str, int],
    role: str,
    fatal: list[str],
) -> ResolvedBinding | None:
    if requested is None:
        return None
    has_constant = requested.constant is not None
    has_field = requested.field is not None
    if has_constant == has_field:
        fatal.append(f"{role} binding 必须且只能提供 constant 或 field")
        return None
    if has_field:
        assert requested.field is not None
        if requested.field not in positions:
            fatal.append(f"{role} binding 字段不存在：{requested.field}")
            return None
        return ResolvedBinding(
            role,
            "field",
            field=requested.field,
            evidence=(
                Evidence(
                    f"explicit_{role}_field",
                    "MappingRequest",
                    f"显式绑定 {role} 来源字段：{requested.field}",
                ),
            ),
        )
    if isinstance(requested.constant, str) and not requested.constant.strip():
        fatal.append(f"{role} binding constant 不能为空字符串")
        return None
    return ResolvedBinding(
        role,
        "constant",
        value=requested.constant,
        evidence=(
            Evidence(
                f"explicit_{role}_constant",
                "MappingRequest",
                f"显式绑定 {role} 常量",
                {"value": requested.constant},
            ),
        ),
    )


def _unit_from_context(curated: CuratedDataset) -> tuple[ResolvedBinding | None, bool]:
    matches = []
    for cell in curated.context_cells:
        if isinstance(cell.value, str):
            match = re.fullmatch(r"\s*单位\s*[：:]\s*(\S+)\s*", cell.value)
            if match:
                matches.append((match.group(1), cell.source_row, cell.source_column))
    values = {value for value, _, _ in matches}
    if len(values) != 1:
        return None, len(values) > 1
    value, source_row, source_column = matches[0]
    return (
        ResolvedBinding(
            "unit",
            "context",
            value=value,
            evidence=(
                Evidence(
                    "unit_context_cell",
                    "CuratedDataset.context_cells",
                    "从表头前上下文的明确单位标记提取单位",
                    {"source_row": source_row, "source_column": source_column},
                ),
            ),
        ),
        False,
    )


def _infer_period_type_for_table(
    curated: CuratedDataset, binding: ResolvedBinding
) -> str | None:
    if binding.kind == "constant":
        parsed = _parse_period(binding.value, None)
        return parsed[0] if parsed else None
    if binding.kind != "field" or binding.field is None:
        return None
    inferred = {
        parsed[0]
        for row in curated.rows
        if (parsed := _parse_period(row.values.get(binding.field), None)) is not None
    }
    return next(iter(inferred)) if len(inferred) == 1 else None


def _resolve_binding(binding: ResolvedBinding | None, row: CuratedRow) -> Any:
    if binding is None or binding.kind == "missing":
        return None
    if binding.kind == "field":
        return row.values.get(binding.field) if binding.field else None
    return binding.value


def _parse_period(value: Any, expected_type: str | None) -> tuple[str, str] | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    year_match = re.fullmatch(r"(\d{4})(?:年)?", text)
    month_match = re.fullmatch(r"(\d{4})\s*(?:年|[-/])\s*(\d{1,2})(?:月)?", text)
    quarter_match = re.fullmatch(r"(\d{4})\s*(?:年|[-/]?)\s*[Qq第]?([1-4])(?:季度)?", text)
    if expected_type == "YEAR":
        match = year_match or month_match or quarter_match
        return ("YEAR", match.group(1)) if match else None
    if expected_type == "MONTH":
        if not month_match:
            return None
        month = int(month_match.group(2))
        return ("MONTH", f"{month_match.group(1)}-{month:02d}") if 1 <= month <= 12 else None
    if expected_type == "QUARTER":
        return (
            ("QUARTER", f"{quarter_match.group(1)}-Q{quarter_match.group(2)}")
            if quarter_match
            else None
        )
    if expected_type is not None:
        return None
    if month_match:
        month = int(month_match.group(2))
        if 1 <= month <= 12:
            return "MONTH", f"{month_match.group(1)}-{month:02d}"
    if quarter_match:
        return "QUARTER", f"{quarter_match.group(1)}-Q{quarter_match.group(2)}"
    if year_match:
        return "YEAR", year_match.group(1)
    return None


def _normalize_unit(raw: str | None, units: Mapping[str, str]) -> str | None:
    if raw is None:
        return None
    if raw in units:
        return raw
    matches = [key for key, display in units.items() if display == raw]
    return matches[0] if len(matches) == 1 else None


def _acceptable_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _structural_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).strip())


def _unprojected(
    row: CuratedRow,
    value_plan: PlannedValueField,
    reason: str,
    value: Any,
) -> Mapping[str, Any]:
    return {
        "source_row": row.source_row,
        "value_field": value_plan.value_field,
        "source_column": value_plan.source_column,
        "value": value,
        "reason": reason,
    }


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


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
