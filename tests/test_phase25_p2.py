from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from data_mapper import (
    Evidence,
    ExecutionStatus,
    JudgeOutput,
    JudgeUnavailableError,
    ResolutionReviewStatus,
    SemanticStatus,
    load_ontology_catalog,
    retrieve_metric_candidates,
    review_resolution,
    run_semantic_judgment,
)
from data_mapper.evaluation import resolve_gold_case_semantic_context


ROOT = Path(__file__).parents[1]
GOLD = ROOT / "tests" / "fixtures" / "phase25" / "phase25_p0_gold_truth.json"


class StaticJudge:
    name = "static-test-judge"
    version = "v1"
    prompt_version = "prompt-v1"
    model = None

    def __init__(self, output=None, error: Exception | None = None):
        self.output = output
        self.error = error

    def judge(self, candidate_set):
        if self.error is not None:
            raise self.error
        return self.output


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )


@pytest.fixture(scope="module")
def candidate_set(catalog):
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    case = next(
        item
        for item in payload["cases"]
        if item["report_family"] == "BALANCE_SHEET"
        and item["metric_subject"]["raw_label"] == "资 产 总 计"
    )
    return retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=resolve_gold_case_semantic_context(payload, case),
        source=case["source"],
        catalog=catalog,
    )


def test_successful_resolution_is_proposed_and_traceable(candidate_set, catalog) -> None:
    output = JudgeOutput(
        semantic_status=SemanticStatus.MAP_EXISTING,
        selected_metric_id="qc.total_assets",
        reason="名称与本体定义共同支持同一业务口径",
        supporting_evidence=(
            Evidence("same_business_concept", "oracle", "同一核算对象"),
        ),
        counter_evidence=(
            Evidence("broader_candidate", "oracle", "财务费用范围更宽"),
        ),
    )
    first = run_semantic_judgment(candidate_set, catalog, StaticJudge(output))
    second = run_semantic_judgment(candidate_set, catalog, StaticJudge(output))

    assert first.execution_status is ExecutionStatus.SUCCEEDED
    assert first.semantic_status is SemanticStatus.MAP_EXISTING
    assert first.review_status is ResolutionReviewStatus.PROPOSED
    assert first.selected_metric_id == "qc.total_assets"
    assert first.candidate_set_id == candidate_set.candidate_set_id
    assert first.resolution_id != second.resolution_id
    assert first.supporting_evidence and first.counter_evidence


def test_candidate_whitelist_violation_is_technical_failure(candidate_set, catalog) -> None:
    resolution = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
            {
                "semantic_status": "MAP_EXISTING",
                "selected_metric_id": "invented.metric",
                "reason": "invalid",
            }
        ),
    )

    assert resolution.execution_status is ExecutionStatus.FAILED
    assert resolution.semantic_status is None
    assert resolution.review_status is None
    assert resolution.selected_metric_id is None
    assert resolution.error_code == "INVALID_OUTPUT"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("timeout"), "TIMEOUT"),
        (JudgeUnavailableError("offline"), "UNAVAILABLE"),
        (RuntimeError("boom"), "JUDGE_EXCEPTION"),
    ],
)
def test_technical_failures_never_become_business_status(
    candidate_set, catalog, error, code
) -> None:
    resolution = run_semantic_judgment(
        candidate_set, catalog, StaticJudge(error=error)
    )

    assert resolution.execution_status is ExecutionStatus.FAILED
    assert resolution.semantic_status is None
    assert resolution.review_status is None
    assert resolution.error_code == code


def test_invalid_schema_is_failed_not_no_equivalent(candidate_set, catalog) -> None:
    resolution = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge({"selected_metric_id": None}),
    )

    assert resolution.execution_status is ExecutionStatus.FAILED
    assert resolution.semantic_status is None
    assert resolution.error_code == "INVALID_OUTPUT"


def test_pre_execution_revision_mismatch_is_skipped(candidate_set, catalog) -> None:
    mismatched = replace(candidate_set, ontology_revision="sha256:other")
    resolution = run_semantic_judgment(
        mismatched,
        catalog,
        StaticJudge(
            JudgeOutput(semantic_status=SemanticStatus.NO_EQUIVALENT)
        ),
    )

    assert resolution.execution_status is ExecutionStatus.SKIPPED
    assert resolution.semantic_status is None
    assert resolution.review_status is None
    assert resolution.error_code == "ONTOLOGY_REVISION_MISMATCH"


def test_only_successful_resolution_can_be_explicitly_confirmed(
    candidate_set, catalog
) -> None:
    proposed = run_semantic_judgment(
        candidate_set,
        catalog,
        StaticJudge(
            JudgeOutput(
                SemanticStatus.AMBIGUOUS,
                reason="证据不足",
                counter_evidence=(
                    Evidence("insufficient_context", "oracle", "无法唯一判断"),
                ),
            )
        ),
    )
    confirmed = review_resolution(proposed, ResolutionReviewStatus.CONFIRMED)
    failed = run_semantic_judgment(
        candidate_set, catalog, StaticJudge(error=TimeoutError())
    )

    assert confirmed.review_status is ResolutionReviewStatus.CONFIRMED
    with pytest.raises(ValueError, match="不能进入确认状态"):
        review_resolution(failed, ResolutionReviewStatus.CONFIRMED)
