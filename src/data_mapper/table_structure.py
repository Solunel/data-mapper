"""只负责表头和表格边界的确定性结构识别。"""

from __future__ import annotations

from typing import Any, Sequence

from .errors import InputParseError


MergedRange = tuple[int, int, int, int]


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def row_is_empty(row: Sequence[Any]) -> bool:
    return all(is_empty(value) for value in row)


def find_header_rows(
    rows: Sequence[Sequence[Any]],
    explicit: int | Sequence[int] | None,
    merged_ranges: Sequence[MergedRange],
) -> tuple[int, ...]:
    """在前 20 行选择最可靠的单行或连续多行表头。"""

    if explicit is not None:
        return _validate_explicit_header_rows(rows, explicit)

    column_count = max((len(row) for row in rows), default=0)
    candidates: list[tuple[float, int, int]] = []
    for index, row in enumerate(rows[:20]):
        start = index + 1
        if row_is_empty(row) or _inside_vertical_merge_from_above(start, merged_ranges):
            continue
        end = _candidate_header_end(start, merged_ranges)
        if end > len(rows):
            continue
        expanded = expand_header_rows(
            rows,
            tuple(range(start, end + 1)),
            column_count,
            merged_ranges,
        )
        headers = flatten_headers(expanded, column_count)
        non_empty = [header for header in headers if header]
        if len(non_empty) < 2 or len(set(non_empty)) < 2:
            continue

        numeric_cells = sum(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for expanded_row in expanded
            for value in expanded_row
            if not is_empty(value)
        )
        sparse_penalty = 10 if len(non_empty) <= 2 else 0
        multirow_bonus = (end - start) * 5
        score = (
            len(non_empty) * 10
            + len(set(non_empty)) * 2
            + multirow_bonus
            - numeric_cells * 5
            - sparse_penalty
            - start / 100
        )
        candidates.append((score, start, end))

    if not candidates:
        raise InputParseError(
            "无法在前 20 行中可靠定位表头，请显式配置 header_rows"
        )

    best_score = max(candidate[0] for candidate in candidates)
    # 结构得分相近时取更早的行，避免把第一条全字符串数据误当成表头。
    near_best = [candidate for candidate in candidates if candidate[0] >= best_score - 5]
    _, start, end = min(near_best, key=lambda candidate: candidate[1])
    return tuple(range(start, end + 1))


def expand_header_rows(
    rows: Sequence[Sequence[Any]],
    header_rows: tuple[int, ...],
    column_count: int,
    merged_ranges: Sequence[MergedRange],
) -> list[list[Any]]:
    """只在表头范围内展开合并单元格，不改变数据区。"""

    start = header_rows[0]
    end = header_rows[-1]
    expanded: list[list[Any]] = []
    for row_number in header_rows:
        values = list(rows[row_number - 1][:column_count])
        values.extend([None] * (column_count - len(values)))
        expanded.append(values)

    for min_row, max_row, min_column, max_column in merged_ranges:
        if max_row < start or min_row > end or min_column > column_count:
            continue
        source_row = rows[min_row - 1] if min_row <= len(rows) else ()
        anchor = source_row[min_column - 1] if min_column <= len(source_row) else None
        if is_empty(anchor):
            continue
        for row_number in range(max(start, min_row), min(end, max_row) + 1):
            for column_number in range(
                min_column,
                min(max_column, column_count) + 1,
            ):
                expanded[row_number - start][column_number - 1] = anchor
    return expanded


def flatten_headers(
    expanded_rows: Sequence[Sequence[Any]],
    column_count: int,
) -> list[str]:
    """按层级组合多行表头，同一层级重复名称只保留一次。"""

    headers: list[str] = []
    for column in range(column_count):
        parts: list[str] = []
        for row in expanded_rows:
            value = row[column] if column < len(row) else None
            if is_empty(value):
                continue
            text = _header_text(value)
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        headers.append(" / ".join(parts))
    return headers


def table_column_bounds(
    headers: Sequence[str],
    data_rows: Sequence[Sequence[Any]],
) -> tuple[int, int]:
    """保留具名列，并容纳紧邻表格且持续有数据的无名列。"""

    named = [index for index, header in enumerate(headers) if header]
    if not named:
        return 0, len(headers)
    start = min(named)
    end = max(named) + 1
    sampled = [row for row in data_rows[:100] if not row_is_empty(row)]

    def occupancy(column: int) -> float:
        if not sampled:
            return 0.0
        populated = sum(
            column < len(row) and not is_empty(row[column])
            for row in sampled
        )
        return populated / len(sampled)

    while start > 0 and occupancy(start - 1) >= 0.5:
        start -= 1
    while end < len(headers) and occupancy(end) >= 0.5:
        end += 1
    return start, end


def _validate_explicit_header_rows(
    rows: Sequence[Sequence[Any]],
    explicit: int | Sequence[int],
) -> tuple[int, ...]:
    if isinstance(explicit, bool):
        raise InputParseError("header_rows 不能使用布尔值")
    if isinstance(explicit, int):
        values = (explicit,)
    elif isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        values = tuple(explicit)
    else:
        raise InputParseError("header_rows 必须是行号整数或连续行号序列")

    if not values or any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in values
    ):
        raise InputParseError("header_rows 必须包含至少一个整数行号")
    if tuple(sorted(set(values))) != values:
        raise InputParseError("header_rows 必须按升序提供且不能重复")
    if values != tuple(range(values[0], values[-1] + 1)):
        raise InputParseError("多行表头必须使用连续行号")
    for value in values:
        if value < 1 or value > len(rows):
            raise InputParseError(f"配置的表头行 {value} 超出输入范围")
        if row_is_empty(rows[value - 1]):
            raise InputParseError(f"配置的表头行 {value} 为空")
    return values


def _inside_vertical_merge_from_above(
    row_number: int,
    merged_ranges: Sequence[MergedRange],
) -> bool:
    return any(
        min_row < row_number <= max_row and min_row != max_row
        for min_row, max_row, _, _ in merged_ranges
    )


def _candidate_header_end(
    start: int,
    merged_ranges: Sequence[MergedRange],
) -> int:
    end = start
    changed = True
    while changed:
        changed = False
        for min_row, max_row, _, _ in merged_ranges:
            if start <= min_row <= end and max_row > end:
                end = max_row
                changed = True
    return end


def _header_text(value: Any) -> str:
    return (
        str(value)
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
