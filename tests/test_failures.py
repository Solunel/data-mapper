from __future__ import annotations

from pathlib import Path

import pytest

from data_mapper import InputParseError, UnsupportedFormatError, curate_file
from data_mapper.contracts import PipelineConfig
from data_mapper.parsers import ingest_raw, parse_raw


FIXTURES = Path(__file__).parent / "fixtures"


def test_unsupported_format_is_rejected_explicitly() -> None:
    with pytest.raises(UnsupportedFormatError, match="仅支持 .xlsx 和 .csv"):
        curate_file(FIXTURES / "synthetic_unsupported.txt")


def test_invalid_xlsx_is_not_reported_as_empty_success() -> None:
    with pytest.raises(InputParseError, match="XLSX 文件无效") as captured:
        curate_file(FIXTURES / "synthetic_invalid.xlsx")

    assert captured.value.raw_dataset is not None
    assert captured.value.raw_dataset.status == "failed"
    assert captured.value.raw_dataset.error


def test_raw_checksum_change_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_mutated.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    config = PipelineConfig()
    raw = ingest_raw(source, config)
    source.write_text("a,b\n3,4\n", encoding="utf-8")

    with pytest.raises(InputParseError, match="校验值不再匹配") as captured:
        parse_raw(raw, config)

    assert captured.value.raw_dataset is not None
    assert captured.value.raw_dataset.error == "checksum_mismatch"


def test_non_consecutive_header_rows_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_invalid_header_rows.csv"
    source.write_text(
        "一级表头,,\n"
        "项目,本期数,预算数\n"
        "二级表头,,\n"
        "营业收入,100,90\n",
        encoding="utf-8",
    )

    config = PipelineConfig(header_rows={"CSV": (2, 4)})
    with pytest.raises(InputParseError, match="必须使用连续行号"):
        curate_file(source, config)
