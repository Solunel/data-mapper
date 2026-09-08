from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from data_mapper import PipelineConfig, curate_file
from data_mapper.pipeline import PIPELINE_ORDER


FIXTURES = Path(__file__).parent / "fixtures"


def _matches_declared_type(value, data_type: str) -> bool:
    if value is None:
        return True
    expected = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, float),
        "boolean": lambda item: isinstance(item, bool),
        "date": lambda item: isinstance(item, date) and not isinstance(item, datetime),
        "datetime": lambda item: isinstance(item, datetime),
        "null": lambda item: item is None,
    }
    return expected[data_type](value)


def _assert_values_match_schema(curated) -> None:
    declared = {column.normalized_name: column.data_type for column in curated.data_schema.columns}
    assert all(
        _matches_declared_type(value, declared[name])
        for row in curated.rows
        for name, value in row.values.items()
    )


def test_csv_full_phase1_chain_is_traceable_and_replayable() -> None:
    source = FIXTURES / "synthetic_sales_bom.csv"
    before = source.read_bytes()
    config = PipelineConfig(
        type_overrides={
            "CSV": {
                "amount": "number",
                "active": "boolean",
                "order_date": "date",
            }
        }
    )

    first = curate_file(source, config)
    second = curate_file(source, config)

    assert source.read_bytes() == before
    assert first.raw_dataset.status == "parsed"
    assert first.raw_dataset.sha256 == hashlib.sha256(before).hexdigest()
    assert first.raw_dataset.dataset_id == second.raw_dataset.dataset_id
    assert first.raw_dataset.version_id == second.raw_dataset.version_id

    curated = first.curated_datasets[0]
    assert curated.curated_id == second.curated_datasets[0].curated_id
    assert curated.sheet_name == "CSV"
    assert tuple(step.name for step in curated.pipeline_steps) == PIPELINE_ORDER
    assert [row.source_row for row in curated.rows] == [2, 3, 4, 5]
    assert curated.rows[0].values["note"] == "first"
    assert curated.rows[0].values["amount"] == 1200.5
    assert curated.rows[0].values["active"] is True
    assert curated.rows[0].values["order_date"] == date(2026, 1, 15)
    assert curated.rows[-1].values["amount"] is None
    assert curated.rows[-1].values["order_date"] is None

    quality = curated.quality_report
    assert quality.curated_row_count == 4
    assert quality.row_count_difference >= 1
    assert quality.row_count_reasons["entirely_blank_rows_removed"] >= 1
    assert quality.duplicate_row_count == 1
    assert quality.conversion_error_count == 2
    assert {issue.source_column for issue in quality.conversion_error_examples} == {
        "amount",
        "order_date",
    }
    assert all(issue.source_row == 5 for issue in quality.conversion_error_examples)
    assert not quality.passed
    _assert_values_match_schema(curated)
    json.dumps(first.to_dict(), ensure_ascii=False)


def test_xlsx_all_non_empty_sheets_and_non_first_header() -> None:
    source = FIXTURES / "synthetic_multisheet.xlsx"
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    result = curate_file(source)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    assert result.raw_dataset.sha256 == before_hash
    assert [item.sheet_name for item in result.curated_datasets] == [
        "Synthetic Monthly",
        "Synthetic Budget",
    ]

    monthly, budget = result.curated_datasets
    assert monthly.header_row == 3
    assert [row.source_row for row in monthly.rows] == [4, 5, 6]
    assert monthly.data_schema.columns[2].data_type == "number"
    assert monthly.data_schema.columns[3].data_type == "date"
    assert isinstance(monthly.rows[0].values["report_date"], date)
    assert monthly.rows[0].values["approved"] is True
    assert monthly.header_mapping[0].original_name == "Record ID"
    assert monthly.header_mapping[0].normalized_name == "record_id"

    assert budget.header_row == 1
    assert [row.source_row for row in budget.rows] == [2, 3, 4]
    assert budget.data_schema.columns[1].data_type == "number"
    assert budget.quality_report.curated_row_count == 3
    assert all(tuple(step.name for step in item.pipeline_steps) == PIPELINE_ORDER for item in result.curated_datasets)
    for item in result.curated_datasets:
        _assert_values_match_schema(item)
