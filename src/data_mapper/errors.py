"""Phase 1 的明确失败类型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import RawDataset


class Phase1Error(Exception):
    """Phase 1 支持范围内错误的基类。"""

    def __init__(self, message: str, *, raw_dataset: RawDataset | None = None) -> None:
        super().__init__(message)
        self.raw_dataset = raw_dataset


class UnsupportedFormatError(Phase1Error):
    """输入扩展名超出 Phase 1 范围时抛出。"""


class InputParseError(Phase1Error):
    """支持范围内的文件无法可靠解析时抛出。"""
