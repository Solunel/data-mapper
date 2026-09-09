from __future__ import annotations

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
    source.write_text("Name,Name,\nA,B,C\n", encoding="utf-8")

    config = PipelineConfig(header_rows={"CSV": 1})
    first = curate_file(source, config).curated_datasets[0]
    second = curate_file(source, config).curated_datasets[0]

    assert [item.normalized_name for item in first.header_mapping] == [
        "Name",
        "Name_2",
        "column_3",
    ]
    assert [item.original_name for item in first.header_mapping] == ["Name", "Name", ""]
    assert [item.source_position for item in first.header_mapping] == [1, 2, 3]
    assert first.header_mapping == second.header_mapping
    assert {issue.code for issue in first.issues} >= {
        "empty_header",
        "duplicate_normalized_header",
    }
