"""Evaluation-only Gold review contracts and validation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..metric_resolution_contracts import (
    DataMappingResult,
    MetricDecision,
    MetricMatchStatus,
    OntologyCatalog,
)
from ..observation_contracts import RowRole
from .gold_contracts import (
    GoldReviewCase,
    GoldReviewDraft,
    GoldReviewIssue,
    GoldReviewSkippedItem,
    GoldReviewValidationReport,
    HumanGoldReview,
    ReviewDatasetAssignment,
    ReviewDatasetRole,
)


GOLD_REVIEW_FORMAT_VERSION = "phase2.5-gold-review-v2"
GOLD_REVIEW_EXPORT_VERSION = "phase2.5-p0-v2"
GOLD_REVIEW_DRAFT_STATUS = "AWAITING_INDEPENDENT_CONFIRMATION"

PROFIT_STATEMENT = "PROFIT_STATEMENT"
BALANCE_SHEET = "BALANCE_SHEET"
CASH_FLOW_STATEMENT = "CASH_FLOW_STATEMENT"
COST_EXPENSE_STATEMENT = "COST_EXPENSE_STATEMENT"

DEFAULT_REQUIRED_DEVELOPMENT_FAMILY = PROFIT_STATEMENT
DEFAULT_REQUIRED_HOLDOUT_FAMILIES = (
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    COST_EXPENSE_STATEMENT,
)

_SEMANTIC_STATUSES = {"MAP_EXISTING", "NO_EQUIVALENT", "AMBIGUOUS"}
_REVIEW_STATUSES = {"PROPOSED", "CONFIRMED", "REJECTED"}


class Phase25ReviewError(ValueError):
    """P0 审核材料无法可靠生成或读取。"""


def build_gold_review_draft(
    results: Iterable[DataMappingResult],
    catalog: OntologyCatalog,
    assignments: Mapping[str, ReviewDatasetAssignment],
    *,
    context_window: int = 2,
) -> GoldReviewDraft:
    """从冻结 Phase 2 结果构造不含算法答案的人工审核草案。"""

    result_items = tuple(results)
    if not result_items:
        raise Phase25ReviewError("至少需要一个 DataMappingResult")
    if context_window < 0:
        raise Phase25ReviewError("context_window 不能小于 0")

    cases: list[GoldReviewCase] = []
    skipped_items: list[GoldReviewSkippedItem] = []
    table_contexts: list[Mapping[str, Any]] = []
    for result in sorted(result_items, key=_result_sort_key):
        structuring = result.structuring_result
        resolution = result.metric_resolution_result
        if resolution.ontology_revision != catalog.ontology_revision:
            raise Phase25ReviewError(
                "DataMappingResult 与 OntologyCatalog 的 ontology_revision 不一致："
                f"{resolution.resolution_run_id}"
            )
        assignment = assignments.get(resolution.resolution_run_id)
        if assignment is None:
            raise Phase25ReviewError(
                "DataMappingResult 缺少人工审核数据集分配："
                f"{resolution.resolution_run_id}"
            )
        table_context = _build_table_context(result)
        table_contexts.append(table_context)
        plan_cases, plan_skipped = _review_items(
            result,
            assignment,
            context_window,
            str(table_context["table_context_id"]),
        )
        cases.extend(plan_cases)
        skipped_items.extend(plan_skipped)

    cases.sort(
        key=lambda item: (
            item.dataset_role.value,
            item.report_family,
            str(item.source.get("source_file", "")),
            str(item.source.get("sheet_name", "")),
            int(item.source.get("source_row", 0)),
            item.case_id,
        )
    )
    skipped_items.sort(
        key=lambda item: (
            str(item.source.get("source_file", "")),
            str(item.source.get("sheet_name", "")),
            int(item.source.get("source_row", 0)),
            item.subject_id,
        )
    )
    if not cases:
        raise Phase25ReviewError("DataMappingResult 中没有 eligible 未决 Metric")

    holdout_families = tuple(
        sorted(
            {
                assignment.report_family
                for assignment in assignments.values()
                if assignment.dataset_role is ReviewDatasetRole.HOLDOUT_CANDIDATE
            }
        )
    )
    mapping_run_ids = tuple(
        sorted(
            result.metric_resolution_result.resolution_run_id
            for result in result_items
        )
    )
    draft_id = _stable_id(
        "gold-review-draft",
        {
            "format_version": GOLD_REVIEW_FORMAT_VERSION,
            "export_version": GOLD_REVIEW_EXPORT_VERSION,
            "ontology_revision": catalog.ontology_revision,
            "context_window": context_window,
            "mapping_run_ids": mapping_run_ids,
            "assignments": {
                key: value.to_dict() for key, value in sorted(assignments.items())
            },
            "case_ids": [item.case_id for item in cases],
            "table_context_ids": [
                item["table_context_id"] for item in table_contexts
            ],
            "skipped_subject_ids": [item.subject_id for item in skipped_items],
        },
    )
    return GoldReviewDraft(
        format_version=GOLD_REVIEW_FORMAT_VERSION,
        export_version=GOLD_REVIEW_EXPORT_VERSION,
        draft_id=draft_id,
        draft_status=GOLD_REVIEW_DRAFT_STATUS,
        ontology_revision=catalog.ontology_revision,
        context_window=context_window,
        mapping_run_ids=mapping_run_ids,
        required_development_family=DEFAULT_REQUIRED_DEVELOPMENT_FAMILY,
        required_holdout_families=holdout_families,
        instructions=(
            "human_review 是兼容字段名；默认由独立人工审核，显式授权的其他 reviewer 必须记录 provenance。",
            "只有 review_status=CONFIRMED 且 include_in_gold_set=true 的案例属于 Gold Set。",
            "expected_metric_id 和 hard_negative_metric_ids 必须引用本文件同 revision 的 ontology_metrics。",
            "填写后运行只读校验；ready_for_p1=true 之前不得开始 Candidate Retrieval。",
        ),
        ontology_metrics=tuple(
            sorted(catalog.metrics, key=lambda item: item.current_metric_id)
        ),
        table_contexts=tuple(
            sorted(table_contexts, key=lambda item: str(item["mapping_run_id"]))
        ),
        cases=tuple(cases),
        skipped_items=tuple(skipped_items),
    )


def export_gold_review_draft(
    draft: GoldReviewDraft,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """写出审核草案；默认拒绝覆盖已有人工填写结果。"""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as stream:
        json.dump(draft.to_dict(), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return destination


def load_gold_review_payload(path: str | Path) -> Mapping[str, Any]:
    source = Path(path).expanduser().resolve(strict=True)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Phase25ReviewError(f"Gold 审核文件不是有效 JSON：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise Phase25ReviewError("Gold 审核文件顶层必须是 JSON object")
    return payload


def validate_gold_review_file(
    path: str | Path,
    catalog: OntologyCatalog,
) -> GoldReviewValidationReport:
    return validate_gold_review_payload(load_gold_review_payload(path), catalog)


def validate_gold_review_payload(
    payload: Mapping[str, Any],
    catalog: OntologyCatalog,
) -> GoldReviewValidationReport:
    """只读校验人工结果和 P0→P1 的最低结构门禁。"""

    issues: list[GoldReviewIssue] = []
    format_version = _optional_string(payload.get("format_version"))
    ontology_revision = _optional_string(payload.get("ontology_revision"))
    if format_version != GOLD_REVIEW_FORMAT_VERSION:
        issues.append(
            GoldReviewIssue(
                code="FORMAT_VERSION_MISMATCH",
                message=f"format_version 必须为 {GOLD_REVIEW_FORMAT_VERSION}",
            )
        )
    if ontology_revision != catalog.ontology_revision:
        issues.append(
            GoldReviewIssue(
                code="ONTOLOGY_REVISION_MISMATCH",
                message="审核材料与当前 OntologyCatalog 的 ontology_revision 不一致",
            )
        )
    expected_ontology_metrics = [
        item.to_dict()
        for item in sorted(catalog.metrics, key=lambda item: item.current_metric_id)
    ]
    if payload.get("ontology_metrics") != expected_ontology_metrics:
        issues.append(
            GoldReviewIssue(
                code="ONTOLOGY_REFERENCE_MISMATCH",
                message="审核材料内嵌的 ontology_metrics 与当前 OntologyCatalog 不一致",
            )
        )

    table_contexts_by_id: dict[str, Mapping[str, Any]] = {}
    raw_table_contexts = payload.get("table_contexts")
    if not isinstance(raw_table_contexts, list):
        issues.append(
            GoldReviewIssue(
                code="TABLE_CONTEXTS_NOT_LIST",
                message="table_contexts 必须是 JSON array",
            )
        )
        raw_table_contexts = []
    for index, item in enumerate(raw_table_contexts):
        if not isinstance(item, Mapping):
            issues.append(
                GoldReviewIssue(
                    code="TABLE_CONTEXT_NOT_OBJECT",
                    message=f"table_contexts[{index}] 必须是 JSON object",
                )
            )
            continue
        table_context_id = _optional_string(item.get("table_context_id"))
        if not table_context_id:
            issues.append(
                GoldReviewIssue(
                    code="TABLE_CONTEXT_ID_REQUIRED",
                    message=f"table_contexts[{index}].table_context_id 不能为空",
                )
            )
            continue
        if table_context_id in table_contexts_by_id:
            issues.append(
                GoldReviewIssue(
                    code="DUPLICATE_TABLE_CONTEXT_ID",
                    message="table_context_id 重复",
                )
            )
            continue
        if table_context_id != _table_context_fingerprint(item):
            issues.append(
                GoldReviewIssue(
                    code="TABLE_CONTEXT_FINGERPRINT_MISMATCH",
                    message="同表主体或报表注释已改变；请重新导出 Gold 审核材料",
                )
            )
        table_contexts_by_id[table_context_id] = item

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        issues.append(
            GoldReviewIssue(code="CASES_NOT_LIST", message="cases 必须是 JSON array")
        )
        raw_cases = []

    metric_ids = {item.current_metric_id for item in catalog.metrics}
    seen_case_ids: set[str] = set()
    seen_decision_ids: set[str] = set()
    pending_count = 0
    included_count = 0
    status_counts = {status: 0 for status in sorted(_SEMANTIC_STATUSES)}
    coverage: dict[str, int] = {}
    has_map_existing = False
    has_hard_negative = False

    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            issues.append(
                GoldReviewIssue(
                    code="CASE_NOT_OBJECT",
                    message=f"cases[{index}] 必须是 JSON object",
                )
            )
            continue
        case_id = _optional_string(raw_case.get("case_id"))
        case_ref = case_id or f"cases[{index}]"
        if not case_id:
            issues.append(
                GoldReviewIssue("CASE_ID_REQUIRED", "case_id 不能为空", case_ref)
            )
        elif case_id in seen_case_ids:
            issues.append(
                GoldReviewIssue("DUPLICATE_CASE_ID", "case_id 重复", case_id)
            )
        seen_case_ids.add(case_ref)

        decision_id = _optional_string(raw_case.get("source_metric_decision_id"))
        if not decision_id:
            issues.append(
                GoldReviewIssue(
                    "SOURCE_DECISION_ID_REQUIRED",
                    "source_metric_decision_id 不能为空",
                    case_ref,
                )
            )
        elif decision_id in seen_decision_ids:
            issues.append(
                GoldReviewIssue(
                    "DUPLICATE_SOURCE_DECISION_ID",
                    "同一 source_metric_decision_id 不能重复进入审核材料",
                    case_ref,
                )
            )
        seen_decision_ids.add(decision_id or case_ref)

        if raw_case.get("ontology_revision") != ontology_revision:
            issues.append(
                GoldReviewIssue(
                    "CASE_REVISION_MISMATCH",
                    "案例 ontology_revision 与审核材料不一致",
                    case_ref,
                )
            )
        if raw_case.get("source_fingerprint") != _case_source_fingerprint(raw_case):
            issues.append(
                GoldReviewIssue(
                    "CASE_SOURCE_FINGERPRINT_MISMATCH",
                    "案例来源或上下文字段已改变；请从同一 Phase 2 结果重新导出",
                    case_ref,
                )
            )

        semantic_context = raw_case.get("semantic_context")
        if not isinstance(semantic_context, Mapping):
            issues.append(
                GoldReviewIssue(
                    "SEMANTIC_CONTEXT_REQUIRED",
                    "案例 semantic_context 必须是 JSON object",
                    case_ref,
                )
            )
        else:
            table_context_id = _optional_string(
                semantic_context.get("table_context_id")
            )
            table_context = table_contexts_by_id.get(table_context_id or "")
            if table_context is None:
                issues.append(
                    GoldReviewIssue(
                        "TABLE_CONTEXT_REFERENCE_INVALID",
                        "案例必须引用当前审核材料内有效的 table_context_id",
                        case_ref,
                    )
                )
            elif table_context.get("mapping_run_id") != raw_case.get(
                "mapping_run_id"
            ):
                issues.append(
                    GoldReviewIssue(
                        "TABLE_CONTEXT_MAPPING_RUN_MISMATCH",
                        "案例与 table_context 的 mapping_run_id 不一致",
                        case_ref,
                    )
                )

        report_family = _optional_string(raw_case.get("report_family"))
        dataset_role = _optional_string(raw_case.get("dataset_role"))
        if dataset_role not in {item.value for item in ReviewDatasetRole}:
            issues.append(
                GoldReviewIssue(
                    "INVALID_DATASET_ROLE", "dataset_role 无效", case_ref
                )
            )

        review = raw_case.get("human_review")
        if not isinstance(review, Mapping):
            issues.append(
                GoldReviewIssue(
                    "HUMAN_REVIEW_NOT_OBJECT",
                    "human_review 必须是 JSON object",
                    case_ref,
                )
            )
            continue

        review_status = review.get("review_status")
        include = review.get("include_in_gold_set")
        if review_status is None or review_status == "PROPOSED":
            pending_count += 1
        elif review_status not in _REVIEW_STATUSES:
            issues.append(
                GoldReviewIssue(
                    "INVALID_REVIEW_STATUS", "review_status 无效", case_ref
                )
            )
            continue

        if include is True and review_status != "CONFIRMED":
            issues.append(
                GoldReviewIssue(
                    "INCLUDED_CASE_NOT_CONFIRMED",
                    "include_in_gold_set=true 的案例必须先由人工 CONFIRMED",
                    case_ref,
                )
            )
            continue
        if review_status == "REJECTED" and include is True:
            issues.append(
                GoldReviewIssue(
                    "REJECTED_CASE_INCLUDED",
                    "REJECTED 案例不能进入 Gold Set",
                    case_ref,
                )
            )
            continue
        if review_status != "CONFIRMED":
            continue

        if not _optional_string(review.get("reviewer")):
            issues.append(
                GoldReviewIssue(
                    "CONFIRMED_REVIEWER_REQUIRED",
                    "CONFIRMED 案例必须填写 reviewer",
                    case_ref,
                )
            )
        if not _optional_string(review.get("review_basis")):
            issues.append(
                GoldReviewIssue(
                    "CONFIRMED_REVIEW_BASIS_REQUIRED",
                    "CONFIRMED 案例必须填写 review_basis",
                    case_ref,
                )
            )
        reviewed_at = _optional_string(review.get("reviewed_at"))
        if not reviewed_at or not _valid_iso_datetime(reviewed_at):
            issues.append(
                GoldReviewIssue(
                    "CONFIRMED_REVIEWED_AT_REQUIRED",
                    "CONFIRMED 案例必须填写 ISO 8601 reviewed_at",
                    case_ref,
                )
            )
        if include not in {True, False}:
            issues.append(
                GoldReviewIssue(
                    "CONFIRMED_INCLUDE_DECISION_REQUIRED",
                    "CONFIRMED 案例必须明确 include_in_gold_set",
                    case_ref,
                )
            )
            continue
        if include is False:
            continue

        semantic_status = review.get("expected_semantic_status")
        if semantic_status not in _SEMANTIC_STATUSES:
            issues.append(
                GoldReviewIssue(
                    "INVALID_SEMANTIC_STATUS",
                    "纳入 Gold Set 的案例必须填写有效 expected_semantic_status",
                    case_ref,
                )
            )
            continue

        expected_metric_id = _optional_string(review.get("expected_metric_id"))
        allowed_metric_ids = _string_list(
            review.get("allowed_metric_ids"),
            "allowed_metric_ids",
            case_ref,
            issues,
        )
        hard_negative_ids = _string_list(
            review.get("hard_negative_metric_ids"),
            "hard_negative_metric_ids",
            case_ref,
            issues,
        )
        for metric_id in (*allowed_metric_ids, *hard_negative_ids):
            if metric_id not in metric_ids:
                issues.append(
                    GoldReviewIssue(
                        "UNKNOWN_REVIEW_METRIC_ID",
                        f"人工填写的 Metric ID 不存在于当前 revision：{metric_id}",
                        case_ref,
                    )
                )

        if semantic_status == "MAP_EXISTING":
            if not expected_metric_id:
                issues.append(
                    GoldReviewIssue(
                        "EXPECTED_METRIC_ID_REQUIRED",
                        "MAP_EXISTING 必须填写 expected_metric_id",
                        case_ref,
                    )
                )
            elif expected_metric_id not in metric_ids:
                issues.append(
                    GoldReviewIssue(
                        "UNKNOWN_EXPECTED_METRIC_ID",
                        "expected_metric_id 不存在于当前 ontology revision",
                        case_ref,
                    )
                )
            if expected_metric_id and expected_metric_id in hard_negative_ids:
                issues.append(
                    GoldReviewIssue(
                        "EXPECTED_METRIC_IS_HARD_NEGATIVE",
                        "expected_metric_id 不能同时标为困难负样本",
                        case_ref,
                    )
                )
        elif expected_metric_id is not None:
            issues.append(
                GoldReviewIssue(
                    "EXPECTED_METRIC_ID_FORBIDDEN",
                    "NO_EQUIVALENT / AMBIGUOUS 不得填写 expected_metric_id",
                    case_ref,
                )
            )

        included_count += 1
        status_counts[str(semantic_status)] += 1
        coverage[report_family or ""] = coverage.get(report_family or "", 0) + 1
        has_map_existing = has_map_existing or (
            semantic_status == "MAP_EXISTING" and bool(expected_metric_id)
        )
        has_hard_negative = has_hard_negative or bool(hard_negative_ids)

    if not has_map_existing:
        issues.append(
            GoldReviewIssue(
                "MAP_EXISTING_REQUIRED",
                "P1 前至少需要一个独立确认的 MAP_EXISTING 案例",
            )
        )
    if not has_hard_negative:
        issues.append(
            GoldReviewIssue(
                "HARD_NEGATIVE_REQUIRED",
                "P1 前至少需要一个独立确认的困难负样本",
            )
        )
    for family in DEFAULT_REQUIRED_HOLDOUT_FAMILIES:
        if coverage.get(family, 0) == 0:
            issues.append(
                GoldReviewIssue(
                    "HOLDOUT_FAMILY_REQUIRED",
                    f"P1 前至少需要一个独立确认的跨表保留案例：{family}",
                )
            )

    return GoldReviewValidationReport(
        format_version=format_version,
        ontology_revision=ontology_revision,
        ready_for_p1=not issues,
        total_case_count=len(raw_cases),
        pending_case_count=pending_count,
        confirmed_included_count=included_count,
        confirmed_semantic_status_counts=status_counts,
        confirmed_dataset_coverage=dict(sorted(coverage.items())),
        issues=tuple(issues),
    )


def _review_items(
    result: DataMappingResult,
    assignment: ReviewDatasetAssignment,
    context_window: int,
    table_context_id: str,
) -> tuple[list[GoldReviewCase], list[GoldReviewSkippedItem]]:
    structuring = result.structuring_result
    resolution = result.metric_resolution_result
    subjects = structuring.row_subjects
    positions = {item.subject_id: index for index, item in enumerate(subjects)}
    table_value_context = tuple(
        {
            "value_field": item.value_field,
            "business_scope": _binding_snapshot(item.business_scope),
            "period_type": item.period_type,
            "period_basis": item.period_basis,
            "unit": _binding_snapshot(item.unit),
            "binding_complete": item.binding_complete,
        }
        for item in structuring.table_mapping_plan.value_fields
    )
    cases: list[GoldReviewCase] = []
    skipped_items: list[GoldReviewSkippedItem] = []
    decisions = {
        item.subject.subject_id: item
        for item in resolution.deterministic_decisions
    }
    for subject in subjects:
        decision = decisions.get(subject.subject_id)
        if decision is None or not _eligible(decision):
            skipped_items.append(_skipped_item(subject, decision))
            continue
        position = positions[subject.subject_id]
        previous = subjects[max(0, position - context_window) : position]
        following = subjects[position + 1 : position + 1 + context_window]
        source = {
            "source_file": subject.source_file,
            "sheet_name": subject.sheet_name,
            "source_row": subject.source_row,
            "source_column": subject.source_column,
        }
        metric_subject = {
            "subject_id": subject.subject_id,
            "raw_label": subject.raw_label,
            "comparison_name": subject.comparison_name,
            "row_role": subject.row_role.value,
            "extraction_evidence": [item.to_dict() for item in subject.evidence],
        }
        semantic_context = {
            "nearest_group": subject.context.get("group_label"),
            "source_context": dict(subject.context),
            "previous_subjects": [_subject_snapshot(item) for item in previous],
            "following_subjects": [_subject_snapshot(item) for item in following],
            "table_context_id": table_context_id,
            "table_value_context": table_value_context,
        }
        immutable_source = {
            "source_metric_decision_id": decision.decision_id,
            "ontology_revision": decision.ontology_revision,
            "dataset_role": assignment.dataset_role.value,
            "report_family": assignment.report_family,
            "mapping_run_id": resolution.resolution_run_id,
            "curated_id": structuring.curated_id,
            "raw_dataset_id": structuring.raw_dataset_id,
            "raw_version_id": structuring.raw_version_id,
            "phase2_status": decision.status.value,
            "source": source,
            "metric_subject": metric_subject,
            "semantic_context": semantic_context,
        }
        cases.append(
            GoldReviewCase(
                case_id=_stable_id(
                    "gold-review-case",
                    {
                        "source_metric_decision_id": decision.decision_id,
                        "ontology_revision": decision.ontology_revision,
                    },
                ),
                source_fingerprint=_stable_id(
                    "gold-review-source", immutable_source
                ),
                source_metric_decision_id=decision.decision_id,
                ontology_revision=decision.ontology_revision,
                dataset_role=assignment.dataset_role,
                report_family=assignment.report_family,
                mapping_run_id=resolution.resolution_run_id,
                curated_id=structuring.curated_id,
                raw_dataset_id=structuring.raw_dataset_id,
                raw_version_id=structuring.raw_version_id,
                phase2_status=decision.status.value,
                source=source,
                metric_subject=metric_subject,
                semantic_context=semantic_context,
                human_review=HumanGoldReview(),
            )
        )
    return cases, skipped_items


def _eligible(decision: MetricDecision) -> bool:
    return (
        decision.subject.row_role is RowRole.METRIC
        and decision.status
        in {MetricMatchStatus.UNMATCHED, MetricMatchStatus.AMBIGUOUS}
    )


def _skipped_item(subject: Any, decision: MetricDecision | None) -> GoldReviewSkippedItem:
    if subject.row_role is not RowRole.METRIC:
        reason = f"ROW_ROLE_{subject.row_role.value}"
    elif decision is None:
        reason = "METRIC_DECISION_NOT_AVAILABLE"
    else:
        reason = f"PHASE2_{decision.status.value}"
    return GoldReviewSkippedItem(
        subject_id=subject.subject_id,
        source_metric_decision_id=decision.decision_id if decision is not None else None,
        phase2_status=decision.status.value if decision is not None else None,
        execution_status="SKIPPED",
        reason=reason,
        source={
            "source_file": subject.source_file,
            "sheet_name": subject.sheet_name,
            "source_row": subject.source_row,
            "source_column": subject.source_column,
        },
        metric_subject={
            "raw_label": subject.raw_label,
            "comparison_name": subject.comparison_name,
            "row_role": subject.row_role.value,
        },
    )


def _subject_snapshot(subject: Any) -> dict[str, Any]:
    return {
        "raw_label": subject.raw_label,
        "comparison_name": subject.comparison_name,
        "row_role": subject.row_role.value,
        "source_row": subject.source_row,
    }


def _same_table_metric_snapshot(
    subject: Any,
    decision: MetricDecision | None,
) -> dict[str, Any]:
    selected_metric = decision.selected_metric if decision is not None else None
    return {
        "subject_id": subject.subject_id,
        "raw_label": subject.raw_label,
        "comparison_name": subject.comparison_name,
        "row_role": subject.row_role.value,
        "source_row": subject.source_row,
        "phase2_status": decision.status.value if decision is not None else None,
        "current_metric_id": (
            selected_metric.current_metric_id if selected_metric is not None else None
        ),
    }


def _build_table_context(result: DataMappingResult) -> Mapping[str, Any]:
    structuring = result.structuring_result
    resolution = result.metric_resolution_result
    decisions = {
        item.subject.subject_id: item
        for item in resolution.deterministic_decisions
    }
    content = {
        "mapping_run_id": resolution.resolution_run_id,
        "same_table_metric_subjects": [
            _same_table_metric_snapshot(subject, decisions.get(subject.subject_id))
            for subject in structuring.row_subjects
            if subject.row_role is RowRole.METRIC
        ],
        "report_notes": [
            _subject_snapshot(subject)
            for subject in structuring.row_subjects
            if subject.row_role is RowRole.NOTE
        ],
    }
    return {
        "table_context_id": _stable_id("gold-table-context", content),
        **content,
    }


def resolve_gold_case_semantic_context(
    payload: Mapping[str, Any],
    case: Mapping[str, Any],
) -> Mapping[str, Any]:
    """把 Gold case 的局部上下文与同表只读快照组合成运行时 Context。"""

    context = case.get("semantic_context")
    if not isinstance(context, Mapping):
        raise Phase25ReviewError("Gold case semantic_context 无效")
    table_context_id = _optional_string(context.get("table_context_id"))
    table_contexts = payload.get("table_contexts")
    if not table_context_id or not isinstance(table_contexts, list):
        raise Phase25ReviewError("Gold case 缺少有效 table_context 引用")
    table_context = next(
        (
            item
            for item in table_contexts
            if isinstance(item, Mapping)
            and item.get("table_context_id") == table_context_id
        ),
        None,
    )
    if table_context is None:
        raise Phase25ReviewError("Gold case 引用的 table_context 不存在")
    if table_context.get("mapping_run_id") != case.get("mapping_run_id"):
        raise Phase25ReviewError("Gold case 与 table_context mapping_run_id 不一致")
    return {
        **dict(context),
        "same_table_metric_subjects": [
            dict(item)
            for item in table_context.get("same_table_metric_subjects") or ()
            if isinstance(item, Mapping)
            and item.get("subject_id")
            != (case.get("metric_subject") or {}).get("subject_id")
        ],
        "report_notes": [
            dict(item)
            for item in table_context.get("report_notes") or ()
            if isinstance(item, Mapping)
        ],
    }


def _binding_snapshot(binding: Any) -> dict[str, Any] | None:
    if binding is None:
        return None
    return {"kind": binding.kind, "value": binding.value, "field": binding.field}


def _result_sort_key(result: DataMappingResult) -> tuple[str, str, str]:
    structuring = result.structuring_result
    return (
        structuring.table_mapping_plan.source_file,
        structuring.table_mapping_plan.sheet_name,
        result.metric_resolution_result.resolution_run_id,
    )


def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return f"{namespace}:" + hashlib.sha256(encoded).hexdigest()


def _case_source_fingerprint(raw_case: Mapping[str, Any]) -> str:
    immutable_source = {
        key: raw_case.get(key)
        for key in (
            "source_metric_decision_id",
            "ontology_revision",
            "dataset_role",
            "report_family",
            "mapping_run_id",
            "curated_id",
            "raw_dataset_id",
            "raw_version_id",
            "phase2_status",
            "source",
            "metric_subject",
            "semantic_context",
        )
    }
    return _stable_id("gold-review-source", immutable_source)


def _table_context_fingerprint(raw_context: Mapping[str, Any]) -> str:
    content = {
        key: raw_context.get(key)
        for key in (
            "mapping_run_id",
            "same_table_metric_subjects",
            "report_notes",
        )
    }
    return _stable_id("gold-table-context", content)


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"无法稳定序列化 {type(value).__name__}")


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _valid_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _string_list(
    value: Any,
    field_name: str,
    case_id: str,
    issues: list[GoldReviewIssue],
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        issues.append(
            GoldReviewIssue(
                "INVALID_METRIC_ID_LIST",
                f"{field_name} 必须是字符串数组",
                case_id,
            )
        )
        return ()
    return tuple(item for item in value if item)
