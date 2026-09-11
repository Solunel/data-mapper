from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

import a as demo_entry

from data_mapper import (
    MetricResolutionRequest,
    ObservationStructuringRequest,
    OntologyInstantiationError,
    ScalarBinding,
    actual_observation_id,
    curate_file,
    instantiate_observations,
    load_ontology_catalog,
    map_curated_observations,
)
from data_mapper.metric_resolution_contracts import DataMappingResult, ResolvedObservation
from data_mapper.observation_contracts import Period
from data_mapper.ontology_catalog import build_ontology_catalog


ROOT = Path(__file__).parents[1]
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
PROFIT_FIXTURE = ROOT / "tests" / "fixtures" / "财务快报-利润表.xlsx"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


@pytest.fixture(scope="module")
def one_mapping(catalog):
    curated = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    mapped = map_curated_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            organization_id="org.level1_subsidiary_a",
            unit=ScalarBinding(constant="万元"),
        ),
        MetricResolutionRequest(),
        catalog,
    )
    original = next(
        item
        for item in mapped.resolved_observations
        if item.metric_id is not None and item.organization_id is not None
    )
    effective = original.metric_resolution
    decision = next(
        item
        for item in mapped.metric_resolution_result.deterministic_decisions
        if item.decision_id == effective.source_metric_decision_id
    )
    return replace(
        mapped,
        structuring_result=replace(
            mapped.structuring_result,
            row_subjects=(decision.subject,),
            observation_drafts=(original.observation,),
        ),
        metric_resolution_result=replace(
            mapped.metric_resolution_result,
            deterministic_decisions=(decision,),
            candidate_sets=(),
            semantic_resolutions=(),
            ontology_change_proposals=(),
        ),
        effective_metric_resolutions=(effective,),
        resolved_observations=(original,),
    )


def _with_drafts(
    mapping: DataMappingResult,
    drafts,
    *,
    effective=None,
) -> DataMappingResult:
    selected_effective = effective or mapping.effective_metric_resolutions[0]
    resolved = tuple(
        ResolvedObservation(
            observation=draft,
            metric_resolution=selected_effective,
            organization_id=mapping.resolved_observations[0].organization_id,
        )
        for draft in drafts
    )
    return replace(
        mapping,
        structuring_result=replace(
            mapping.structuring_result,
            observation_drafts=tuple(drafts),
        ),
        effective_metric_resolutions=(selected_effective,),
        resolved_observations=resolved,
    )


def test_catalog_projects_unit_semantics_without_changing_schema_fingerprint() -> None:
    definition = json.loads(DEFINITION.read_text(encoding="utf-8"))
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    original = build_ontology_catalog(definition, knowledge)
    changed = json.loads(json.dumps(definition, ensure_ascii=False))
    changed["enums"]["Unit"]["values"]["PERCENT"]["storage_semantics"] = "changed"
    projected = build_ontology_catalog(changed, knowledge)

    assert original.unit_storage_semantics == {"PERCENT": "0_to_1"}
    assert projected.unit_storage_semantics == {"PERCENT": "changed"}
    assert projected.observation_schema.fingerprint == original.observation_schema.fingerprint
    assert projected.ontology_revision != original.ontology_revision


def test_valid_observation_is_projected_with_exact_definition_shape(
    one_mapping, catalog
) -> None:
    result = instantiate_observations((one_mapping,), catalog, status="DRAFT")

    assert len(result.actual_observations) == 1
    actual = result.actual_observations[0]
    assert set(actual.to_dict()) == {
        "id",
        "organization_id",
        "metric_id",
        "business_scope",
        "source",
        "period",
        "actual_value",
        "unit",
        "status",
    }
    assert actual.source == one_mapping.resolved_observations[0].observation.sheet_name
    assert actual.status == "DRAFT"
    assert result.blocked_observations == ()
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False))[
        "actual_observations"
    ][0]["id"] == actual.id

    overview = demo_entry.build_instantiation_overview_report(result)
    assert overview["ActualObservation 数"] == 1
    assert overview["Blocked Observation 数"] == 0


@pytest.mark.parametrize(
    ("period_type", "period_key"),
    (("MONTH", "2025-01"), ("QUARTER", "2025-Q1"), ("YEAR", "2025")),
)
@pytest.mark.parametrize(
    "period_basis",
    ("PERIOD_VALUE", "YEAR_TO_DATE", "PERIOD_BEGIN", "PERIOD_END"),
)
def test_all_period_types_and_bases_pass(
    one_mapping, catalog, period_type, period_key, period_basis
) -> None:
    draft = replace(
        one_mapping.resolved_observations[0].observation,
        period_type=period_type,
        period_key=period_key,
        period_basis=period_basis,
    )
    result = instantiate_observations(
        (_with_drafts(one_mapping, (draft,)),),
        catalog,
        status="DRAFT",
    )
    assert result.actual_observations[0].period == Period(
        period_type, period_key, period_basis
    )


def test_gate_collects_all_known_record_issues(one_mapping, catalog) -> None:
    draft = replace(
        one_mapping.resolved_observations[0].observation,
        business_scope="  ",
        period_type="BAD",
        period_key="2025-13",
        period_basis="BAD",
        actual_value=float("inf"),
        unit_normalized=None,
        sheet_name=" ",
    )
    effective = replace(
        one_mapping.effective_metric_resolutions[0],
        effective_status="UNRESOLVED",
        current_metric_id=None,
    )
    mapping = _with_drafts(one_mapping, (draft,), effective=effective)
    mapping = replace(
        mapping,
        resolved_observations=(
            replace(mapping.resolved_observations[0], organization_id=None),
        ),
    )

    result = instantiate_observations((mapping,), catalog, status="DRAFT")
    codes = {item.code for item in result.blocked_observations[0].reasons}
    assert codes == {
        "METRIC_ID_MISSING",
        "METRIC_STATUS_NOT_MAPPED",
        "ORGANIZATION_ID_MISSING",
        "BUSINESS_SCOPE_INVALID",
        "PERIOD_TYPE_INVALID",
        "PERIOD_KEY_INVALID",
        "PERIOD_BASIS_INVALID",
        "ACTUAL_VALUE_INVALID",
        "UNIT_MISSING",
        "SOURCE_INVALID",
    }
    assert result.actual_observations == ()


def test_percent_storage_semantics_is_enforced(one_mapping, catalog) -> None:
    draft = replace(
        one_mapping.resolved_observations[0].observation,
        actual_value=1.01,
        unit_raw="%",
        unit_normalized="PERCENT",
    )
    result = instantiate_observations(
        (_with_drafts(one_mapping, (draft,)),), catalog, status="DRAFT"
    )
    assert [
        issue.code for issue in result.blocked_observations[0].reasons
    ] == ["PERCENT_VALUE_OUT_OF_RANGE"]


def test_id_excludes_source_value_unit_status_and_technical_fields(
    one_mapping, catalog
) -> None:
    draft = one_mapping.resolved_observations[0].observation
    first = instantiate_observations((one_mapping,), catalog, status="DRAFT")
    changed = replace(
        draft,
        actual_value=draft.actual_value + 1,
        unit_normalized="PERSON",
        sheet_name="另一张表",
        source_row=draft.source_row + 50,
        observation_draft_id="observation-draft:changed",
    )
    second = instantiate_observations(
        (_with_drafts(one_mapping, (changed,)),), catalog, status="ACTIVE"
    )

    assert first.actual_observations[0].id == second.actual_observations[0].id
    assert first.actual_observations[0].id == actual_observation_id(
        organization_id=one_mapping.resolved_observations[0].organization_id,
        metric_id=one_mapping.resolved_observations[0].metric_id,
        business_scope=draft.business_scope,
        period=Period(draft.period_type, draft.period_key, draft.period_basis),
    )


def test_same_identity_payload_deduplicates_across_sources_deterministically(
    one_mapping, catalog
) -> None:
    base = one_mapping.resolved_observations[0].observation
    source_b = replace(base, observation_draft_id="draft:b", sheet_name="B表")
    source_a = replace(base, observation_draft_id="draft:a", sheet_name="A表")
    result = instantiate_observations(
        (
            _with_drafts(one_mapping, (source_b,)),
            _with_drafts(one_mapping, (source_a,)),
        ),
        catalog,
        status="DRAFT",
    )

    assert len(result.actual_observations) == 1
    assert result.actual_observations[0].source == "A表"
    assert result.deduplicated_observation_count == 1
    assert result.blocked_observations == ()


def test_conflicting_business_identity_blocks_entire_group(one_mapping, catalog) -> None:
    base = one_mapping.resolved_observations[0].observation
    first = replace(base, observation_draft_id="draft:first")
    second = replace(
        base,
        observation_draft_id="draft:second",
        actual_value=base.actual_value + 1,
        sheet_name="另一来源表",
    )
    result = instantiate_observations(
        (
            _with_drafts(one_mapping, (first,)),
            _with_drafts(one_mapping, (second,)),
        ),
        catalog,
        status="DRAFT",
    )

    assert result.actual_observations == ()
    assert len(result.blocked_observations) == 2
    assert all(
        item.reasons[0].code == "CONFLICTING_BUSINESS_IDENTITY"
        for item in result.blocked_observations
    )


def test_unresolved_metric_is_aggregated_by_subject_even_without_observation(
    one_mapping, catalog
) -> None:
    effective = replace(
        one_mapping.effective_metric_resolutions[0],
        effective_status="UNRESOLVED",
        current_metric_id=None,
    )
    without_draft = _with_drafts(one_mapping, (), effective=effective)
    result = instantiate_observations((without_draft,), catalog, status="DRAFT")

    assert len(result.unresolved_metrics) == 1
    item = result.unresolved_metrics[0]
    assert item.metric_subject_id == effective.metric_subject_id
    assert item.observation_count == 0
    assert item.organization_value is None
    assert item.organization_id is None
    assert result.blocked_observations == ()


@pytest.mark.parametrize("status", ("", "PUBLISHED", None))
def test_invalid_status_fails_fast(one_mapping, catalog, status) -> None:
    with pytest.raises(OntologyInstantiationError, match="status"):
        instantiate_observations((one_mapping,), catalog, status=status)


def test_empty_batch_fails_fast(catalog) -> None:
    with pytest.raises(OntologyInstantiationError, match="至少"):
        instantiate_observations((), catalog, status="DRAFT")


def test_revision_schema_and_percent_metadata_mismatch_fail_fast(
    one_mapping, catalog
) -> None:
    with pytest.raises(OntologyInstantiationError, match="revision"):
        instantiate_observations(
            (one_mapping,), replace(catalog, ontology_revision="other"), status="DRAFT"
        )
    changed_schema = replace(catalog.observation_schema, fingerprint="other")
    with pytest.raises(OntologyInstantiationError, match="fingerprint"):
        instantiate_observations(
            (one_mapping,),
            replace(catalog, observation_schema=changed_schema),
            status="DRAFT",
        )
    with pytest.raises(OntologyInstantiationError, match="PERCENT"):
        instantiate_observations(
            (one_mapping,),
            replace(catalog, unit_storage_semantics={}),
            status="DRAFT",
        )
