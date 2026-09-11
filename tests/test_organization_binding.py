from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from data_mapper import (
    MetricResolutionRequest,
    ObservationStructuringRequest,
    OntologyOrganization,
    ScalarBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
    resolve_organization,
)
from data_mapper.contracts import CuratedRow


ROOT = Path(__file__).parents[1]
PROFIT_FIXTURE = ROOT / "tests" / "fixtures" / "财务快报-利润表.xlsx"


def _catalog_with(*organizations: OntologyOrganization):
    catalog = load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        ROOT / "ontology" / "Knowledge.json",
    )
    return replace(
        catalog,
        organization_ids=tuple(item.organization_id for item in organizations),
        organizations=organizations,
    )


def _curated_with_organization(name: str):
    curated = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    return replace(
        curated,
        curated_id=f"organization-binding:{name}",
        rows=tuple(
            CuratedRow(
                source_row=row.source_row,
                values={**row.values, "公司名称": name},
            )
            for row in curated.rows
        ),
    )


def _map(name: str, catalog, organization_id: str | None = None):
    curated = _curated_with_organization(name)
    return map_curated_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            organization_id=organization_id,
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(),
        catalog,
    )


def test_catalog_exposes_organization_names_for_read_only_resolution(tmp_path) -> None:
    knowledge = json.loads(
        (ROOT / "ontology" / "Knowledge.json").read_text(encoding="utf-8")
    )
    knowledge["Organization"] = [
        {
            "id": "org.demo_company_a",
            "name_cn": "一级子公司A",
            "parent_organization_id": "org.demo_group",
            "status": "ACTIVE",
        }
    ]
    knowledge_path = tmp_path / "Knowledge.json"
    knowledge_path.write_text(
        json.dumps(knowledge, ensure_ascii=False),
        encoding="utf-8",
    )
    catalog = load_ontology_catalog(
        ROOT / "ontology" / "Definition.json",
        knowledge_path,
    )

    company = next(
        item
        for item in catalog.organizations
        if item.organization_id == "org.demo_company_a"
    )
    assert company.name_cn == "一级子公司A"


def test_workflow_binds_exact_unique_name_without_mutating_drafts() -> None:
    known = OntologyOrganization("org.demo_company_a", "一级子公司A")
    catalog = _catalog_with(known)
    result = _map("一级子公司A", catalog)

    assert result.resolved_observations
    assert all(
        item.organization_id == "org.demo_company_a"
        for item in result.resolved_observations
    )
    assert all(
        item.observation.organization_value == "一级子公司A"
        and item.observation.organization_id is None
        for item in result.resolved_observations
    )


def test_unknown_name_stays_unresolved_without_generated_id() -> None:
    catalog = _catalog_with(
        OntologyOrganization("org.demo_company_a", "一级子公司A")
    )
    result = _map("不存在的公司", catalog)

    assert resolve_organization("不存在的公司", catalog) is None
    assert all(item.organization_id is None for item in result.resolved_observations)


def test_duplicate_exact_name_stays_unresolved() -> None:
    catalog = _catalog_with(
        OntologyOrganization("org.company_a.1", "一级子公司A"),
        OntologyOrganization("org.company_a.2", "一级子公司A"),
    )

    assert resolve_organization("一级子公司A", catalog) is None
    result = _map("一级子公司A", catalog)
    assert all(item.organization_id is None for item in result.resolved_observations)


def test_known_draft_organization_id_is_validated_against_catalog() -> None:
    catalog = _catalog_with(
        OntologyOrganization("org.demo_company_a", "一级子公司A")
    )

    valid = _map("不存在的公司", catalog, "org.demo_company_a")
    assert all(
        item.organization_id == "org.demo_company_a"
        for item in valid.resolved_observations
    )

    invalid = _map("不存在的公司", catalog, "org.not_in_catalog")
    assert all(item.organization_id is None for item in invalid.resolved_observations)


def test_invalid_draft_organization_id_can_fall_back_to_unique_name() -> None:
    catalog = _catalog_with(
        OntologyOrganization("org.demo_company_a", "一级子公司A")
    )
    result = _map("一级子公司A", catalog, "org.not_in_catalog")

    assert all(
        item.organization_id == "org.demo_company_a"
        for item in result.resolved_observations
    )


def test_resolved_observation_exposes_only_effective_metric_id() -> None:
    catalog = _catalog_with(
        OntologyOrganization("org.demo_company_a", "一级子公司A")
    )
    result = _map("一级子公司A", catalog)

    assert all(
        item.metric_id == item.metric_resolution.current_metric_id
        for item in result.resolved_observations
    )
    assert any(
        item.metric_id is not None
        and item.organization_id == "org.demo_company_a"
        for item in result.resolved_observations
    )


def test_organization_binding_does_not_change_metric_resolution() -> None:
    empty_catalog = _catalog_with()
    known_catalog = replace(
        empty_catalog,
        organization_ids=("org.demo_company_a",),
        organizations=(
            OntologyOrganization("org.demo_company_a", "一级子公司A"),
        ),
    )

    without_binding = _map("一级子公司A", empty_catalog)
    with_binding = _map("一级子公司A", known_catalog)

    assert (
        with_binding.metric_resolution_result.to_dict()
        == without_binding.metric_resolution_result.to_dict()
    )
    assert (
        with_binding.effective_metric_resolutions
        == without_binding.effective_metric_resolutions
    )
