"""Table boundary detection within Excel sheets.

Each sheet may contain one or more logical tables. This module uses
rule-based heuristics to identify:
  - Table boundaries (start/end rows)
  - Header rows
  - Column roles: name, sequence, value, unit, time_series, category, notes
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Optional

from openpyxl.utils import get_column_letter, column_index_from_string


# ── Column role keywords ─────────────────────────────────────────────────────

_UNIT_KEYWORDS = {"单位", "万元", "元", "亿元", "%", "MW", "kW", "h", "年", "月", "个"}
_NAME_KEYWORDS = {"项目", "名称", "参数", "指标", "科目"}
_SEQ_KEYWORDS = {"序号", "编号", "序"}
_CATEGORY_KEYWORDS = {"类别", "分类", "大类"}
_TOTAL_KEYWORDS = {"合计", "小计", "汇总", "总计", "总额"}
_NOTES_KEYWORDS = {"备注", "说明", "注", "取值说明", "参数释义"}


def _is_excel_date_serial(v) -> bool:
    """Heuristic: integer in range of plausible Excel date serials (2000-2100)."""
    if not isinstance(v, (int, float)):
        return False
    return 36526 <= v <= 73050  # 2000-01-01 to 2099-12-31


def _excel_serial_to_label(serial: float) -> str:
    """Convert Excel date serial to YYYY-MM label."""
    try:
        dt = datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(serial))
        return dt.strftime("%Y-%m")
    except Exception:
        return str(int(serial))


def _is_year_value(v) -> bool:
    """Heuristic: integer that looks like a year (2000-2100)."""
    return isinstance(v, (int, float)) and 2000 <= v <= 2100 and v == int(v)


def _looks_like_sequence(v) -> bool:
    """Heuristic: value looks like a sequence number (1, 1.1, 2, 'A', etc.)."""
    if isinstance(v, (int, float)):
        return 0 < v < 1000
    if isinstance(v, str):
        return bool(re.match(r"^\d+(\.\d+)*$", v.strip()))
    return False


# ── Table structure ──────────────────────────────────────────────────────────

class ColRole:
    CATEGORY = "category"
    SEQUENCE = "sequence"
    NAME = "name"
    TOTAL = "total"
    UNIT = "unit"
    TIME_SERIES = "time_series"
    NOTES = "notes"
    FORMULA_DESC = "formula_desc"
    UNKNOWN = "unknown"


class TableInfo:
    """Detected table within a sheet."""

    def __init__(self, sheet: str, header_row: int, data_start: int, data_end: int):
        self.sheet = sheet
        self.header_row = header_row
        self.data_start = data_start
        self.data_end = data_end
        # col_letter -> ColRole string
        self.col_roles: dict[str, str] = {}
        # col_letter -> header label
        self.col_labels: dict[str, str] = {}
        # col_letter -> period label (for time_series cols)
        self.time_period_labels: dict[str, str] = {}

    def name_col(self) -> Optional[str]:
        for col, role in self.col_roles.items():
            if role == ColRole.NAME:
                return col
        return None

    def total_col(self) -> Optional[str]:
        for col, role in self.col_roles.items():
            if role == ColRole.TOTAL:
                return col
        return None

    def unit_col(self) -> Optional[str]:
        for col, role in self.col_roles.items():
            if role == ColRole.UNIT:
                return col
        return None

    def category_col(self) -> Optional[str]:
        for col, role in self.col_roles.items():
            if role == ColRole.CATEGORY:
                return col
        return None

    def sequence_col(self) -> Optional[str]:
        for col, role in self.col_roles.items():
            if role == ColRole.SEQUENCE:
                return col
        return None

    def time_series_cols(self) -> list[str]:
        return [col for col, role in self.col_roles.items() if role == ColRole.TIME_SERIES]


# ── Main detection function ──────────────────────────────────────────────────

def detect_tables(
    sheet_name: str,
    rows: dict[int, dict[str, object]],  # row_num -> {col_letter: value}
) -> list[TableInfo]:
    """Detect logical tables within a sheet.

    Args:
        sheet_name: Name of the sheet.
        rows: Dict mapping row number to dict of {col_letter: cell_value}.

    Returns:
        List of TableInfo objects, one per detected table.
    """
    if not rows:
        return []

    sorted_row_nums = sorted(rows.keys())

    # ── Step 1: find header rows ─────────────────────────────────────────────
    # A header row is a row where most non-empty values are strings and
    # at least one matches a known header keyword.
    header_rows = _find_header_rows(rows, sorted_row_nums)

    if not header_rows:
        # Fallback: treat first non-empty row as header
        header_rows = [sorted_row_nums[0]]

    # ── Step 2: split into table segments by header rows ────────────────────
    tables: list[TableInfo] = []
    for i, hrow in enumerate(header_rows):
        data_start = hrow + 1
        # data ends just before the next header row (or at last row)
        if i + 1 < len(header_rows):
            data_end = header_rows[i + 1] - 1
        else:
            data_end = sorted_row_nums[-1]

        if data_start > data_end:
            continue

        tbl = TableInfo(sheet_name, hrow, data_start, data_end)
        _classify_columns(tbl, rows)
        tables.append(tbl)

    return tables


def _keyword_categories(str_values: list) -> set[str]:
    """Return the set of keyword categories matched by a list of string values."""
    cats: set[str] = set()
    for v in str_values:
        if any(kw in v for kw in _CATEGORY_KEYWORDS):
            cats.add("cat")
        if any(kw in v for kw in _SEQ_KEYWORDS):
            cats.add("seq")
        if any(kw in v for kw in _NAME_KEYWORDS):
            cats.add("name")
        if any(kw in v for kw in _UNIT_KEYWORDS):
            cats.add("unit")
        if any(kw in v for kw in _NOTES_KEYWORDS):
            cats.add("notes")
    return cats


def _find_header_rows(
    rows: dict[int, dict[str, object]],
    sorted_row_nums: list[int],
) -> list[int]:
    """Identify rows that serve as column headers.

    Two cases qualify as a header row:
    1. Text header: < 20% numeric values AND keywords from ≥ 2 distinct categories.
    2. Time-series header: > 50% date serials AND at least 1 keyword category
       (the text columns label the row as a header, date serials label time periods).
    """
    header_rows = []
    for rnum in sorted_row_nums:
        row = rows[rnum]
        if not row:
            continue
        values = list(row.values())
        str_values = [v for v in values if isinstance(v, str) and v.strip()]
        if not str_values:
            continue

        num_values = [
            v for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and not _is_excel_date_serial(v) and not _is_year_value(v)
        ]
        date_serial_count = sum(1 for v in values if _is_excel_date_serial(v))
        year_count = sum(1 for v in values if _is_year_value(v))
        num_ratio = len(num_values) / len(values)
        date_ratio = (date_serial_count + year_count) / len(values)

        kw_cats = _keyword_categories(str_values)

        # Require ≥ 3 distinct keyword categories to avoid false positives
        # (data cells often contain 1-2 keyword substrings by coincidence)
        is_text_header = num_ratio < 0.2 and len(kw_cats) >= 3
        is_ts_header = date_ratio > 0.5 and len(kw_cats) >= 1

        if is_text_header or is_ts_header:
            header_rows.append(rnum)

    return header_rows


def _classify_columns(tbl: TableInfo, rows: dict[int, dict[str, object]]) -> None:
    """Classify each column's role based on header label and data values."""
    header_row = rows.get(tbl.header_row, {})

    # Collect all columns that appear in data rows
    data_cols: set[str] = set()
    for rnum in range(tbl.data_start, tbl.data_end + 1):
        if rnum in rows:
            data_cols.update(rows[rnum].keys())
    data_cols.update(header_row.keys())

    for col in sorted(data_cols, key=lambda c: column_index_from_string(c)):
        label = header_row.get(col)
        label_str = str(label).strip() if label is not None else ""

        # Check header label first
        role = _role_from_label(label_str)

        # If label is a date serial or year, it's a time_series column
        if role == ColRole.UNKNOWN and label is not None:
            if _is_excel_date_serial(label):
                role = ColRole.TIME_SERIES
                tbl.time_period_labels[col] = _excel_serial_to_label(label)
            elif _is_year_value(label):
                role = ColRole.TIME_SERIES
                tbl.time_period_labels[col] = str(int(label))

        # Fallback: sample data values to infer role
        if role == ColRole.UNKNOWN:
            role = _role_from_data(col, tbl, rows)

        tbl.col_roles[col] = role
        tbl.col_labels[col] = label_str


def _role_from_label(label: str) -> str:
    if not label:
        return ColRole.UNKNOWN
    for kw in _CATEGORY_KEYWORDS:
        if kw in label:
            return ColRole.CATEGORY
    for kw in _SEQ_KEYWORDS:
        if kw in label:
            return ColRole.SEQUENCE
    for kw in _NAME_KEYWORDS:
        if kw in label:
            return ColRole.NAME
    for kw in _TOTAL_KEYWORDS:
        if kw in label:
            return ColRole.TOTAL
    for kw in _UNIT_KEYWORDS:
        if kw in label:
            return ColRole.UNIT
    for kw in _NOTES_KEYWORDS:
        if kw in label:
            return ColRole.NOTES
    return ColRole.UNKNOWN


def _role_from_data(col: str, tbl: TableInfo, rows: dict[int, dict[str, object]]) -> str:
    """Infer column role by sampling data values."""
    sample = []
    for rnum in range(tbl.data_start, min(tbl.data_start + 10, tbl.data_end + 1)):
        if rnum in rows and col in rows[rnum]:
            sample.append(rows[rnum][col])

    if not sample:
        return ColRole.UNKNOWN

    str_count = sum(1 for v in sample if isinstance(v, str) and v.strip())
    num_count = sum(1 for v in sample if isinstance(v, (int, float)) and not isinstance(v, bool))
    seq_count = sum(1 for v in sample if _looks_like_sequence(v))

    if str_count > len(sample) * 0.6:
        return ColRole.NAME
    if seq_count > len(sample) * 0.5:
        return ColRole.SEQUENCE
    if num_count > len(sample) * 0.5:
        return ColRole.TOTAL  # numeric column without header label -> likely value/total

    return ColRole.UNKNOWN
