from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from data_mapper.ontology_catalog import build_ontology_catalog


def load_metric_gold_catalog(
    definition_path: Path,
    _knowledge_path: Path,
):
    """Rebuild the ontology revision to which the historical Metric Gold is pinned."""

    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    gold_path = (
        Path(__file__).parent
        / "fixtures"
        / "phase25"
        / "phase25_p0_gold_truth.json"
    )
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    knowledge = {
        "Metric": [
            {
                "id": item["current_metric_id"],
                "name_cn": item["name_cn"],
                "aliases": item["aliases"],
                "definition_cn": item["definition_cn"],
                "business_labels": item["business_labels"],
                "value_semantics": item["value_semantics"],
                "status": item["status"],
                "version": item["version"],
            }
            for item in gold["ontology_metrics"]
        ]
    }
    catalog = build_ontology_catalog(definition, knowledge)
    # Gold 保存的是经审核的 Catalog 投影而非原始 Knowledge；revision 也按快照固定。
    return replace(catalog, ontology_revision=gold["ontology_revision"])
