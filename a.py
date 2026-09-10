"""Phase 1 / Phase 2 / Phase 2.5 个人测试入口。

默认面向 PyCharm 右键运行：修改“PyCharm 右键运行配置”中的常量即可。
Phase 2.5 会先完整保留 Phase 2 结果，再对选定的未决 Metric 运行候选召回和
DeepSeek 语义判断；所有模型结论保持 PROPOSED，不自动确认或修改本体。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# 固定为 UTF-8，避免 Windows 控制台或重定向输出出现中文乱码。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 允许直接用 Python 运行本文件，不要求事先把当前项目安装到环境中。
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_mapper import (  # noqa: E402
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    COST_EXPENSE_STATEMENT,
    ExecutionStatus,
    MappingRequest,
    DeepSeekSemanticJudge,
    JudgeUnavailableError,
    OntologyCatalogError,
    PROFIT_STATEMENT,
    Phase1Error,
    Phase25ReviewError,
    PipelineConfig,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
    ScalarBinding,
    StructureStatus,
    SemanticStatus,
    ValueFieldBinding,
    build_effective_mapping_view,
    build_gold_review_draft,
    build_ontology_change_proposal,
    curate_file,
    evaluate_retrieval_on_gold,
    export_gold_review_draft,
    load_gold_review_payload,
    load_ontology_catalog,
    map_curated_dataset,
    retrieve_candidates_for_decision,
    run_semantic_judgment,
    run_semantic_pilot_on_gold,
    validate_gold_review_file,
    validate_gold_review_payload,
)


# ===== PyCharm 右键运行配置：通常只需要修改这里 =====
DEFAULT_TEST_PATH = (
    PROJECT_ROOT / "reports" / "一级子公司A_利润表_2025-01.xlsx"
)
DEFAULT_PHASE = "2.5"
DEFAULT_DATASETS_ONLY = True
DEFAULT_PREVIEW_ROWS = 5
DEFAULT_ALL_ROWS = False

DEFAULT_DEFINITION_PATH = PROJECT_ROOT / "ontology" / "Definition.json"
DEFAULT_KNOWLEDGE_PATH = PROJECT_ROOT / "ontology" / "Knowledge.json"
DEFAULT_METRIC_NAME_FIELD: str | None = None
DEFAULT_ORGANIZATION: str | None = None
DEFAULT_ORGANIZATION_CURRENT_ID: str | None = None
DEFAULT_REPORT_PERIOD_TYPE: str | None = None
DEFAULT_REPORT_PERIOD_KEY: str | None = None
DEFAULT_UNIT: str | None = "万元"
DEFAULT_MAPPING_RULE_VERSION = "phase2-v2"
DEFAULT_METRIC_OVERRIDES: dict[int, str] = {}
DEFAULT_ONTOLOGY_GAP_CONFIRMATIONS: tuple[int, ...] = ()
DEFAULT_DEEPSEEK_ENV_PATH = PROJECT_ROOT / ".env"

# Phase 2.5 右键运行配置：
# - True：调用 DeepSeek；False：只展示 Candidate Retrieval。
# - SOURCE_ROWS 为空时，按源行顺序选择未决 Metric；填写后只判断这些源行。
# - MAX_JUDGMENTS=None 表示处理全部选中项。默认五行覆盖等价、父子口径、
#   符号相反和本体缺口/证据不足等代表情况，避免一次右键运行产生大量 API 请求。
DEFAULT_PHASE25_USE_LLM = True
DEFAULT_PHASE25_TOP_K = 5
DEFAULT_PHASE25_SOURCE_ROWS: tuple[int, ...] = (13, 37, 43, 44, 58)
DEFAULT_PHASE25_MAX_JUDGMENTS: int | None = 12
DEFAULT_PHASE25_SHOW_FULL_PHASE2 = False
DEFAULT_PHASE25_SHOW_DETAIL = False
DEFAULT_PHASE25_CANDIDATE_PREVIEW = 3

# 多业务范围列必须在这里显式绑定，不能由 a.py 猜测。示例：
# DEFAULT_VALUE_BINDINGS = (
#     ValueFieldBinding(
#         value_field="发电成本",
#         business_scope=ScalarBinding(constant="发电成本"),
#         period_type="YEAR",
#         period_key=ScalarBinding(field="时间"),
#         period_basis="PERIOD_VALUE",
#         unit=ScalarBinding(constant="元"),
#     ),
# )
DEFAULT_VALUE_BINDINGS: tuple[ValueFieldBinding, ...] = ()
# ====================================================

SUPPORTED_SUFFIXES = {".csv", ".xlsx"}


def build_console_report(
    result: Any, preview_rows: int | None = 2
) -> dict[str, Any]:
    """生成适合人工判断解析结果的中文精简报告。"""

    raw = result.raw_dataset
    datasets: list[dict[str, Any]] = []

    for dataset in result.curated_datasets:
        quality = dataset.quality_report
        source_rows = [row.source_row for row in dataset.rows]
        all_null_columns = [
            column.original_name
            for column in dataset.data_schema.columns
            if column.data_type == "null"
        ]

        fields = []
        for index, column in enumerate(dataset.data_schema.columns):
            mapping = dataset.header_mapping[index]
            field = {
                "名称": column.original_name,
                "源列": mapping.source_position,
                "数据类型": column.data_type,
                "允许为空": column.nullable,
                "类型推断置信度": column.confidence,
            }
            # 只有技术性去重或空表头补名时，内部键才值得单独展示。
            if column.normalized_name != column.original_name:
                field["内部键"] = column.normalized_name
            fields.append(field)

        issues = [
            {
                "级别": issue.severity,
                "代码": issue.code,
                "说明": issue.message,
                "源行": issue.source_row,
                "源列": issue.source_column,
            }
            for issue in quality.issues
        ]

        dataset_report: dict[str, Any] = {
            "工作表": dataset.sheet_name,
            "解析到的表头行": list(dataset.header_rows),
            "表头前上下文": [
                {
                    "源行": cell.source_row,
                    "源列": cell.source_column,
                    "值": cell.value,
                }
                for cell in dataset.context_cells
            ],
            "数据规模": {
                "源数据行数": quality.input_row_count,
                "Curated 行数": quality.curated_row_count,
                "列数": quality.column_count,
                "源数据行范围": (
                    [min(source_rows), max(source_rows)] if source_rows else None
                ),
                "删除的完全空白行": quality.row_count_reasons.get(
                    "entirely_blank_rows_removed", 0
                ),
            },
            "字段": fields,
            "数据预览": [
                {"源行": row.source_row, **dict(row.values)}
                for row in (
                    dataset.rows
                    if preview_rows is None
                    else dataset.rows[:preview_rows]
                )
            ],
            "质量检查": {
                "通过": quality.passed,
                "重复行数": quality.duplicate_row_count,
                "转换错误数": quality.conversion_error_count,
                "问题": issues,
            },
        }
        if all_null_columns:
            dataset_report["未验证值类型的空列"] = all_null_columns
            dataset_report["空列说明"] = (
                "这些列的表头结构已被保留，但当前文件没有非空值，"
                "因此只能推断为 null，不能据此验证实际数据类型。"
            )
        datasets.append(dataset_report)

    return {
        "文件": raw.content_ref,
        "解析成功": True,
        "Raw Dataset": {
            "文件名": raw.source_file,
            "格式": raw.extension,
            "文件大小": raw.size_bytes,
            "SHA-256": raw.sha256,
            "解析版本": raw.parser_version,
            "状态": raw.status,
        },
        "数据集": datasets,
    }


def build_datasets_only_report(
    result: Any, row_limit: int | None
) -> list[dict[str, Any]]:
    """只返回解析后的数据集及其数据行。"""

    datasets = []
    for dataset in result.curated_datasets:
        rows = dataset.rows if row_limit is None else dataset.rows[:row_limit]
        datasets.append(
            {
                "文件": result.raw_dataset.content_ref,
                "工作表": dataset.sheet_name,
                "表头行": list(dataset.header_rows),
                "数据集总行数": len(dataset.rows),
                "本次展示行数": len(rows),
                "数据": [
                    {"源行": row.source_row, **dict(row.values)} for row in rows
                ],
            }
        )
    return datasets


def build_mapping_request(dataset: Any, args: argparse.Namespace) -> MappingRequest:
    """把命令行/文件顶部的显式测试配置转换为 MappingRequest。"""

    unit = ScalarBinding(constant=args.unit) if args.unit is not None else None
    organization = (
        ScalarBinding(constant=args.organization)
        if args.organization is not None
        else None
    )
    report_period_key = (
        ScalarBinding(constant=args.period_key)
        if args.period_key is not None
        else None
    )
    return MappingRequest(
        curated_id=dataset.curated_id,
        metric_name_field=args.metric_name_field,
        value_bindings=DEFAULT_VALUE_BINDINGS,
        organization=organization,
        organization_current_id=DEFAULT_ORGANIZATION_CURRENT_ID,
        report_period_type=args.period_type,
        report_period_key=report_period_key,
        unit=unit,
        metric_overrides=DEFAULT_METRIC_OVERRIDES,
        ontology_gap_confirmations=DEFAULT_ONTOLOGY_GAP_CONFIRMATIONS,
        mapping_rule_version=DEFAULT_MAPPING_RULE_VERSION,
    )


def build_phase2_console_report(
    mapping_result: Any,
    row_limit: int | None,
) -> dict[str, Any]:
    """输出适合人工检查的表级计划、Metric 决策和候选摘要。"""

    plan = mapping_result.plan
    table = plan.table_mapping_plan
    decisions = (
        plan.metric_decisions
        if row_limit is None
        else plan.metric_decisions[:row_limit]
    )
    candidates = (
        plan.observation_candidates
        if row_limit is None
        else plan.observation_candidates[:row_limit]
    )
    excluded_subjects = [
        subject
        for subject in plan.row_subjects
        if subject.row_role.value in {"GROUP", "NOTE"}
    ]
    shown_excluded_subjects = (
        excluded_subjects
        if row_limit is None
        else excluded_subjects[:row_limit]
    )
    return {
        "Mapping Run ID": plan.mapping_run_id,
        "Ontology Revision": plan.ontology_catalog.ontology_revision,
        "输入": {
            "Curated ID": plan.curated_id,
            "Raw Dataset ID": plan.raw_dataset_id,
            "Raw Version ID": plan.raw_version_id,
            "文件": table.source_file,
            "工作表": table.sheet_name,
            "规则版本": plan.request.mapping_rule_version,
        },
        "表级 Mapping Plan": {
            "结构状态": table.structure_status.value,
            "指标名称字段": table.metric_name_field,
            "组织绑定": (
                table.organization.to_dict() if table.organization else None
            ),
            "数值字段绑定": [item.to_dict() for item in table.value_fields],
            "忽略或超范围字段": [
                item.to_dict() for item in table.ignored_fields
            ],
            "未解决 Binding": [
                item.to_dict() for item in table.unresolved_bindings
            ],
            "证据": [item.to_dict() for item in table.evidence],
        },
        "Row / Metric Subject Extraction": {
            "行角色统计": dict(mapping_result.report.row_role_counts),
            "排除 Matcher 的明确非指标行": [
                {
                    "源行": subject.source_row,
                    "raw_label": subject.raw_label,
                    "comparison_name": subject.comparison_name,
                    "row_role": subject.row_role.value,
                    "证据": [item.to_dict() for item in subject.evidence],
                }
                for subject in shown_excluded_subjects
            ],
        },
        "Metric 四态统计": dict(mapping_result.report.metric_status_counts),
        "MetricDecision": {
            "总数": len(plan.metric_decisions),
            "本次展示": len(decisions),
            "明细": [
                {
                    "Decision ID": decision.decision_id,
                    "源行": decision.subject.source_row,
                    "raw_label": decision.subject.raw_label,
                    "comparison_name": decision.subject.comparison_name,
                    "row_role": decision.subject.row_role.value,
                    "状态": decision.status.value,
                    "选中 Metric": (
                        {
                            "current_metric_id": (
                                decision.selected_metric.current_metric_id
                            ),
                            "name_cn": decision.selected_metric.name_cn,
                            "status": decision.selected_metric.status,
                            "version": decision.selected_metric.version,
                        }
                        if decision.selected_metric
                        else None
                    ),
                    "候选": [
                        {
                            "current_metric_id": metric.current_metric_id,
                            "name_cn": metric.name_cn,
                            "status": metric.status,
                            "version": metric.version,
                        }
                        for metric in decision.candidates
                    ],
                    "证据": [item.to_dict() for item in decision.evidence],
                    "本体缺口候选": decision.ontology_gap_candidate,
                }
                for decision in decisions
            ],
        },
        "ObservationCandidate": {
            "说明": "candidate_id 是来源候选身份，不是 ActualObservation.id",
            "总数": len(plan.observation_candidates),
            "本次展示": len(candidates),
            "明细": [
                {
                    "candidate_id": candidate.candidate_id,
                    "源行": candidate.source_row,
                    "值字段": candidate.value_field,
                    "指标名": candidate.metric_name,
                    "current_metric_id": candidate.current_metric_id,
                    "实际值": candidate.actual_value,
                    "业务范围": candidate.business_scope,
                    "组织原值": candidate.organization_value,
                    "组织 current ID": candidate.organization_current_id,
                    "期间类型": candidate.period_type,
                    "期间": candidate.period_key,
                    "期间口径": candidate.period_basis,
                    "原始单位": candidate.unit_raw,
                    "规范单位": candidate.unit_normalized,
                    "满足正式实例约束": (
                        candidate.definition_constraints_satisfied
                    ),
                    "正式实例化缺失字段": list(
                        candidate.instantiation_missing_fields
                    ),
                }
                for candidate in candidates
            ],
        },
        "Mapping Report": {
            "已进入 Matcher 但所有绑定值均为空的行数": (
                mapping_result.report.empty_value_row_count
            ),
            "未投影值": list(mapping_result.report.unprojected_values),
            "约束缺口统计": dict(
                mapping_result.report.constraint_issue_counts
            ),
            "警告": list(mapping_result.report.warnings),
        },
    }


def build_phase2_overview_report(mapping_result: Any) -> dict[str, Any]:
    """为 Phase 2.5 右键入口保留必要的 Phase 2 原始结果摘要。"""

    plan = mapping_result.plan
    table = plan.table_mapping_plan
    matched_examples = [
        decision
        for decision in plan.metric_decisions
        if decision.status.value == "MATCHED"
    ][:3]
    return {
        "Mapping Run ID": plan.mapping_run_id,
        "Ontology Revision": plan.ontology_catalog.ontology_revision,
        "输入": {
            "文件": table.source_file,
            "工作表": table.sheet_name,
            "Curated ID": plan.curated_id,
            "规则版本": plan.request.mapping_rule_version,
        },
        "TableMappingPlan": {
            "结构状态": table.structure_status.value,
            "指标名称字段": table.metric_name_field,
            "组织绑定": (
                {
                    "kind": table.organization.kind,
                    "field": table.organization.field,
                    "value": table.organization.value,
                }
                if table.organization is not None
                else None
            ),
            "数值字段": [
                {
                    "value_field": item.value_field,
                    "business_scope": (
                        item.business_scope.value
                        if item.business_scope is not None
                        else None
                    ),
                    "period_type": item.period_type,
                    "period_basis": item.period_basis,
                    "unit": item.unit.value if item.unit is not None else None,
                    "binding_complete": item.binding_complete,
                }
                for item in table.value_fields
            ],
            "未解决 Binding": [
                item.to_dict() for item in table.unresolved_bindings
            ],
        },
        "Row Role 统计": dict(mapping_result.report.row_role_counts),
        "Metric 四态统计": dict(mapping_result.report.metric_status_counts),
        "Phase 2 确定性匹配样例": [
            {
                "源行": decision.subject.source_row,
                "raw_label": decision.subject.raw_label,
                "comparison_name": decision.subject.comparison_name,
                "current_metric_id": (
                    decision.selected_metric.current_metric_id
                    if decision.selected_metric is not None
                    else None
                ),
            }
            for decision in matched_examples
        ],
        "ObservationCandidate 数": len(plan.observation_candidates),
        "Mapping 警告": list(mapping_result.report.warnings),
    }


def build_phase25_console_report(
    mapping_result: Any,
    catalog: Any,
    judge: Any | None,
) -> tuple[dict[str, Any], int]:
    """运行并展示 Phase 2.5 主链；只产生 PROPOSED 派生结果。"""

    plan = mapping_result.plan
    eligible = [
        decision
        for decision in plan.metric_decisions
        if decision.subject.row_role.value == "METRIC"
        and decision.status.value in {"UNMATCHED", "AMBIGUOUS"}
    ]
    requested_rows = set(DEFAULT_PHASE25_SOURCE_ROWS)
    selected = (
        [
            decision
            for decision in eligible
            if decision.subject.source_row in requested_rows
        ]
        if requested_rows
        else list(eligible)
    )
    if DEFAULT_PHASE25_MAX_JUDGMENTS is not None:
        selected = selected[: max(DEFAULT_PHASE25_MAX_JUDGMENTS, 0)]

    resolutions = []
    proposals = []
    details: list[dict[str, Any]] = []
    pipeline_error_count = 0
    for decision in selected:
        try:
            candidate_set = retrieve_candidates_for_decision(
                decision,
                plan,
                catalog,
                top_k=DEFAULT_PHASE25_TOP_K,
            )
        except (Phase25ReviewError, ValueError) as exc:
            pipeline_error_count += 1
            details.append(
                {
                    "源行": decision.subject.source_row,
                    "raw_label": decision.subject.raw_label,
                    "comparison_name": decision.subject.comparison_name,
                    "Phase 2 状态": decision.status.value,
                    "Candidate Retrieval 成功": False,
                    "错误类型": type(exc).__name__,
                    "错误": str(exc),
                }
            )
            continue

        resolution = (
            run_semantic_judgment(candidate_set, catalog, judge)
            if judge is not None
            else None
        )
        proposal = None
        if resolution is not None:
            resolutions.append(resolution)
            if resolution.semantic_status is SemanticStatus.NO_EQUIVALENT:
                proposal = build_ontology_change_proposal(
                    resolution,
                    candidate_set,
                )
                if proposal is not None:
                    proposals.append(proposal)

        details.append(
            {
                "源行": decision.subject.source_row,
                "raw_label": decision.subject.raw_label,
                "comparison_name": decision.subject.comparison_name,
                "Phase 2 状态": decision.status.value,
                "CandidateSet": {
                    "candidate_set_id": candidate_set.candidate_set_id,
                    "retrieval_version": candidate_set.retrieval_version,
                    "top_k": candidate_set.top_k,
                    "上下文摘要": {
                        "nearest_group": candidate_set.semantic_context.get(
                            "nearest_group"
                        ),
                        "previous_subjects": [
                            {
                                "源行": item.get("source_row"),
                                "comparison_name": item.get("comparison_name"),
                                "row_role": item.get("row_role"),
                            }
                            for item in candidate_set.semantic_context.get(
                                "previous_subjects"
                            )
                            or ()
                        ],
                        "following_subjects": [
                            {
                                "源行": item.get("source_row"),
                                "comparison_name": item.get("comparison_name"),
                                "row_role": item.get("row_role"),
                            }
                            for item in candidate_set.semantic_context.get(
                                "following_subjects"
                            )
                            or ()
                        ],
                        "同表候选冲突": [
                            {
                                "源行": item.get("source_row"),
                                "raw_label": item.get("raw_label"),
                                "comparison_name": item.get("comparison_name"),
                                "phase2_status": item.get("phase2_status"),
                                "current_metric_id": item.get("current_metric_id"),
                            }
                            for item in candidate_set.semantic_context.get(
                                "same_table_candidate_conflicts"
                            )
                            or ()
                        ],
                        "报表注释": [
                            {
                                "源行": item.get("source_row"),
                                "raw_label": item.get("raw_label"),
                                "comparison_name": item.get("comparison_name"),
                            }
                            for item in candidate_set.semantic_context.get(
                                "report_notes"
                            )
                            or ()
                        ],
                        "table_value_context": [
                            {
                                "value_field": item.get("value_field"),
                                "business_scope": (
                                    (item.get("business_scope") or {}).get(
                                        "value"
                                    )
                                ),
                                "period_type": item.get("period_type"),
                                "period_basis": item.get("period_basis"),
                                "unit": (item.get("unit") or {}).get("value"),
                                "binding_complete": item.get(
                                    "binding_complete"
                                ),
                            }
                            for item in candidate_set.semantic_context.get(
                                "table_value_context"
                            )
                            or ()
                        ],
                    },
                    "候选": [
                        {
                            "rank": item.rank,
                            "current_metric_id": item.metric.current_metric_id,
                            "name_cn": item.metric.name_cn,
                            "definition_cn": item.metric.definition_cn,
                            "aliases": list(item.metric.aliases),
                            "召回分数摘要": {
                                "total": item.scores.total,
                                "name_sequence": item.scores.name_sequence,
                                "alias_sequence": item.scores.alias_sequence,
                                "definition_overlap": (
                                    item.scores.definition_overlap
                                ),
                                "context_similarity": (
                                    item.scores.context_similarity
                                ),
                            },
                        }
                        for item in candidate_set.candidates
                    ],
                },
                "SemanticResolution": (
                    {
                        "resolution_id": resolution.resolution_id,
                        "execution_status": resolution.execution_status.value,
                        "semantic_status": (
                            resolution.semantic_status.value
                            if resolution.semantic_status is not None
                            else None
                        ),
                        "review_status": (
                            resolution.review_status.value
                            if resolution.review_status is not None
                            else None
                        ),
                        "selected_metric_id": resolution.selected_metric_id,
                        "reason": resolution.reason,
                        "error_code": resolution.error_code,
                        "error_message": resolution.error_message,
                        "supporting_evidence": [
                            item.to_dict()
                            for item in resolution.supporting_evidence
                        ],
                        "counter_evidence": [
                            item.to_dict() for item in resolution.counter_evidence
                        ],
                    }
                    if resolution is not None
                    else None
                ),
                "OntologyChangeProposal 草案": (
                    {
                        "proposal_id": proposal.proposal_id,
                        "proposal_kind": proposal.proposal_kind.value,
                        "suggested_name_cn": proposal.suggested_name_cn,
                        "target_metric_id": proposal.target_metric_id,
                        "related_metric_ids": list(proposal.related_metric_ids),
                        "reason": proposal.reason,
                        "review_status": proposal.review_status.value,
                    }
                    if proposal is not None
                    else None
                ),
            }
        )

    if not DEFAULT_PHASE25_SHOW_DETAIL:
        for item in details:
            candidate_report = item.get("CandidateSet")
            if isinstance(candidate_report, dict):
                candidates = candidate_report.get("候选") or []
                context_summary = candidate_report.get("上下文摘要") or {}
                item["CandidateSet"] = {
                    "candidate_set_id": candidate_report["candidate_set_id"],
                    "retrieval_version": candidate_report["retrieval_version"],
                    "top_k": candidate_report["top_k"],
                    "本次展示候选数": min(
                        len(candidates),
                        max(DEFAULT_PHASE25_CANDIDATE_PREVIEW, 0),
                    ),
                    "同表候选冲突": context_summary.get("同表候选冲突") or [],
                    "报表注释": context_summary.get("报表注释") or [],
                    "候选摘要": [
                        {
                            "rank": candidate["rank"],
                            "current_metric_id": candidate[
                                "current_metric_id"
                            ],
                            "name_cn": candidate["name_cn"],
                            "total_score": candidate["召回分数摘要"]["total"],
                        }
                        for candidate in candidates[
                            : max(DEFAULT_PHASE25_CANDIDATE_PREVIEW, 0)
                        ]
                    ],
                }
            resolution_report = item.get("SemanticResolution")
            if isinstance(resolution_report, dict):
                item["SemanticResolution"] = {
                    "resolution_id": resolution_report["resolution_id"],
                    "execution_status": resolution_report[
                        "execution_status"
                    ],
                    "semantic_status": resolution_report["semantic_status"],
                    "review_status": resolution_report["review_status"],
                    "selected_metric_id": resolution_report[
                        "selected_metric_id"
                    ],
                    "reason": resolution_report["reason"],
                    "error_code": resolution_report["error_code"],
                    "error_message": resolution_report["error_message"],
                }

    execution_status_counts = {status.value: 0 for status in ExecutionStatus}
    semantic_status_counts = {status.value: 0 for status in SemanticStatus}
    for resolution in resolutions:
        execution_status_counts[resolution.execution_status.value] += 1
        if resolution.semantic_status is not None:
            semantic_status_counts[resolution.semantic_status.value] += 1

    effective = build_effective_mapping_view(
        plan,
        tuple(resolutions),
        catalog,
    )
    effective_status_counts: dict[str, int] = {}
    effective_source_counts: dict[str, int] = {}
    for item in effective.items:
        effective_status_counts[item.effective_status] = (
            effective_status_counts.get(item.effective_status, 0) + 1
        )
        effective_source_counts[item.source] = (
            effective_source_counts.get(item.source, 0) + 1
        )

    failed_count = pipeline_error_count + execution_status_counts["FAILED"]
    return (
        {
            "运行模式": (
                "Candidate Retrieval + DeepSeek Semantic Judge"
                if judge is not None
                else "只运行 Candidate Retrieval，不调用 LLM"
            ),
            "输出模式": (
                "详细证据"
                if DEFAULT_PHASE25_SHOW_DETAIL
                else "人工阅读摘要；将 DEFAULT_PHASE25_SHOW_DETAIL 改为 True 可查看完整上下文和证据"
            ),
            "重要边界": [
                "Phase 2 原始 MetricDecision 未被覆盖",
                "所有成功的模型结论均为 PROPOSED，不是正式确认",
                "未确认的 MAP_EXISTING 不进入 Effective Mapping",
                "ADD_METRIC 仅为不可执行草案；ADD_ALIAS 不自动生成",
                "Definition / Knowledge 全程只读",
            ],
            "Judge": (
                {
                    "name": judge.name,
                    "judge_version": judge.version,
                    "prompt_version": judge.prompt_version,
                    "model": judge.model,
                }
                if judge is not None
                else None
            ),
            "数据外发边界": (
                "仅发送指标主体、有限报表上下文、业务范围/期间/单位语义和"
                "Top-K 本体候选；不发送实际数值、源文件路径或内部运行标识"
                if judge is not None
                else "未调用外部模型"
            ),
            "Eligibility": {
                "Phase 2 MetricDecision 总数": len(plan.metric_decisions),
                "eligible 未决 Metric 数": len(eligible),
                "顶部配置指定源行": sorted(requested_rows),
                "本次实际判断数": len(selected),
                "本次未执行的 eligible 数": len(eligible) - len(selected),
                "说明": (
                    "只处理 DEFAULT_PHASE25_SOURCE_ROWS 指定的代表行；设为空元组可"
                    "按顺序处理全部 eligible 项"
                    if requested_rows
                    else "按源行顺序处理 eligible 项"
                ),
            },
            "状态统计": {
                "execution_status": execution_status_counts,
                "semantic_status": semantic_status_counts,
                "Candidate Retrieval 管线错误": pipeline_error_count,
            },
            "结果明细": details,
            "Effective Mapping（只读派生视图）": {
                "状态统计": effective_status_counts,
                "来源统计": effective_source_counts,
                "Phase 2.5 CONFIRMED 映射数": effective_source_counts.get(
                    "PHASE_2_5_CONFIRMED", 0
                ),
                "说明": "当前模型结果均未确认，因此不会新增有效映射。",
            },
            "OntologyChangeProposal": {
                "草案数": len(proposals),
                "自动执行数": 0,
                "明细": [
                    {
                        "proposal_id": item.proposal_id,
                        "proposal_kind": item.proposal_kind.value,
                        "源行": item.source.get("source_row"),
                        "comparison_name": item.comparison_name,
                        "suggested_name_cn": item.suggested_name_cn,
                        "target_metric_id": item.target_metric_id,
                        "review_status": item.review_status.value,
                        "reason": item.reason,
                    }
                    for item in proposals
                ],
            },
        },
        failed_count,
    )


def discover_files(path: Path) -> list[Path]:
    """返回单个输入文件，或递归发现目录中的受支持报表。"""

    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []
    if not path.is_dir():
        return []

    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        and not candidate.name.startswith("~$")
    )


def phase25_review_assignment(mapping_plan: Any) -> ReviewDatasetAssignment:
    """按冻结设计给已知财务报表族分配开发候选或跨表保留候选。"""

    source_name = Path(mapping_plan.table_mapping_plan.source_file).stem
    known_families = (
        ("资产负债表", BALANCE_SHEET, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("现金流量表", CASH_FLOW_STATEMENT, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("成本费用表", COST_EXPENSE_STATEMENT, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("利润表", PROFIT_STATEMENT, ReviewDatasetRole.DEVELOPMENT_CANDIDATE),
    )
    matches = [item for item in known_families if item[0] in source_name]
    if len(matches) != 1:
        raise Phase25ReviewError(
            "无法可靠分配 Phase 2.5 审核报表族，请使用冻结设计中的四类已知样例："
            f"{mapping_plan.table_mapping_plan.source_file}"
        )
    _, report_family, dataset_role = matches[0]
    return ReviewDatasetAssignment(
        report_family=report_family,
        dataset_role=dataset_role,
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 Phase 1/2/2.5 处理单个报表或目录中的 Excel/CSV。"
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_TEST_PATH,
        help=f"待测试的文件或目录，默认：{DEFAULT_TEST_PATH}",
    )
    parser.add_argument(
        "--phase",
        choices=("1", "2", "2.5"),
        default=DEFAULT_PHASE,
        help=f"运行阶段，默认：{DEFAULT_PHASE}",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=None,
        help=(
            "每个工作表显示的数据预览行数；不传时使用文件顶部的"
            " DEFAULT_PREVIEW_ROWS。"
        ),
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--datasets-only",
        action="store_true",
        help="只输出解析后的数据集及数据行，不输出 Raw、Schema 和质量报告。",
    )
    output_group.add_argument(
        "--full",
        action="store_true",
        help="输出当前阶段完整契约；默认输出适合人工查看的摘要。",
    )
    parser.add_argument(
        "--all-rows",
        action="store_true",
        help="展示全部数据行；不指定时由 --preview-rows 控制展示数量。",
    )
    parser.add_argument(
        "--definition",
        type=Path,
        default=DEFAULT_DEFINITION_PATH,
        help="Phase 2 Definition.json 路径。",
    )
    parser.add_argument(
        "--knowledge",
        type=Path,
        default=DEFAULT_KNOWLEDGE_PATH,
        help="Phase 2 Knowledge.json 路径。",
    )
    parser.add_argument(
        "--metric-name-field",
        default=DEFAULT_METRIC_NAME_FIELD,
        help="可选的指标名称字段 override。",
    )
    parser.add_argument(
        "--organization",
        default=DEFAULT_ORGANIZATION,
        help="可选的组织常量；不传时使用确定性字段绑定。",
    )
    parser.add_argument(
        "--period-type",
        choices=("MONTH", "QUARTER", "YEAR"),
        default=DEFAULT_REPORT_PERIOD_TYPE,
        help="可选的报表期间类型常量。",
    )
    parser.add_argument(
        "--period-key",
        default=DEFAULT_REPORT_PERIOD_KEY,
        help="可选的报表期间常量；不传时使用确定性字段绑定。",
    )
    parser.add_argument(
        "--unit",
        default=DEFAULT_UNIT,
        help=f"可选的单位常量，默认：{DEFAULT_UNIT}",
    )
    phase25_group = parser.add_mutually_exclusive_group()
    phase25_group.add_argument(
        "--phase25-review-output",
        type=Path,
        help="从 Phase 2 未决 Metric 生成 Phase 2.5 P0 人工审核 JSON；拒绝覆盖已有文件。",
    )
    phase25_group.add_argument(
        "--phase25-validate-review",
        type=Path,
        help="只读校验人工填写后的 Phase 2.5 Gold 审核 JSON。",
    )
    phase25_group.add_argument(
        "--phase25-evaluate-retrieval",
        type=Path,
        help="在已确认 Gold Truth 上只读评测 Phase 2.5 Candidate Retrieval。",
    )
    phase25_group.add_argument(
        "--phase25-deepseek-pilot",
        type=Path,
        help="在已确认 Gold Truth 上运行只读 DeepSeek Semantic Judge Pilot。",
    )
    parser.add_argument(
        "--deepseek-env-file",
        type=Path,
        default=DEFAULT_DEEPSEEK_ENV_PATH,
        help=f"DeepSeek 本地配置文件，默认：{DEFAULT_DEEPSEEK_ENV_PATH}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    if args.phase25_validate_review is not None:
        try:
            catalog = load_ontology_catalog(
                args.definition.expanduser().resolve(),
                args.knowledge.expanduser().resolve(),
            )
            validation = validate_gold_review_file(
                args.phase25_validate_review,
                catalog,
            )
        except (OSError, OntologyCatalogError, Phase25ReviewError) as exc:
            print(
                json.dumps(
                    {
                        "Phase": "2.5-P0",
                        "审核材料校验成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(json.dumps(validation.to_dict(), ensure_ascii=False, indent=2))
        return 0 if validation.ready_for_p1 else 2

    if args.phase25_evaluate_retrieval is not None:
        try:
            catalog = load_ontology_catalog(
                args.definition.expanduser().resolve(),
                args.knowledge.expanduser().resolve(),
            )
            evaluation = evaluate_retrieval_on_gold(
                load_gold_review_payload(args.phase25_evaluate_retrieval),
                catalog,
            )
        except (OSError, OntologyCatalogError, Phase25ReviewError) as exc:
            print(
                json.dumps(
                    {
                        "Phase": "2.5-P1",
                        "召回评测成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(json.dumps(evaluation.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.phase25_deepseek_pilot is not None:
        try:
            catalog = load_ontology_catalog(
                args.definition.expanduser().resolve(),
                args.knowledge.expanduser().resolve(),
            )
            judge = DeepSeekSemanticJudge(
                env_file=args.deepseek_env_file.expanduser().resolve()
            )
            judge.validate_local_configuration()
            pilot = run_semantic_pilot_on_gold(
                load_gold_review_payload(args.phase25_deepseek_pilot),
                catalog,
                judge,
            )
        except (
            OSError,
            OntologyCatalogError,
            Phase25ReviewError,
            JudgeUnavailableError,
            ValueError,
        ) as exc:
            print(
                json.dumps(
                    {
                        "Phase": "2.5-P2-LLM-Pilot",
                        "Pilot 执行成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(json.dumps(pilot.to_dict(), ensure_ascii=False, indent=2))
        gate_passed = (
            pilot.failed_count == 0
            and pilot.skipped_count == 0
            and pilot.map_existing_metric_accuracy == 1.0
            and pilot.hard_negative_false_match_count == 0
            and pilot.non_conservative_error_count == 0
        )
        return 0 if gate_passed else 2

    if args.phase25_review_output is not None and args.phase == "1":
        print("--phase25-review-output 只能与 --phase 2 一起使用", file=sys.stderr)
        return 1

    input_path = args.path.expanduser().resolve()
    datasets_only_mode = (
        args.phase == "1" and (DEFAULT_DATASETS_ONLY or args.datasets_only)
    )
    if args.full:
        datasets_only_mode = False

    if args.all_rows:
        row_limit = None
    elif args.preview_rows is not None:
        row_limit = max(args.preview_rows, 0)
    elif DEFAULT_ALL_ROWS:
        row_limit = None
    else:
        row_limit = max(DEFAULT_PREVIEW_ROWS, 0)
    files = discover_files(input_path)

    if not files:
        print(
            json.dumps(
                {
                    "测试路径": str(input_path),
                    "错误": "没有找到可测试的 .xlsx 或 .csv 文件。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    reports: list[dict[str, Any]] = []
    parsed_datasets: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    parse_failure_count = 0
    quality_failure_count = 0
    ready_count = 0
    needs_binding_count = 0
    blocked_count = 0
    phase25_failure_count = 0
    phase25_plans: list[Any] = []
    phase25_assignments: dict[str, ReviewDatasetAssignment] = {}

    catalog = None
    if args.phase in {"2", "2.5"}:
        try:
            catalog = load_ontology_catalog(
                args.definition.expanduser().resolve(),
                args.knowledge.expanduser().resolve(),
            )
        except (OSError, OntologyCatalogError) as exc:
            print(
                json.dumps(
                    {
                        "Phase": 2,
                        "本体加载成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    phase25_judge = None
    if args.phase == "2.5" and DEFAULT_PHASE25_USE_LLM:
        phase25_judge = DeepSeekSemanticJudge(
            env_file=args.deepseek_env_file.expanduser().resolve()
        )

    for source_file in files:
        try:
            result = curate_file(source_file, PipelineConfig())
            quality_passed = all(
                dataset.quality_report.passed
                for dataset in result.curated_datasets
            )
            if not quality_passed:
                quality_failure_count += 1
            if datasets_only_mode:
                parsed_datasets.extend(
                    build_datasets_only_report(result, row_limit)
                )
            elif args.phase == "1":
                report = (
                    result.to_dict()
                    if args.full
                    else build_console_report(result, preview_rows=row_limit)
                )
                reports.append(report)
            else:
                assert catalog is not None
                for dataset in result.curated_datasets:
                    request = build_mapping_request(dataset, args)
                    mapping_result = map_curated_dataset(
                        dataset,
                        request,
                        catalog,
                    )
                    if args.phase25_review_output is not None:
                        phase25_plans.append(mapping_result.plan)
                        phase25_assignments[mapping_result.plan.mapping_run_id] = (
                            phase25_review_assignment(mapping_result.plan)
                        )
                    status = mapping_result.report.structure_status
                    if status is StructureStatus.BLOCKED:
                        blocked_count += 1
                    elif status is StructureStatus.NEEDS_BINDING:
                        needs_binding_count += 1
                    else:
                        ready_count += 1
                    if (
                        args.phase == "2.5"
                        and not args.full
                        and not DEFAULT_PHASE25_SHOW_FULL_PHASE2
                    ):
                        phase2_report = build_phase2_overview_report(
                            mapping_result
                        )
                    else:
                        phase2_report = (
                            mapping_result.to_dict()
                            if args.full
                            else build_phase2_console_report(
                                mapping_result,
                                row_limit,
                            )
                        )
                    if (
                        args.phase == "2.5"
                        and args.phase25_review_output is None
                    ):
                        phase25_report, failure_count = (
                            build_phase25_console_report(
                                mapping_result,
                                catalog,
                                phase25_judge,
                            )
                        )
                        phase25_failure_count += failure_count
                        reports.append(
                            {
                                "Phase 2 原始结果（冻结）": phase2_report,
                                "Phase 2.5 派生结果": phase25_report,
                            }
                        )
                    else:
                        reports.append(phase2_report)
        except Phase1Error as exc:
            parse_failure_count += 1
            parse_errors.append(f"{source_file}：{exc}")
            if not datasets_only_mode:
                reports.append(
                    {
                        "文件": str(source_file),
                        "解析成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    }
                )

    if datasets_only_mode:
        print(
            json.dumps(
                parsed_datasets, ensure_ascii=False, indent=2, default=str
            )
        )
        for error in parse_errors:
            print(f"解析失败：{error}", file=sys.stderr)
        return 1 if parse_failure_count or quality_failure_count else 0

    phase25_review_summary: dict[str, Any] | None = None
    if args.phase25_review_output is not None:
        assert catalog is not None
        try:
            if parse_failure_count:
                raise Phase25ReviewError(
                    "存在未解析文件，拒绝生成不完整的 Phase 2.5 审核材料"
                )
            draft = build_gold_review_draft(
                phase25_plans,
                catalog,
                phase25_assignments,
            )
            destination = export_gold_review_draft(
                draft,
                args.phase25_review_output,
            )
            validation = validate_gold_review_payload(draft.to_dict(), catalog)
            phase25_review_summary = {
                "审核材料": str(destination),
                "Draft ID": draft.draft_id,
                "Ontology Revision": draft.ontology_revision,
                "待审核案例数": len(draft.cases),
                "Eligibility 跳过审计数": len(draft.skipped_items),
                "P1 门禁通过": validation.ready_for_p1,
                "门禁问题": [item.to_dict() for item in validation.issues],
            }
        except (OSError, Phase25ReviewError) as exc:
            print(
                json.dumps(
                    {
                        "Phase": "2.5-P0",
                        "审核材料生成成功": False,
                        "错误类型": type(exc).__name__,
                        "错误": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    summary: dict[str, Any] = {
        "Phase": args.phase,
        "测试路径": str(input_path),
        "发现文件数": len(files),
        "解析成功文件数": len(files) - parse_failure_count,
        "质量通过文件数": len(files)
        - parse_failure_count
        - quality_failure_count,
        "解析失败文件数": parse_failure_count,
        "质量未通过文件数": quality_failure_count,
        "结果": reports,
    }
    if args.phase in {"2", "2.5"}:
        summary.update(
            {
                "READY 数据集数": ready_count,
                "NEEDS_BINDING 数据集数": needs_binding_count,
                "BLOCKED 数据集数": blocked_count,
            }
        )
    if args.phase == "2.5":
        summary.update(
            {
                "Phase 2.5 LLM 调用": DEFAULT_PHASE25_USE_LLM,
                "Phase 2.5 技术失败数": phase25_failure_count,
                "Phase 2.5 正式确认数": 0,
                "Phase 2.5 本体自动修改数": 0,
            }
        )
    if phase25_review_summary is not None:
        summary["Phase 2.5 P0 人工审核门禁"] = phase25_review_summary
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if parse_failure_count or blocked_count or phase25_failure_count:
        return 1
    if needs_binding_count:
        return 2
    return 1 if quality_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
