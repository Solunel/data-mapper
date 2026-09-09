"""Phase 1 / Phase 2 个人测试入口。

用法示例：
    python a.py
    python a.py --phase 2 --preview-rows 5
    python a.py --phase 1 --preview-rows 3
    python a.py "E:\\Desktop\\企业课题\\表格\\某份报表.xlsx"
    python a.py "某份报表.xlsx" --phase 2 --full
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
    MappingRequest,
    OntologyCatalogError,
    Phase1Error,
    PipelineConfig,
    ScalarBinding,
    StructureStatus,
    ValueFieldBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_dataset,
)


# ===== PyCharm 右键运行配置：通常只需要修改这里 =====
DEFAULT_TEST_PATH = Path(
    r"E:\Code_Repo\data-mapper\tests\fixtures\财务快报-利润表.xlsx"
)
DEFAULT_PHASE = 2
DEFAULT_DATASETS_ONLY = True
DEFAULT_PREVIEW_ROWS = 20
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 Phase 1/2 处理单个报表或目录中的 Excel/CSV。"
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
        type=int,
        choices=(1, 2),
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
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    input_path = args.path.expanduser().resolve()
    datasets_only_mode = (
        args.phase == 1 and (DEFAULT_DATASETS_ONLY or args.datasets_only)
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

    catalog = None
    if args.phase == 2:
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
            elif args.phase == 1:
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
                    status = mapping_result.report.structure_status
                    if status is StructureStatus.BLOCKED:
                        blocked_count += 1
                    elif status is StructureStatus.NEEDS_BINDING:
                        needs_binding_count += 1
                    else:
                        ready_count += 1
                    reports.append(
                        mapping_result.to_dict()
                        if args.full
                        else build_phase2_console_report(
                            mapping_result,
                            row_limit,
                        )
                    )
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
    if args.phase == 2:
        summary.update(
            {
                "READY 数据集数": ready_count,
                "NEEDS_BINDING 数据集数": needs_binding_count,
                "BLOCKED 数据集数": blocked_count,
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if parse_failure_count or blocked_count:
        return 1
    if needs_binding_count:
        return 2
    return 1 if quality_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
