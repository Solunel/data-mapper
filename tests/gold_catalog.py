from __future__ import annotations

import json
from pathlib import Path

from data_mapper.ontology_catalog import build_ontology_catalog


def load_metric_gold_catalog(
    definition_path: Path,
    knowledge_path: Path,
):
    """Rebuild the ontology revision to which the historical Metric Gold is pinned."""

    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    knowledge.pop("Organization", None)
    return build_ontology_catalog(definition, knowledge)
