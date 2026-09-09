"""Phase 1 个人测试入口。

用法示例：
    python a.py
    python a.py "E:\\Desktop\\企业课题\\表格\\某份报表.xlsx"
    python a.py "E:\\Desktop\\企业课题\\表格" --preview-rows 3
    python a.py "某份报表.xlsx" --datasets-only --all-rows
    python a.py "某份报表.xlsx" --full
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

from data_mapper import Phase1Error, PipelineConfig, curate_file  # noqa: E402


# ===== PyCharm 右键运行配置：通常只需要修改这里 =====
DEFAULT_TEST_PATH = Path(
    r"E:\Code_Repo\data-mapper\outputs\phase1指标在行_20260908\财务快报-利润表-模拟数据.xlsx"
)
DEFAULT_DATASETS_ONLY = True
DEFAULT_PREVIEW_ROWS = 20
DEFAULT_ALL_ROWS = True
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
        description="用 Phase 1 解析单个报表或一个目录中的全部 Excel/CSV。"
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_TEST_PATH,
        help=f"待测试的文件或目录，默认：{DEFAULT_TEST_PATH}",
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
        help="输出完整 Phase 1 契约，适合进一步排查；默认输出精简报告。",
    )
    parser.add_argument(
        "--all-rows",
        action="store_true",
        help="展示全部数据行；不指定时由 --preview-rows 控制展示数量。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    input_path = args.path.expanduser().resolve()
    datasets_only_mode = DEFAULT_DATASETS_ONLY or args.datasets_only
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
            else:
                report = (
                    result.to_dict()
                    if args.full
                    else build_console_report(result, preview_rows=row_limit)
                )
                reports.append(report)
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

    summary = {
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
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    return 1 if parse_failure_count or quality_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
