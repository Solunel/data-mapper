from __future__ import annotations

from datetime import date
from pathlib import Path

from data_mapper import PipelineConfig, curate_file


def test_schema_inference_uses_multiple_rows_and_reports_mixed_values(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_inference.csv"
    source.write_text("numeric,mixed\n1,1\n2.5,oops\n3,3\n", encoding="utf-8")

    curated = curate_file(source).curated_datasets[0]
    columns = {column.normalized_name: column for column in curated.data_schema.columns}

    assert columns["numeric"].data_type == "number"
    assert columns["numeric"].inference_evidence == {"integer": 2, "number": 1}
    assert columns["mixed"].data_type == "string"
    assert any(issue.code == "mixed_type_inference" for issue in curated.issues)


def test_headers_are_unique_stable_and_traceable(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_headers.csv"
    source.write_text("Name, name ,\nA,B,C\n", encoding="utf-8")

    first = curate_file(source).curated_datasets[0]
    second = curate_file(source).curated_datasets[0]

    assert [item.normalized_name for item in first.header_mapping] == [
        "name",
        "name_2",
        "column_3",
    ]
    assert [item.original_name for item in first.header_mapping] == ["Name", "name", ""]
    assert first.header_mapping == second.header_mapping
    assert {issue.code for issue in first.issues} >= {
        "empty_header",
        "duplicate_normalized_header",
    }


def test_eight_digit_numbers_default_to_integer_and_compact_dates_require_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic_eight_digit_amount.csv"
    source.write_text(
        "amount,full_date,compact_date\n"
        "67930301,2023-01-31,20230131\n"
        "13800830,2023-02-28,20230228\n",
        encoding="utf-8",
    )

    default = curate_file(source).curated_datasets[0]
    default_types = {
        column.normalized_name: column.data_type for column in default.data_schema.columns
    }
    assert default_types == {
        "amount": "integer",
        "full_date": "date",
        "compact_date": "integer",
    }
    assert default.rows[0].values["amount"] == 67930301

    overridden = curate_file(
        source,
        PipelineConfig(type_overrides={"CSV": {"compact_date": "date"}}),
    ).curated_datasets[0]
    assert overridden.rows[0].values["compact_date"] == date(2023, 1, 31)
    assert overridden.data_schema.columns[2].inference_method == "explicit-override"
