"""Phase 2 指标在行 Curated 到观测候选的确定性只读 Mapping。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .contracts import CuratedDataset, CuratedRow
from .mapping_contracts import (
    Evidence,
    IgnoredField,
    MappingPlan,
    MappingReport,
    MappingRequest,
    MappingResult,
    MetricDecision,
    MetricMatchStatus,
    MetricSubject,
    ObservationCandidate,
    OntologyCatalog,
    OntologyMetric,
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


def map_curated_dataset(
    curated: CuratedDataset,
    request: MappingRequest,
    catalog: OntologyCatalog,
) -> MappingResult:
    """建立表级计划、指标决策和 0～N 条来源候选，不产生写入副作用。"""

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
    table_plan = _build_table_plan(curated, request, catalog)

    row_subjects: tuple[MetricSubject, ...] = ()
    decisions: tuple[MetricDecision, ...] = ()
    candidates: tuple[ObservationCandidate, ...] = ()
    unprojected: list[Mapping[str, Any]] = []
    empty_rows = 0
    if table_plan.structure_status is not StructureStatus.BLOCKED:
        row_subjects = _extract_metric_subjects(curated, request, table_plan)
        decisions = _build_metric_decisions(row_subjects, request, catalog)
        candidates, unprojected, empty_rows = _project_candidates(
            curated, request, catalog, table_plan, decisions
        )

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
        warnings.append(
            "OntologyCatalog 当前没有 Organization 实例；不会伪造 organization_id"
        )
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
        unprojected_values=tuple(unprojected),
        ignored_fields=table_plan.ignored_fields,
        unresolved_bindings=table_plan.unresolved_bindings,
        constraint_issue_counts=constraint_counts,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return MappingResult(plan=plan, report=report)


def comparison_name(value: str) -> str:
    """只用于证据展示：NFKC、首尾及连续空白归一。"""

    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def comparison_key(value: str) -> str:
    """受限比较键；不删词、不做包含或同义词匹配。"""

    normalized = comparison_name(value).translate(_PUNCTUATION_TRANSLATION).casefold()
    normalized = re.sub(r"\s*([,.:;()\-])\s*", r"\1", normalized)
    return normalized


def _build_table_plan(
    curated: CuratedDataset,
    request: MappingRequest,
    catalog: OntologyCatalog,
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
    fatal.extend(_catalog_errors(catalog))
    if request.curated_id != curated.curated_id:
        fatal.append(
            f"MappingRequest.curated_id 与输入不一致：{request.curated_id}"
        )
    if not request.mapping_rule_version.strip():
        fatal.append("mapping_rule_version 不能为空")
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
                UnresolvedBinding(
                    field=None,
                    role="metric_name",
                    reason="存在多个合理的指标名称字段",
                )
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
        unresolved.append(
            UnresolvedBinding(
                field=None,
                role="organization",
                reason="无法唯一绑定组织来源",
            )
        )

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
                    code="unit_missing_reported",
                    source="table_plan",
                    message="没有可提取的原始单位；候选将保留单位约束缺口",
                ),
            ),
        )

    requested_value_fields: list[ValueFieldBinding]
    if request.value_bindings:
        requested_value_fields = list(request.value_bindings)
    else:
        requested_value_fields = [
            ValueFieldBinding(value_field=field)
            for field in fields
            if _structural_key(field) in _AUTO_PERIOD_BASIS
        ]

    duplicates = _duplicates(binding.value_field for binding in requested_value_fields)
    if duplicates:
        fatal.append("数值字段 binding 重复：" + ", ".join(duplicates))

    planned_values: list[PlannedValueField] = []
    for requested in requested_value_fields:
        planned_values.append(
            _plan_value_field(
                requested,
                request,
                curated,
                positions,
                schemas,
                report_period,
                table_unit,
                catalog,
                fatal,
            )
        )
    if not planned_values and not fatal:
        unresolved.append(
            UnresolvedBinding(
                field=None,
                role="actual_value",
                reason="没有可唯一识别的数值字段",
            )
        )

    explicitly_ignored = set()
    for field, reason in request.ignored_fields.items():
        if field not in positions:
            fatal.append(f"ignored field 不存在：{field}")
            continue
        if not str(reason).strip():
            fatal.append(f"ignored field 缺少原因：{field}")
            continue
        explicitly_ignored.add(field)
        ignored.append(
            IgnoredField(field=field, source_column=positions[field], reason=str(reason))
        )

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
        schema = schemas[field]
        if name in _AUXILIARY_FIELDS:
            ignored.append(
                IgnoredField(
                    field=field,
                    source_column=positions[field],
                    reason="已识别为辅助/行定位字段，不参与观测投影",
                )
            )
        elif any(term in name for term in _COMPARISON_TERMS):
            ignored.append(
                IgnoredField(
                    field=field,
                    source_column=positions[field],
                    reason="比较语义超出 Phase 2 范围",
                    disposition="out-of-scope",
                )
            )
        elif schema.data_type == "null":
            ignored.append(
                IgnoredField(
                    field=field,
                    source_column=positions[field],
                    reason="当前 Curated 中该列全空，未作为值字段猜测",
                )
            )
        elif schema.data_type in {"integer", "number"}:
            unresolved.append(
                UnresolvedBinding(
                    field=field,
                    role="actual_value",
                    reason="数值列语义无法由受限规则唯一确定",
                )
            )
        else:
            ignored.append(
                IgnoredField(
                    field=field,
                    source_column=positions[field],
                    reason="非数值且未承担本次 Mapping 结构角色",
                    disposition="out-of-scope",
                )
            )

    contradictory_fields = sorted(used_fields.intersection(explicitly_ignored))
    if contradictory_fields:
        fatal.append(
            "字段不能同时承担 Mapping 角色和被 ignored："
            + ", ".join(contradictory_fields)
        )

    for planned in planned_values:
        unresolved.extend(
            UnresolvedBinding(
                field=planned.value_field,
                role="value_field",
                reason=reason,
            )
            for reason in planned.unresolved_reasons
        )

    metric_subject_rows = {
        item.source_row
        for item in curated.rows
        if metric_field is not None
        and item.values.get(metric_field) is not None
        and str(item.values.get(metric_field)).strip()
    }
    for row, current_metric_id in request.metric_overrides.items():
        if row not in metric_subject_rows:
            fatal.append(f"Metric override 未指向有效指标主体：source_row={row}")
        if catalog.metric_by_id(current_metric_id) is None:
            fatal.append(
                f"source_row={row} 的 Metric override 不存在于当前 ontology_revision："
                f"{current_metric_id}"
            )
    conflicting_rows = sorted(
        set(request.metric_overrides).intersection(request.ontology_gap_confirmations)
    )
    if conflicting_rows:
        fatal.append(
            "同一指标主体不能同时 Metric override 和确认 ontology gap："
            + ", ".join(map(str, conflicting_rows))
        )
    missing_gap_rows = sorted(
        set(request.ontology_gap_confirmations).difference(metric_subject_rows)
    )
    if missing_gap_rows:
        fatal.append(
            "ontology gap confirmation 未指向有效指标主体："
            + ", ".join(map(str, missing_gap_rows))
        )
    if (
        request.organization_current_id is not None
        and request.organization_current_id not in catalog.organization_ids
    ):
        fatal.append(
            "organization_current_id 不存在于当前 ontology_revision："
            + request.organization_current_id
        )

    if fatal:
        status = StructureStatus.BLOCKED
        unresolved.extend(
            UnresolvedBinding(field=None, role="input", reason=message)
            for message in fatal
        )
    elif unresolved:
        status = StructureStatus.NEEDS_BINDING
    else:
        status = StructureStatus.READY

    plan = TableMappingPlan(
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
    return plan


def _plan_value_field(
    requested: ValueFieldBinding,
    request: MappingRequest,
    curated: CuratedDataset,
    positions: Mapping[str, int],
    schemas: Mapping[str, Any],
    report_period: ResolvedBinding | None,
    table_unit: ResolvedBinding | None,
    catalog: OntologyCatalog,
    fatal: list[str],
) -> PlannedValueField:
    field = requested.value_field
    evidence: list[Evidence] = []
    reasons: list[str] = []
    if field not in positions:
        fatal.append(f"数值字段不存在：{field}")
        return PlannedValueField(
            value_field=field,
            source_column=0,
            business_scope=None,
            period_type=None,
            period_key=None,
            period_basis=None,
            unit=None,
            binding_complete=False,
            unresolved_reasons=("数值字段不存在",),
        )

    if request.value_bindings:
        evidence.append(
            Evidence(
                code="explicit_value_field",
                source="MappingRequest",
                message=f"显式选择数值字段：{field}",
            )
        )
    else:
        evidence.append(
            Evidence(
                code="recognized_period_value_field",
                source="deterministic_rule",
                message=f"按明确字段名识别数值字段：{field}",
            )
        )

    schema_type = schemas[field].data_type
    if schema_type not in {"integer", "number", "null"}:
        reasons.append(f"Data Schema 类型 {schema_type} 不是可接受数值类型")

    name = _structural_key(field)
    period_basis = requested.period_basis or _AUTO_PERIOD_BASIS.get(name)
    if requested.period_basis:
        evidence.append(
            Evidence(
                code="explicit_period_basis",
                source="MappingRequest",
                message=f"显式绑定 period_basis：{requested.period_basis}",
            )
        )
    elif period_basis:
        evidence.append(
            Evidence(
                code="period_basis_by_exact_header",
                source="deterministic_rule",
                message=f"按明确字段名绑定 period_basis：{period_basis}",
            )
        )
    if period_basis not in catalog.period_basis_values:
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
                code="year_begin_anchor",
                source="deterministic_rule",
                message="年初数锚定报表期间所属年度，而不是月报月份",
            )
        )
    elif period_type is None and period_key is not None:
        period_type = _infer_period_type_for_table(curated, period_key)
    if period_type not in catalog.period_type_values:
        reasons.append("period_type 缺失、冲突或不属于当前 OntologyCatalog")

    business_scope = _requested_scalar(
        requested.business_scope, positions, "business_scope", fatal
    )
    if business_scope is None and name in _AUTO_PERIOD_BASIS:
        business_scope = ResolvedBinding(
            role="business_scope",
            kind="constant",
            value="公司整体",
            evidence=(
                Evidence(
                    code="whole_company_no_scope_dimension",
                    source="deterministic_rule",
                    message="值字段表达期间口径且未发现额外业务细分，使用公司整体",
                ),
            ),
        )
    if business_scope is None:
        reasons.append("business_scope 无法绑定；不会从业务范围列头猜测")

    unit = _requested_scalar(requested.unit, positions, "unit", fatal) or table_unit
    if unit is None:
        reasons.append("unit 存在冲突且无法唯一绑定")
    return PlannedValueField(
        value_field=field,
        source_column=positions[field],
        business_scope=business_scope,
        period_type=period_type,
        period_key=period_key,
        period_basis=period_basis,
        unit=unit,
        binding_complete=not reasons,
        evidence=tuple(evidence),
        unresolved_reasons=tuple(reasons),
    )


def _extract_metric_subjects(
    curated: CuratedDataset,
    request: MappingRequest,
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
            explicitly_confirmed=(
                row.source_row in request.metric_overrides
                or row.source_row in request.ontology_gap_confirmations
            ),
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


def _build_metric_decisions(
    subjects: tuple[MetricSubject, ...],
    request: MappingRequest,
    catalog: OntologyCatalog,
) -> tuple[MetricDecision, ...]:
    decisions: list[MetricDecision] = []
    for subject in subjects:
        if subject.row_role in {RowRole.GROUP, RowRole.NOTE}:
            continue
        decisions.append(_match_metric(subject, request, catalog))
    return tuple(decisions)


def _extract_comparison_name(raw_label: str) -> tuple[str, tuple[Evidence, ...]]:
    """仅剥离可确定的报表展示结构，不改写 Curated 原值。"""

    current = comparison_name(raw_label)
    evidence: list[Evidence] = []
    rules: tuple[tuple[re.Pattern[str], str, str], ...] = (
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
                    Evidence(
                        code=code,
                        source="deterministic_rule",
                        message=message,
                        details={"before": current, "after": updated},
                    )
                )
                current = updated
                changed = True
    updated, count = _DISPLAY_INSTRUCTION.subn("", current, count=1)
    updated = comparison_name(updated)
    if count and updated and updated != current:
        evidence.append(
            Evidence(
                code="display_instruction_removed",
                source="deterministic_rule",
                message="移除明确的正负号填列展示说明",
                details={"before": current, "after": updated},
            )
        )
        current = updated
    evidence.append(
        Evidence(
            code="metric_comparison_name",
            source="row_subject_extraction",
            message="生成只用于 Metric 匹配的 comparison name；Curated 原值保持不变",
            details={"raw_label": raw_label, "comparison_name": current},
        )
    )
    return current, tuple(evidence)


def _classify_row_role(
    raw_label: str,
    extracted_name: str,
    *,
    explicitly_confirmed: bool,
) -> tuple[RowRole, Evidence]:
    if explicitly_confirmed:
        role = RowRole.METRIC
        code = "confirmed_metric_subject"
        message = "显式 Metric override 或本体缺口确认将该行确认为指标主体"
    elif _NOTE_LABEL.search(comparison_name(raw_label)):
        role = RowRole.NOTE
        code = "explicit_note_row"
        message = "按明确的“注:”标记识别为注释行"
    elif _GROUP_LABEL.search(extracted_name):
        role = RowRole.GROUP
        code = "explicit_group_heading"
        message = "按明确的分类/类别/分组结尾识别为分组标题"
    elif extracted_name in _GENERIC_UNKNOWN_LABELS:
        role = RowRole.UNKNOWN
        code = "generic_row_label"
        message = "标签过于宽泛，保留为 UNKNOWN 并继续进行保守匹配"
    else:
        role = RowRole.METRIC
        code = "metric_field_row"
        message = "位于已确认指标名称字段且未命中明确非指标规则"
    return role, Evidence(
        code=code,
        source="deterministic_rule",
        message=message,
        details={"raw_label": raw_label, "comparison_name": extracted_name},
    )


def _match_metric(
    subject: MetricSubject,
    request: MappingRequest,
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


def _project_candidates(
    curated: CuratedDataset,
    request: MappingRequest,
    catalog: OntologyCatalog,
    plan: TableMappingPlan,
    decisions: tuple[MetricDecision, ...],
) -> tuple[tuple[ObservationCandidate, ...], list[Mapping[str, Any]], int]:
    rows = {row.source_row: row for row in curated.rows}
    candidates: list[ObservationCandidate] = []
    unprojected: list[Mapping[str, Any]] = []
    candidate_ids: set[str] = set()
    empty_rows = 0
    for decision in decisions:
        row = rows[decision.subject.source_row]
        row_had_value = False
        for value_plan in plan.value_fields:
            raw_value = row.values.get(value_plan.value_field)
            if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                continue
            row_had_value = True
            if not value_plan.binding_complete:
                unprojected.append(
                    _unprojected(row, value_plan, "value_binding_unresolved", raw_value)
                )
                continue
            if not _acceptable_number(raw_value):
                unprojected.append(
                    _unprojected(row, value_plan, "value_not_numeric", raw_value)
                )
                continue

            if plan.organization is None:
                unprojected.append(
                    _unprojected(
                        row,
                        value_plan,
                        "organization_binding_unresolved",
                        raw_value,
                    )
                )
                continue

            scope = _resolve_binding(value_plan.business_scope, row)
            if scope is None or not str(scope).strip():
                unprojected.append(
                    _unprojected(row, value_plan, "business_scope_missing", raw_value)
                )
                continue
            period_raw = _resolve_binding(value_plan.period_key, row)
            parsed_period = _parse_period(period_raw, value_plan.period_type)
            if parsed_period is None:
                unprojected.append(
                    _unprojected(row, value_plan, "period_key_invalid", raw_value)
                )
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
            unit_normalized = _normalize_unit(unit_raw, catalog.unit_values)
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
            if unit_normalized is None:
                missing.append("unit")
            missing.append("status")

            identity = {
                "curated_id": curated.curated_id,
                "raw_dataset_id": curated.raw_dataset_id,
                "raw_version_id": curated.raw_version_id,
                "source_file": curated.source_file,
                "sheet_name": curated.sheet_name,
                "source_row": row.source_row,
                "value_source_column": value_plan.source_column,
                "organization_value": organization_value,
                "organization_current_id": request.organization_current_id,
                "ontology_revision": catalog.ontology_revision,
                "metric_subject_id": decision.subject.subject_id,
                "current_metric_id": current_metric_id,
                "business_scope": str(scope).strip(),
                "period_type": period_type,
                "period_key": period_key,
                "period_basis": value_plan.period_basis,
                "unit_raw": unit_raw,
                "unit_normalized": unit_normalized,
                "mapping_rule_version": request.mapping_rule_version,
            }
            candidate_id = _stable_id("observation-candidate", identity)
            if candidate_id in candidate_ids:
                unprojected.append(
                    _unprojected(row, value_plan, "duplicate_candidate", raw_value)
                )
                continue
            candidate_ids.add(candidate_id)
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
                    ResolvedBinding(
                        role="metric_name",
                        kind="field",
                        field=plan.metric_name_field,
                    ),
                    ResolvedBinding(
                        role="actual_value",
                        kind="field",
                        field=value_plan.value_field,
                        evidence=value_plan.evidence,
                    ),
                    plan.organization,
                    value_plan.business_scope,
                    ResolvedBinding(
                        role="period_type",
                        kind="resolved",
                        value=period_type,
                    ),
                    value_plan.period_key,
                    ResolvedBinding(
                        role="period_basis",
                        kind="resolved",
                        value=value_plan.period_basis,
                        evidence=value_plan.evidence,
                    ),
                    value_plan.unit,
                )
                if binding is not None
            )
            candidates.append(
                ObservationCandidate(
                    candidate_id=candidate_id,
                    candidate_identity_kind="SOURCE_MAPPING_CANDIDATE",
                    metric_decision_id=decision.decision_id,
                    metric_name=decision.subject.raw_label,
                    ontology_revision=catalog.ontology_revision,
                    current_metric_id=current_metric_id,
                    actual_value=raw_value,
                    business_scope=str(scope).strip(),
                    organization_value=organization_value,
                    organization_current_id=request.organization_current_id,
                    period_type=period_type,
                    period_key=period_key,
                    period_basis=str(value_plan.period_basis),
                    unit_raw=unit_raw,
                    unit_normalized=unit_normalized,
                    source=f"{curated.source_file}#{curated.sheet_name}",
                    source_file=curated.source_file,
                    sheet_name=curated.sheet_name,
                    source_row=row.source_row,
                    metric_source_column=decision.subject.source_column,
                    value_source_column=value_plan.source_column,
                    value_field=value_plan.value_field,
                    curated_id=curated.curated_id,
                    raw_dataset_id=curated.raw_dataset_id,
                    raw_version_id=curated.raw_version_id,
                    mapping_rule_version=request.mapping_rule_version,
                    role_bindings=role_bindings,
                    binding_evidence=tuple(binding_evidence),
                    definition_constraints_satisfied=not missing,
                    instantiation_missing_fields=tuple(missing),
                )
            )
        if not row_had_value:
            empty_rows += 1
    return tuple(candidates), unprojected, empty_rows


def _catalog_errors(catalog: OntologyCatalog) -> list[str]:
    errors = []
    if not catalog.ontology_revision.strip():
        errors.append("OntologyCatalog.ontology_revision 不能为空")
    missing_basis = set(_AUTO_PERIOD_BASIS.values()).difference(
        catalog.period_basis_values
    )
    if missing_basis:
        errors.append("OntologyCatalog 缺少 period_basis：" + ", ".join(sorted(missing_basis)))
    metric_ids = [metric.current_metric_id for metric in catalog.metrics]
    if len(metric_ids) != len(set(metric_ids)):
        errors.append("OntologyCatalog 存在重复 current_metric_id")
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
            role=role,
            kind="field",
            field=matches[0],
            evidence=(
                Evidence(
                    code=f"unique_{role}_field",
                    source="deterministic_rule",
                    message=f"唯一明确的{label}字段：{matches[0]}",
                ),
            ),
        )
        evidence.extend(resolved.evidence)
        return resolved
    if len(matches) > 1:
        return None
    elif required:
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
            role=role,
            kind="field",
            field=requested.field,
            evidence=(
                Evidence(
                    code=f"explicit_{role}_field",
                    source="MappingRequest",
                    message=f"显式绑定 {role} 来源字段：{requested.field}",
                ),
            ),
        )
    if isinstance(requested.constant, str) and not requested.constant.strip():
        fatal.append(f"{role} binding constant 不能为空字符串")
        return None
    return ResolvedBinding(
        role=role,
        kind="constant",
        value=requested.constant,
        evidence=(
            Evidence(
                code=f"explicit_{role}_constant",
                source="MappingRequest",
                message=f"显式绑定 {role} 常量",
                details={"value": requested.constant},
            ),
        ),
    )


def _unit_from_context(
    curated: CuratedDataset,
) -> tuple[ResolvedBinding | None, bool]:
    matches = []
    for cell in curated.context_cells:
        if not isinstance(cell.value, str):
            continue
        match = re.fullmatch(r"\s*单位\s*[：:]\s*(\S+)\s*", cell.value)
        if match:
            matches.append((match.group(1), cell.source_row, cell.source_column))
    values = {value for value, _, _ in matches}
    if len(values) != 1:
        return None, len(values) > 1
    value, source_row, source_column = matches[0]
    return (
        ResolvedBinding(
            role="unit",
            kind="context",
            value=value,
            evidence=(
                Evidence(
                    code="unit_context_cell",
                    source="CuratedDataset.context_cells",
                    message="从表头前上下文的明确单位标记提取单位",
                    details={"source_row": source_row, "source_column": source_column},
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
        if not 1 <= month <= 12:
            return None
        return "MONTH", f"{month_match.group(1)}-{month:02d}"
    if expected_type == "QUARTER":
        if not quarter_match:
            return None
        return "QUARTER", f"{quarter_match.group(1)}-Q{quarter_match.group(2)}"
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


def _value_semantics_conflict(raw_name: str, metric: OntologyMetric) -> bool:
    name = comparison_name(raw_name)
    source_is_ratio = name.endswith(("率", "比例", "比率")) or "%" in name
    return source_is_ratio and metric.value_semantics == "NUMBER"


def _structural_key(value: str) -> str:
    """仅用于已知结构列名；业务 Metric 名称绝不使用删空白规则。"""

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
