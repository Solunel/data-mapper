from __future__ import annotations

import json
import hashlib
from pathlib import Path

from refactor_baseline import build_r0_business_snapshot


ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "tests" / "baselines" / "clean_refactor_r0.json"


def test_clean_refactor_business_behavior_matches_r0_snapshot() -> None:
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    actual = build_r0_business_snapshot()
    encoded = json.dumps(
        actual,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert hashlib.sha256(encoded).hexdigest() == expected["snapshot_sha256"]
    assert actual["assets"] == expected["assets"]
    assert actual["phase2"]["ready_four_metric_states"]["row_roles"] == [
        "METRIC",
        "METRIC",
        "METRIC",
        "METRIC",
        "GROUP",
        "NOTE",
        "UNKNOWN",
    ]
    assert set(
        actual["phase2"]["ready_four_metric_states"]["decision_statuses"]
    ) == {"MATCHED", "UNMATCHED", "ONTOLOGY_GAP"}
    assert actual["phase2"]["ambiguous"]["status"] == "AMBIGUOUS"
    partial = actual["phase2"]["needs_binding_partial_projection"]
    assert partial["structure_status"] == "NEEDS_BINDING"
    assert len(partial["projected"]) == 1
    assert len(partial["unprojected_values"]) == 1
    assert actual["phase2"]["blocked"]["candidate_count"] == 0
    assert actual["phase25"]["retrieval_evaluation"]["recall_at_3"] == 1.0
    assert actual["phase25"]["retrieval_evaluation"]["recall_at_5"] == 1.0
    assert actual["phase25"]["interest_expense_fixed_judgment"][
        "review_status"
    ] == "PROPOSED"
    conflicts = actual["phase25"]["interest_expense_candidate_set"][
        "semantic_context"
    ]["same_table_candidate_conflicts"]
    assert any(
        item["current_metric_id"] == "qc.interest_expense"
        and item["source_row"] == 29
        for item in conflicts
    )
