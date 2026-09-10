from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from data_mapper import (
    MetricMatchStatus,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    RowRole,
    ScalarBinding,
    StructureStatus,
    ValueFieldBinding,
    curate_file,
    load_ontology_catalog,
    resolve_metrics_deterministically,
    structure_observations,
)
from data_mapper.contracts import CuratedRow


ROOT = Path(__file__).parents[1]
DEFINITION = ROOT / "ontology" / "Definition.json"
KNOWLEDGE = ROOT / "ontology" / "Knowledge.json"
PROFIT_FIXTURE = ROOT / "tests" / "fixtures" / "财务快报-利润表.xlsx"


@pytest.fixture(scope="module")
def catalog():
    return load_ontology_catalog(DEFINITION, KNOWLEDGE)


def _profit_curated(row_specs, *, curated_id: str):
    base = curate_file(PROFIT_FIXTURE).curated_datasets[0]
    rows = []
    for original, spec in zip(base.rows, row_specs, strict=False):
        values = dict(original.values)
        values.update(spec)
        rows.append(CuratedRow(source_row=original.source_row, values=values))
    return replace(base, curated_id=curated_id, rows=tuple(rows))


def _renamed_cumulative(curated):
    name_map = {"本年累计数": "本年金额"}
    headers = tuple(
        replace(
            item,
            original_name=name_map.get(item.normalized_name, item.original_name),
            normalized_name=name_map.get(item.normalized_name, item.normalized_name),
            base_normalized_name=name_map.get(
                item.normalized_name, item.base_normalized_name
            ),
        )
        for item in curated.header_mapping
    )
    columns = tuple(
        replace(
            item,
            original_name=name_map.get(item.normalized_name, item.original_name),
            normalized_name=name_map.get(item.normalized_name, item.normalized_name),
        )
        for item in curated.data_schema.columns
    )
    rows = tuple(
        CuratedRow(
            source_row=row.source_row,
            values={name_map.get(key, key): value for key, value in row.values.items()},
        )
        for row in curated.rows
    )
    return replace(
        curated,
        header_mapping=headers,
        data_schema=replace(curated.data_schema, columns=columns),
        rows=rows,
    )


def test_catalog_exposes_read_only_actual_observation_projection(catalog) -> None:
    schema = catalog.observation_schema
    shapes = {
        field.name: (field.value_type, field.target, field.required)
        for field in schema.actual_observation_fields
    }

    assert shapes["organization_id"] == ("reference", "Organization.id", True)
    assert shapes["metric_id"] == ("reference", "Metric.id", True)
    assert shapes["period"] == ("struct", "Period", True)
    assert shapes["status"] == ("enum", "Status", True)
    assert {field.name for field in schema.period_fields} == {
        "period_type",
        "period_key",
        "period_basis",
    }
    assert schema.status_values == ("DRAFT", "ACTIVE", "INACTIVE")
    assert schema.fingerprint.startswith("sha256:")


def test_structuring_is_independent_from_metric_resolution_and_required_gate(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "主营业务收入", "本月数": 10, "本年累计数": 70}],
        curated_id="r1-independent",
    )
    request = ObservationStructuringRequest(
        curated_id=curated.curated_id,
        organization_id="known-reference-passthrough",
        unit=ScalarBinding(constant="万元"),
    )
    first = structure_observations(curated, request, catalog.observation_schema)
    relaxed_schema = replace(
        catalog.observation_schema,
        actual_observation_fields=tuple(
            replace(field, required=False)
            for field in catalog.observation_schema.actual_observation_fields
        ),
        period_fields=tuple(
            replace(field, required=False)
            for field in catalog.observation_schema.period_fields
        ),
        fingerprint="test:r1-required-is-not-a-draft-gate",
    )
    second = structure_observations(curated, request, relaxed_schema)

    assert first.report.structure_status is StructureStatus.READY
    assert first.observation_drafts
    assert [item.observation_draft_id for item in first.observation_drafts] == [
        item.observation_draft_id for item in second.observation_drafts
    ]
    assert all(
        item.organization_id == "known-reference-passthrough"
        for item in first.observation_drafts
    )
    forbidden = {
        "ontology_revision",
        "current_metric_id",
        "metric_decision_id",
        "status",
        "definition_constraints_satisfied",
        "instantiation_missing_fields",
    }
    assert forbidden.isdisjoint(first.observation_drafts[0].to_dict())

    unmatched = resolve_metrics_deterministically(
        first.row_subjects,
        MetricResolutionRequest(),
        catalog,
    )
    overridden = resolve_metrics_deterministically(
        first.row_subjects,
        MetricResolutionRequest(
            metric_overrides={first.row_subjects[0].source_row: "qc.core_business_cost"}
        ),
        catalog,
    )
    assert first.to_dict() == structure_observations(
        curated, request, catalog.observation_schema
    ).to_dict()
    assert unmatched[0].status is MetricMatchStatus.MATCHED
    assert overridden[0].selected_metric.current_metric_id == "qc.core_business_cost"


def test_needs_binding_keeps_partial_observation_drafts(catalog) -> None:
    curated = _renamed_cumulative(
        _profit_curated(
            [{"项      目": "净利润", "本月数": 10, "本年累计数": 80}],
            curated_id="r1-partial",
        )
    )
    result = structure_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            value_bindings=(
                ValueFieldBinding(
                    value_field="本月数",
                    business_scope=ScalarBinding(constant="公司整体"),
                    period_type="MONTH",
                    period_key=ScalarBinding(field="时间"),
                    period_basis="PERIOD_VALUE",
                    unit=ScalarBinding(constant="万元"),
                ),
                ValueFieldBinding(
                    value_field="本年金额",
                    business_scope=ScalarBinding(constant="公司整体"),
                    period_type="MONTH",
                    period_key=ScalarBinding(field="时间"),
                    unit=ScalarBinding(constant="万元"),
                ),
            ),
        ),
        catalog.observation_schema,
    )

    assert result.report.structure_status is StructureStatus.NEEDS_BINDING
    assert len(result.observation_drafts) == 1
    assert result.observation_drafts[0].value_field == "本月数"
    assert result.report.unprojected_values[0]["value_field"] == "本年金额"


def test_blocked_structuring_never_projects_drafts(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "净利润", "本月数": 10}],
        curated_id="r1-blocked",
    )
    curated = replace(
        curated,
        quality_report=replace(curated.quality_report, passed=False),
    )
    result = structure_observations(
        curated,
        ObservationStructuringRequest(curated_id=curated.curated_id),
        catalog.observation_schema,
    )

    assert result.report.structure_status is StructureStatus.BLOCKED
    assert result.row_subjects == ()
    assert result.observation_drafts == ()


def test_metric_row_hint_is_explicit_structuring_input(catalog) -> None:
    curated = _profit_curated(
        [{"项      目": "其他", "本月数": 1}],
        curated_id="r1-row-hint",
    )
    without_hint = structure_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
        ),
        catalog.observation_schema,
    )
    row = without_hint.row_subjects[0].source_row
    with_hint = structure_observations(
        curated,
        ObservationStructuringRequest(
            curated_id=curated.curated_id,
            unit=ScalarBinding(constant="万元"),
            metric_row_hints=(row,),
        ),
        catalog.observation_schema,
    )

    assert without_hint.row_subjects[0].row_role is RowRole.UNKNOWN
    assert with_hint.row_subjects[0].row_role is RowRole.METRIC
    assert any(
        evidence.code == "confirmed_metric_subject"
        for evidence in with_hint.row_subjects[0].evidence
    )


def test_core_modules_do_not_import_storage_implementations() -> None:
    structuring_source = (
        ROOT / "src" / "data_mapper" / "observation_structuring.py"
    ).read_text(encoding="utf-8")
    resolution_source = (
        ROOT / "src" / "data_mapper" / "deterministic_resolution.py"
    ).read_text(encoding="utf-8")

    assert "from .metric_resolution_contracts import" not in structuring_source
    assert "from .ontology_catalog import" not in structuring_source
    for implementation_name in ("Definition.json", "Knowledge.json", "Neo4j"):
        assert implementation_name not in structuring_source
        assert implementation_name not in resolution_source
