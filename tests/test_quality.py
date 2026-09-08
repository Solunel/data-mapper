from __future__ import annotations

from pathlib import Path

from data_mapper import curate_file


FIXTURES = Path(__file__).parent / "fixtures"


def test_header_only_csv_produces_failed_empty_quality_report() -> None:
    curated = curate_file(FIXTURES / "synthetic_header_only.csv").curated_datasets[0]

    assert curated.rows == ()
    assert curated.quality_report.curated_row_count == 0
    assert not curated.quality_report.passed
    assert any(issue.code == "empty_table" for issue in curated.quality_report.issues)


def test_null_metrics_and_duplicates_are_reported_without_deduplication() -> None:
    curated = curate_file(FIXTURES / "synthetic_sales_bom.csv").curated_datasets[0]
    qualities = {column.name: column for column in curated.quality_report.columns}

    assert len(curated.rows) == 4
    assert curated.quality_report.duplicate_row_count == 1
    assert qualities["amount"].null_count == 2
    assert qualities["amount"].null_rate == 0.5
