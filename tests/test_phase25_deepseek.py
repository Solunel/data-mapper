from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_mapper import (
    DeepSeekSemanticJudge,
    Evidence,
    ExecutionStatus,
    JudgeOutput,
    JudgeUnavailableError,
    ResolutionReviewStatus,
    SemanticStatus,
    build_deepseek_judge_request,
    load_ontology_catalog,
    resolve_gold_case_semantic_context,
    retrieve_metric_candidates,
    run_semantic_judgment,
    run_semantic_pilot_on_gold,
)


ROOT = Path(__file__).parents[1]
GOLD = ROOT / "tests" / "fixtures" / "phase25" / "phase25_p0_gold_truth.json"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )


@pytest.fixture(scope="module")
def gold_payload():
    return json.loads(GOLD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def candidate_set(gold_payload, catalog):
    case = next(
        item
        for item in gold_payload["cases"]
        if item["metric_subject"]["raw_label"] == "利息费用"
    )
    return retrieve_metric_candidates(
        source_metric_decision_id=case["source_metric_decision_id"],
        ontology_revision=case["ontology_revision"],
        metric_subject=case["metric_subject"],
        semantic_context=resolve_gold_case_semantic_context(gold_payload, case),
        source=case["source"],
        catalog=catalog,
    )


def test_request_uses_json_mode_and_minimal_data_boundary(candidate_set) -> None:
    request = build_deepseek_judge_request(candidate_set)
    serialized = json.dumps(request, ensure_ascii=False)

    assert request["response_format"] == {"type": "json_object"}
    assert request["thinking"] == {"type": "enabled"}
    assert "qc.interest_expense" in serialized
    assert "same_table_candidate_conflicts" in serialized
    assert "2.△利息支出" in serialized
    assert "qc.interest_expense" in serialized
    assert "report_notes" in serialized
    assert "金融类企业专用" in serialized
    assert "same_table_metric_subjects" not in serialized
    assert "actual_value" not in serialized
    assert "source_file" not in serialized
    assert "mapping_run_id" not in serialized
    assert "curated_id" not in serialized
    assert "raw_dataset_id" not in serialized
    assert "DEEPSEEK_API_KEY" not in serialized


def test_adapter_reads_ignored_env_and_parses_structured_output(
    tmp_path, monkeypatch, candidate_set, catalog
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_BASE_URL=https://example.invalid\n"
        "DEEPSEEK_MODEL=deepseek-test\n"
        "DEEPSEEK_API_KEY=test-only-token\n",
        encoding="utf-8",
    )
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(
            url=url,
            authorization_present=headers.get("Authorization", "").startswith(
                "Bearer "
            ),
            payload=payload,
            timeout=timeout,
        )
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "semantic_status": "NO_EQUIVALENT",
                                "selected_metric_id": None,
                                "reason": "同表独立项目和报表口径表明并非同一指标",
                                "supporting_evidence": [],
                                "counter_evidence": [
                                    {
                                        "code": "same_table_metric_conflict",
                                        "source": "llm_semantic_judge",
                                        "message": "同表利息支出已映射该 Metric",
                                        "details": {},
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    },
                }
            ]
        }

    judge = DeepSeekSemanticJudge(env_file=env_file, transport=transport)
    resolution = run_semantic_judgment(candidate_set, catalog, judge)

    assert captured["url"] == "https://example.invalid/chat/completions"
    assert captured["authorization_present"]
    assert captured["payload"]["model"] == "deepseek-test"
    assert resolution.execution_status is ExecutionStatus.SUCCEEDED
    assert resolution.semantic_status is SemanticStatus.NO_EQUIVALENT
    assert resolution.selected_metric_id is None
    assert resolution.review_status is ResolutionReviewStatus.PROPOSED


def test_missing_key_is_unavailable_without_network(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_BASE_URL=https://api.deepseek.com\n"
        "DEEPSEEK_MODEL=deepseek-v4-flash\n"
        "DEEPSEEK_API_KEY=\n",
        encoding="utf-8",
    )
    judge = DeepSeekSemanticJudge(env_file=env_file)

    with pytest.raises(JudgeUnavailableError, match="未配置"):
        judge.validate_local_configuration()


def test_invalid_provider_json_is_technical_failure(
    tmp_path, monkeypatch, candidate_set, catalog
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-token")

    def transport(url, headers, payload, timeout):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "not-json"},
                }
            ]
        }

    judge = DeepSeekSemanticJudge(
        env_file=tmp_path / ".env",
        transport=transport,
    )
    resolution = run_semantic_judgment(candidate_set, catalog, judge)

    assert resolution.execution_status is ExecutionStatus.FAILED
    assert resolution.semantic_status is None
    assert resolution.review_status is None
    assert resolution.error_code == "INVALID_OUTPUT"


def test_gold_pilot_is_read_only_and_keeps_all_llm_results_proposed(
    gold_payload, catalog
) -> None:
    expected = {
        case["source_metric_decision_id"]: case["human_review"]
        for case in gold_payload["cases"]
        if case["human_review"]["review_status"] == "CONFIRMED"
        and case["human_review"]["include_in_gold_set"] is True
    }

    class OracleJudge:
        name = "oracle-test-judge"
        version = "v1"
        prompt_version = "oracle-prompt-v1"
        model = None

        def judge(self, candidate_set):
            review = expected[candidate_set.source_metric_decision_id]
            return JudgeOutput(
                semantic_status=SemanticStatus(
                    review["expected_semantic_status"]
                ),
                selected_metric_id=review["expected_metric_id"],
                reason="测试 oracle 按已确认 Gold 返回结构化结论",
                supporting_evidence=(
                    Evidence(
                        "oracle_gold",
                        "test_oracle",
                        "仅验证 Pilot 编排与状态边界",
                    ),
                ),
            )

    before = json.dumps(gold_payload, ensure_ascii=False, sort_keys=True)
    report = run_semantic_pilot_on_gold(gold_payload, catalog, OracleJudge())

    assert report.included_case_count == 12
    assert report.succeeded_count == 12
    assert report.failed_count == 0
    assert report.semantic_status_accuracy == 1.0
    assert report.map_existing_metric_accuracy == 1.0
    assert report.hard_negative_false_match_count == 0
    assert report.conservative_abstention_count == 0
    assert report.non_conservative_error_count == 0
    assert all(
        item.resolution.review_status is ResolutionReviewStatus.PROPOSED
        for item in report.case_results
    )
    assert json.dumps(gold_payload, ensure_ascii=False, sort_keys=True) == before


def test_no_equivalent_to_ambiguous_is_reported_as_conservative_abstention(
    gold_payload, catalog
) -> None:
    expected = {
        case["source_metric_decision_id"]: case["human_review"]
        for case in gold_payload["cases"]
        if case["human_review"]["review_status"] == "CONFIRMED"
        and case["human_review"]["include_in_gold_set"] is True
    }

    class CautiousJudge:
        name = "cautious-test-judge"
        version = "v1"
        prompt_version = "cautious-prompt-v1"
        model = None

        def judge(self, candidate_set):
            review = expected[candidate_set.source_metric_decision_id]
            expected_status = SemanticStatus(review["expected_semantic_status"])
            status = (
                SemanticStatus.AMBIGUOUS
                if expected_status is SemanticStatus.NO_EQUIVALENT
                else expected_status
            )
            return JudgeOutput(
                semantic_status=status,
                selected_metric_id=(
                    review["expected_metric_id"]
                    if status is SemanticStatus.MAP_EXISTING
                    else None
                ),
                reason="候选范围不足时保守拒答",
                counter_evidence=(
                    Evidence(
                        "candidate_scope_insufficient",
                        "test_oracle",
                        "Top-K 不足以证明本体缺口",
                    ),
                ),
            )

    report = run_semantic_pilot_on_gold(
        gold_payload,
        catalog,
        CautiousJudge(),
    )

    assert report.map_existing_metric_accuracy == 1.0
    assert report.conservative_abstention_count == 10
    assert report.non_conservative_error_count == 0
    assert report.hard_negative_false_match_count == 0
