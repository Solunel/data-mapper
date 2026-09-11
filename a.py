"""Data Mapper 的本地命令行与 PyCharm 右键运行入口。

业务 Mapping 只调用正式 `map_curated_observations` workflow；Gold、Recall 和
Pilot 入口是显式的只读 Evaluation 工具。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_mapper import (  # noqa: E402
    DeepSeekSemanticJudge,
    ExecutionStatus,
    JudgeUnavailableError,
    MetricResolutionMode,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    OntologyCatalogError,
    Phase1Error,
    PipelineConfig,
    ScalarBinding,
    StructureStatus,
    ValueFieldBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
)
from data_mapper.evaluation import (  # noqa: E402
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    COST_EXPENSE_STATEMENT,
    PROFIT_STATEMENT,
    Phase25ReviewError,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
    build_gold_review_draft,
    evaluate_retrieval_on_gold,
    export_gold_review_draft,
    load_gold_review_payload,
    run_semantic_pilot_on_gold,
    validate_gold_review_file,
    validate_gold_review_payload,
)


# ===== PyCharm 右键运行配置：通常只需要修改这里 =====
DEFAULT_TEST_PATH = PROJECT_ROOT / "reports" / "一级子公司A_利润表_2025-01.xlsx"
"""
三个模式分别是：
- preparation：只做数据接入与 Curated 整理
- deterministic：结构化 + 确定性 Metric 匹配
- semantic：确定性匹配 + DeepSeek 语义 fallback
"""

DEFAULT_MODE = "deterministic"
DEFAULT_FULL_OUTPUT = True
DEFAULT_SAVE_OUTPUT_JSON = True
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs"
DEFAULT_DATASETS_ONLY = True
DEFAULT_PREVIEW_ROWS = 10
DEFAULT_ALL_ROWS = False

DEFAULT_DEFINITION_PATH = PROJECT_ROOT / "ontology" / "Definition.json"
DEFAULT_KNOWLEDGE_PATH = PROJECT_ROOT / "ontology" / "Knowledge.json"
DEFAULT_METRIC_NAME_FIELD: str | None = None
DEFAULT_ORGANIZATION: str | None = None
DEFAULT_ORGANIZATION_CURRENT_ID: str | None = None
DEFAULT_REPORT_PERIOD_TYPE: str | None = None
DEFAULT_REPORT_PERIOD_KEY: str | None = None
DEFAULT_UNIT: str | None = "万元"
DEFAULT_STRUCTURING_RULE_VERSION = "observation-structuring-v1"
DEFAULT_DETERMINISTIC_RULE_VERSION = "deterministic-resolution-v1"
DEFAULT_METRIC_ROW_HINTS: tuple[int, ...] = ()
DEFAULT_METRIC_OVERRIDES: dict[int, str] = {}
DEFAULT_ONTOLOGY_GAP_CONFIRMATIONS: tuple[int, ...] = ()
DEFAULT_DEEPSEEK_ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_SEMANTIC_USE_LLM = True
DEFAULT_SEMANTIC_TOP_K = 5
DEFAULT_SEMANTIC_SOURCE_ROWS: tuple[int, ...] = (13, 37, 43, 44, 58)
DEFAULT_SEMANTIC_MAX_JUDGMENTS: int | None = 12
DEFAULT_SEMANTIC_CANDIDATE_PREVIEW = 3

DEFAULT_VALUE_BINDINGS: tuple[ValueFieldBinding, ...] = ()
# ====================================================

SUPPORTED_SUFFIXES = {".csv", ".xlsx"}


def build_console_report(result: Any, preview_rows: int | None = 2) -> dict[str, Any]:
    raw = result.raw_dataset
    datasets = []
    for dataset in result.curated_datasets:
        rows = dataset.rows if preview_rows is None else dataset.rows[:preview_rows]
        datasets.append(
            {
                "工作表": dataset.sheet_name,
                "解析到的表头行": list(dataset.header_rows),
                "数据规模": {
                    "源数据行数": dataset.quality_report.input_row_count,
                    "Curated 行数": dataset.quality_report.curated_row_count,
                    "列数": dataset.quality_report.column_count,
                },
                "字段": [item.to_dict() for item in dataset.data_schema.columns],
                "数据预览": [
                    {"源行": row.source_row, **dict(row.values)} for row in rows
                ],
                "质量检查": dataset.quality_report.to_dict(),
            }
        )
    return {
        "文件": raw.content_ref,
        "解析成功": True,
        "Raw Dataset": raw.to_dict(),
        "数据集": datasets,
    }


def build_datasets_only_report(result: Any, row_limit: int | None) -> list[dict[str, Any]]:
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


def build_mapping_requests(
    dataset: Any,
    args: argparse.Namespace,
    *,
    semantic_fallback: bool,
) -> tuple[ObservationStructuringRequest, MetricResolutionRequest]:
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
    structuring = ObservationStructuringRequest(
        curated_id=dataset.curated_id,
        metric_name_field=args.metric_name_field,
        value_bindings=DEFAULT_VALUE_BINDINGS,
        organization=organization,
        organization_id=DEFAULT_ORGANIZATION_CURRENT_ID,
        report_period_type=args.period_type,
        report_period_key=report_period_key,
        unit=unit,
        metric_row_hints=DEFAULT_METRIC_ROW_HINTS,
        structuring_rule_version=DEFAULT_STRUCTURING_RULE_VERSION,
    )
    resolution = MetricResolutionRequest(
        mode=(
            MetricResolutionMode.DETERMINISTIC_WITH_SEMANTIC_FALLBACK
            if semantic_fallback
            else MetricResolutionMode.DETERMINISTIC_ONLY
        ),
        metric_overrides=DEFAULT_METRIC_OVERRIDES,
        ontology_gap_confirmations=DEFAULT_ONTOLOGY_GAP_CONFIRMATIONS,
        deterministic_rule_version=DEFAULT_DETERMINISTIC_RULE_VERSION,
        retrieval_top_k=DEFAULT_SEMANTIC_TOP_K,
        semantic_source_rows=DEFAULT_SEMANTIC_SOURCE_ROWS,
        semantic_max_judgments=DEFAULT_SEMANTIC_MAX_JUDGMENTS,
    )
    return structuring, resolution


def build_mapping_console_report(
    mapping_result: Any,
    row_limit: int | None,
) -> dict[str, Any]:
    structuring = mapping_result.structuring_result
    resolution = mapping_result.metric_resolution_result
    decisions = (
        resolution.deterministic_decisions
        if row_limit is None
        else resolution.deterministic_decisions[:row_limit]
    )
    observations = (
        mapping_result.resolved_observations
        if row_limit is None
        else mapping_result.resolved_observations[:row_limit]
    )
    return {
        "Structuring Run ID": structuring.structuring_run_id,
        "Resolution Run ID": resolution.resolution_run_id,
        "Ontology Revision": resolution.ontology_revision,
        "TableMappingPlan": structuring.table_mapping_plan.to_dict(),
        "Observation Structuring Report": structuring.report.to_dict(),
        "Metric Resolution Report": resolution.report.to_dict(),
        "MetricDecision": [item.to_dict() for item in decisions],
        "ResolvedObservation": [item.to_dict() for item in observations],
    }


def build_mapping_overview_report(mapping_result: Any) -> dict[str, Any]:
    structuring = mapping_result.structuring_result
    resolution = mapping_result.metric_resolution_result
    matched = [
        decision
        for decision in resolution.deterministic_decisions
        if decision.status.value == "MATCHED"
    ][:3]
    return {
        "Structuring Run ID": structuring.structuring_run_id,
        "Resolution Run ID": resolution.resolution_run_id,
        "Ontology Revision": resolution.ontology_revision,
        "输入": {
            "文件": structuring.table_mapping_plan.source_file,
            "工作表": structuring.table_mapping_plan.sheet_name,
            "Curated ID": structuring.curated_id,
            "Structuring 规则版本": structuring.request.structuring_rule_version,
            "Resolution 规则版本": resolution.request.deterministic_rule_version,
        },
        "结构状态": structuring.report.structure_status.value,
        "Row Role 统计": dict(structuring.report.row_role_counts),
        "Metric 四态统计": dict(resolution.report.deterministic_status_counts),
        "确定性匹配样例": [
            {
                "源行": item.subject.source_row,
                "raw_label": item.subject.raw_label,
                "current_metric_id": item.selected_metric.current_metric_id,
            }
            for item in matched
        ],
        "ObservationDraft 数": len(structuring.observation_drafts),
        "ResolvedObservation 数": len(mapping_result.resolved_observations),
    }


def build_semantic_console_report(mapping_result: Any) -> tuple[dict[str, Any], int]:
    """只展示正式 workflow 已产生的 Resolution 结果，不自行编排。"""

    resolution_result = mapping_result.metric_resolution_result
    decisions = {
        item.decision_id: item for item in resolution_result.deterministic_decisions
    }
    candidate_sets = {
        item.source_metric_decision_id: item
        for item in resolution_result.candidate_sets
    }
    details = []
    for resolution in resolution_result.semantic_resolutions:
        decision = decisions[resolution.source_metric_decision_id]
        candidate_set = candidate_sets[resolution.source_metric_decision_id]
        details.append(
            {
                "源行": decision.subject.source_row,
                "raw_label": decision.subject.raw_label,
                "comparison_name": decision.subject.comparison_name,
                "CandidateSet": {
                    "candidate_set_id": candidate_set.candidate_set_id,
                    "候选摘要": [
                        {
                            "rank": item.rank,
                            "current_metric_id": item.metric.current_metric_id,
                            "name_cn": item.metric.name_cn,
                            "total": item.scores.total,
                        }
                        for item in candidate_set.candidates[
                            :DEFAULT_SEMANTIC_CANDIDATE_PREVIEW
                        ]
                    ],
                    "同表候选冲突": [
                        {
                            "源行": item.get("source_row"),
                            "raw_label": item.get("raw_label"),
                            "current_metric_id": item.get("current_metric_id"),
                        }
                        for item in candidate_set.semantic_context[
                            "same_table_candidate_conflicts"
                        ]
                    ],
                },
                "SemanticResolution": resolution.to_dict(),
            }
        )
    failed_count = sum(
        item.execution_status is ExecutionStatus.FAILED
        for item in resolution_result.semantic_resolutions
    )
    return (
        {
            "Eligibility": {
                "本次实际判断数": len(resolution_result.semantic_resolutions),
                "候选集数": len(resolution_result.candidate_sets),
            },
            "结果明细": details,
            "Effective Metric Resolution": {
                "CONFIRMED 语义映射数": sum(
                    item.source == "SEMANTIC_CONFIRMED"
                    for item in mapping_result.effective_metric_resolutions
                ),
                "说明": "PROPOSED 不会成为有效映射。",
            },
            "OntologyChangeProposal": {
                "草案数": len(resolution_result.ontology_change_proposals),
                "自动执行数": 0,
                "明细": [
                    item.to_dict()
                    for item in resolution_result.ontology_change_proposals
                ],
            },
        },
        failed_count,
    )


def discover_files(path: Path) -> list[Path]:
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


def save_output_json(
    serialized: str,
    directory: Path,
    mode: str,
    *,
    timestamp: str | None = None,
) -> Path:
    """排他保存本次控制台 JSON；同名时递增，不覆盖已有文件。"""

    directory.mkdir(parents=True, exist_ok=True)
    generated_at = timestamp or datetime.now().astimezone().strftime(
        "%Y%m%d-%H%M%S-%f"
    )
    stem = f"data-mapper-{mode}-{generated_at}"
    version = 1
    while True:
        suffix = "" if version == 1 else f"_v{version}"
        destination = directory / f"{stem}{suffix}.json"
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
            return destination
        except FileExistsError:
            version += 1


def emit_json_output(
    payload: Any,
    args: argparse.Namespace,
    *,
    mode: str,
    exit_code: int,
) -> int:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(serialized)
    save_enabled = (
        DEFAULT_SAVE_OUTPUT_JSON
        if args.save_output_json is None
        else args.save_output_json
    )
    if not save_enabled:
        return exit_code
    try:
        destination = save_output_json(
            serialized,
            args.output_directory.resolve(),
            mode,
        )
    except OSError as exc:
        print(
            json.dumps(
                {"输出保存成功": False, "错误": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(f"输出 JSON 已保存：{destination}", file=sys.stderr)
    return exit_code


def review_assignment(mapping_result: Any) -> ReviewDatasetAssignment:
    source_name = Path(
        mapping_result.structuring_result.table_mapping_plan.source_file
    ).stem
    known_families = (
        ("资产负债表", BALANCE_SHEET, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("现金流量表", CASH_FLOW_STATEMENT, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("成本费用表", COST_EXPENSE_STATEMENT, ReviewDatasetRole.HOLDOUT_CANDIDATE),
        ("利润表", PROFIT_STATEMENT, ReviewDatasetRole.DEVELOPMENT_CANDIDATE),
    )
    matches = [item for item in known_families if item[0] in source_name]
    if len(matches) != 1:
        raise Phase25ReviewError(
            "无法可靠分配审核报表族，请使用冻结设计中的四类已知样例："
            + mapping_result.structuring_result.table_mapping_plan.source_file
        )
    _, report_family, dataset_role = matches[0]
    return ReviewDatasetAssignment(report_family, dataset_role)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Data Mapper 处理 Excel/CSV。")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_TEST_PATH)
    parser.add_argument(
        "--mode",
        choices=("preparation", "deterministic", "semantic"),
        default=DEFAULT_MODE,
    )
    parser.add_argument("--preview-rows", type=int, default=None)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--datasets-only", action="store_true")
    output.add_argument("--full", dest="full", action="store_true")
    output.add_argument("--overview", dest="full", action="store_false")
    saving = parser.add_mutually_exclusive_group()
    saving.add_argument(
        "--save-output-json",
        dest="save_output_json",
        action="store_true",
    )
    saving.add_argument(
        "--no-save-output-json",
        dest="save_output_json",
        action="store_false",
    )
    parser.set_defaults(full=None, save_output_json=None)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    parser.add_argument("--all-rows", action="store_true")
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION_PATH)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE_PATH)
    parser.add_argument("--metric-name-field", default=DEFAULT_METRIC_NAME_FIELD)
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION)
    parser.add_argument(
        "--period-type",
        choices=("MONTH", "QUARTER", "YEAR"),
        default=DEFAULT_REPORT_PERIOD_TYPE,
    )
    parser.add_argument("--period-key", default=DEFAULT_REPORT_PERIOD_KEY)
    parser.add_argument("--unit", default=DEFAULT_UNIT)
    evaluation = parser.add_mutually_exclusive_group()
    evaluation.add_argument("--gold-review-output", type=Path)
    evaluation.add_argument("--validate-gold", type=Path)
    evaluation.add_argument("--evaluate-retrieval", type=Path)
    evaluation.add_argument("--semantic-pilot", type=Path)
    parser.add_argument("--deepseek-env-file", type=Path, default=DEFAULT_DEEPSEEK_ENV_PATH)
    return parser.parse_args()


def _run_evaluation_command(args: argparse.Namespace) -> tuple[int, Any] | None:
    selected = (
        args.validate_gold,
        args.evaluate_retrieval,
        args.semantic_pilot,
    )
    if not any(item is not None for item in selected):
        return None
    try:
        catalog = load_ontology_catalog(args.definition.resolve(), args.knowledge.resolve())
        if args.validate_gold is not None:
            result = validate_gold_review_file(args.validate_gold, catalog)
            exit_code = 0 if result.ready_for_p1 else 2
        elif args.evaluate_retrieval is not None:
            result = evaluate_retrieval_on_gold(
                load_gold_review_payload(args.evaluate_retrieval), catalog
            )
            exit_code = 0
        else:
            judge = DeepSeekSemanticJudge(env_file=args.deepseek_env_file.resolve())
            judge.validate_local_configuration()
            result = run_semantic_pilot_on_gold(
                load_gold_review_payload(args.semantic_pilot), catalog, judge
            )
            exit_code = (
                0
                if result.failed_count == 0
                and result.skipped_count == 0
                and result.map_existing_metric_accuracy == 1.0
                and result.hard_negative_false_match_count == 0
                and result.non_conservative_error_count == 0
                else 2
            )
    except (
        OSError,
        OntologyCatalogError,
        Phase25ReviewError,
        JudgeUnavailableError,
        ValueError,
    ) as exc:
        return 1, {
            "成功": False,
            "错误类型": type(exc).__name__,
            "错误": str(exc),
        }
    return exit_code, result.to_dict()


def main() -> int:
    args = parse_arguments()
    full_output = DEFAULT_FULL_OUTPUT if args.full is None else args.full
    evaluation_exit = _run_evaluation_command(args)
    if evaluation_exit is not None:
        exit_code, payload = evaluation_exit
        return emit_json_output(
            payload,
            args,
            mode="evaluation",
            exit_code=exit_code,
        )
    files = discover_files(args.path.resolve())
    if not files:
        print(json.dumps({"测试路径": str(args.path.resolve()), "错误": "没有找到可测试的 .xlsx 或 .csv 文件。"}, ensure_ascii=False, indent=2))
        return 1
    row_limit = (
        None
        if args.all_rows or DEFAULT_ALL_ROWS
        else max(args.preview_rows if args.preview_rows is not None else DEFAULT_PREVIEW_ROWS, 0)
    )
    datasets_only = args.mode == "preparation" and (DEFAULT_DATASETS_ONLY or args.datasets_only)
    if full_output:
        datasets_only = False
    catalog = None
    if args.mode in {"deterministic", "semantic"}:
        try:
            catalog = load_ontology_catalog(args.definition.resolve(), args.knowledge.resolve())
        except (OSError, OntologyCatalogError) as exc:
            print(json.dumps({"本体加载成功": False, "错误": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    judge = None
    if args.mode == "semantic" and DEFAULT_SEMANTIC_USE_LLM:
        judge = DeepSeekSemanticJudge(env_file=args.deepseek_env_file.resolve())

    reports = []
    datasets = []
    mapping_results = []
    failures = 0
    needs_binding = 0
    semantic_failures = 0
    for source_file in files:
        try:
            phase1 = curate_file(source_file, PipelineConfig())
            if datasets_only:
                datasets.extend(build_datasets_only_report(phase1, row_limit))
            elif args.mode == "preparation":
                reports.append(phase1.to_dict() if full_output else build_console_report(phase1, row_limit))
            else:
                assert catalog is not None
                for curated in phase1.curated_datasets:
                    structuring_request, resolution_request = build_mapping_requests(
                        curated,
                        args,
                        semantic_fallback=(
                            args.mode == "semantic" and DEFAULT_SEMANTIC_USE_LLM
                        ),
                    )
                    mapping_result = map_curated_observations(
                        curated,
                        structuring_request,
                        resolution_request,
                        catalog,
                        judge,
                    )
                    mapping_results.append(mapping_result)
                    status = mapping_result.structuring_result.report.structure_status
                    failures += int(status is StructureStatus.BLOCKED)
                    needs_binding += int(status is StructureStatus.NEEDS_BINDING)
                    semantic_failures += sum(
                        item.execution_status is ExecutionStatus.FAILED
                        for item in mapping_result.metric_resolution_result.semantic_resolutions
                    )
                    base = (
                        mapping_result.to_dict()
                        if full_output
                        else build_mapping_overview_report(mapping_result)
                    )
                    if args.mode == "semantic" and not full_output:
                        semantic, _ = build_semantic_console_report(mapping_result)
                        reports.append({"Data Mapping": base, "Semantic Resolution": semantic})
                    else:
                        reports.append(base)
        except (Phase1Error, ValueError) as exc:
            failures += 1
            reports.append({"文件": str(source_file), "成功": False, "错误": str(exc)})

    review_summary = None
    if args.gold_review_output is not None:
        assert catalog is not None
        assignments = {
            result.metric_resolution_result.resolution_run_id: review_assignment(result)
            for result in mapping_results
        }
        draft = build_gold_review_draft(mapping_results, catalog, assignments)
        destination = export_gold_review_draft(draft, args.gold_review_output)
        validation = validate_gold_review_payload(draft.to_dict(), catalog)
        review_summary = {
            "审核材料": str(destination),
            "Draft ID": draft.draft_id,
            "P1 门禁通过": validation.ready_for_p1,
        }

    output = datasets if datasets_only else {
        "Mode": args.mode,
        "发现文件数": len(files),
        "结果": reports,
        "审核材料": review_summary,
    }
    if isinstance(output, dict) and judge is not None:
        output["DeepSeek Diagnostics"] = list(judge.diagnostics)
    exit_code = 1 if failures or semantic_failures else 2 if needs_binding else 0
    return emit_json_output(
        output,
        args,
        mode=args.mode,
        exit_code=exit_code,
    )


if __name__ == "__main__":
    raise SystemExit(main())
