from __future__ import annotations

import importlib.util
from pathlib import Path

import data_mapper


ROOT = Path(__file__).parents[1]


def test_retired_public_api_and_modules_are_absent() -> None:
    retired_symbols = {
        "MappingRequest",
        "ObservationCandidate",
        "MappingPlan",
        "MappingReport",
        "MappingResult",
        "map_curated_dataset",
        "split_mapping_request",
    }
    assert retired_symbols.isdisjoint(data_mapper.__all__)
    assert all(not hasattr(data_mapper, name) for name in retired_symbols)

    retired_modules = (
        "data_mapper.mapping",
        "data_mapper.mapping_contracts",
        "data_mapper.phase25",
        "data_mapper.phase25_contracts",
        "data_mapper.phase25_deepseek",
        "data_mapper.phase25_retrieval",
        "data_mapper.phase25_semantic",
        "data_mapper.phase25_semantic_contracts",
    )
    assert all(importlib.util.find_spec(name) is None for name in retired_modules)


def test_production_has_no_evaluation_or_storage_implementation_dependency() -> None:
    package = ROOT / "src" / "data_mapper"
    production_files = [
        path
        for path in package.glob("*.py")
        if path.name != "__init__.py"
    ]
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        assert "from .evaluation" not in source
        assert "from data_mapper.evaluation" not in source

    core_files = (
        "deterministic_resolution.py",
        "candidate_retrieval.py",
        "semantic_resolution.py",
    )
    for filename in core_files:
        source = (package / filename).read_text(encoding="utf-8")
        for storage_name in ("Definition.json", "Knowledge.json", "Neo4j"):
            assert storage_name not in source


def test_public_example_uses_only_formal_workflow_and_new_cli_names() -> None:
    source = (ROOT / "a.py").read_text(encoding="utf-8")
    assert "map_curated_observations(" in source
    assert "--mode" in source
    for retired in (
        "map_curated_dataset",
        "MappingRequest",
        "_evaluation_plan_view",
        "--phase25-",
    ):
        assert retired not in source
