"""Build Indicator nodes (Layer 2) from detected table structures.

Each data row in a financial table becomes one Indicator node.
The Indicator captures: name, category, unit, summary value, time series,
and a human-readable formula description.
"""
from __future__ import annotations
import re
from typing import Any, Optional

from openpyxl.utils import column_index_from_string

from ..models.cell import CellData
from ..models.indicator import Indicator
from ..models.graph import FinancialGraph
from .table_detector import TableInfo, ColRole, detect_tables


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _make_indicator_id(sheet: str, row: int, name: str, category: str) -> str:
    """Create a stable, readable indicator ID."""
    clean = re.sub(r"[^\w一-鿿]", "_", name or "")[:30]
    cat = re.sub(r"[^\w一-鿿]", "_", category or "")[:15]
    return f"IND_{sheet}_{row}_{cat}_{clean}".replace(" ", "_")


def _make_table_id(sheet: str, header_row: int) -> str:
    return f"TBL_{sheet}_{header_row}"


def _make_readable_formula(formula_raw: str, graph: FinancialGraph) -> str:
    """Replace cell IDs in a formula with indicator names where possible."""
    if not formula_raw:
        return ""
    result = formula_raw
    # Find all cell-id-like patterns: SheetName_row_col
    for cell_id, cell in graph.cells.items():
        if cell.indicator_id and cell_id in result:
            ind = graph.indicators.get(cell.indicator_id)
            if ind:
                result = result.replace(cell_id, ind.name)
    return result


# ── Main builder ─────────────────────────────────────────────────────────────

def build_indicators(
    sheet_cells: dict[str, list[CellData]],
    graph: FinancialGraph,
) -> None:
    """Build Indicator and Table nodes and attach them to the graph.

    Modifies graph in-place: adds indicators, tables, and updates
    cell.indicator_id / cell.table_id on each Cell node.
    """
    for sheet_name, cell_list in sheet_cells.items():
        # Build row->col->value lookup from CellData
        rows: dict[int, dict[str, Any]] = {}
        for cd in cell_list:
            rows.setdefault(cd.row, {})[cd.col] = cd.value

        tables = detect_tables(sheet_name, rows)

        for tbl in tables:
            table_id = _make_table_id(sheet_name, tbl.header_row)
            _process_table(tbl, table_id, sheet_name, rows, cell_list, graph)


def _process_table(
    tbl: TableInfo,
    table_id: str,
    sheet_name: str,
    rows: dict[int, dict[str, Any]],
    cell_list: list[CellData],
    graph: FinancialGraph,
) -> None:
    """Process one detected table: create Indicator nodes for each data row."""
    from ..models.table import Table

    name_col = tbl.name_col()
    total_col = tbl.total_col()
    unit_col = tbl.unit_col()
    category_col = tbl.category_col()
    seq_col = tbl.sequence_col()
    ts_cols = tbl.time_series_cols()

    # Build a cell_data lookup: (row, col) -> CellData
    cd_lookup: dict[tuple[int, str], CellData] = {
        (cd.row, cd.col): cd for cd in cell_list
    }

    indicator_ids: list[str] = []
    last_category: str = ""

    for row_num in range(tbl.data_start, tbl.data_end + 1):
        row = rows.get(row_num, {})
        if not row:
            continue

        # ── Extract name ─────────────────────────────────────────────────────
        name = ""
        if name_col and name_col in row:
            name = _safe_str(row[name_col])
        # Fallback: first string value in the row
        if not name:
            for col in sorted(row.keys(), key=lambda c: column_index_from_string(c)):
                v = row[col]
                if isinstance(v, str) and v.strip() and len(v.strip()) > 1:
                    name = v.strip()
                    break
        if not name:
            continue  # skip rows with no identifiable name

        # ── Extract category (carry forward if merged/empty) ─────────────────
        if category_col and category_col in row:
            cat_val = _safe_str(row[category_col])
            if cat_val:
                last_category = cat_val
        category = last_category

        # ── Extract unit ─────────────────────────────────────────────────────
        unit: Optional[str] = None
        if unit_col and unit_col in row:
            unit = _safe_str(row[unit_col]) or None

        # ── Extract summary/total value ──────────────────────────────────────
        summary_value: Any = None
        value_cell_id: Optional[str] = None
        if total_col and total_col in row:
            summary_value = row[total_col]
            value_cell_id = f"{sheet_name}_{row_num}_{total_col}"
        elif name_col:
            # Try the column immediately after name_col
            name_col_idx = column_index_from_string(name_col)
            next_col = None
            for col in sorted(row.keys(), key=lambda c: column_index_from_string(c)):
                if column_index_from_string(col) > name_col_idx and _is_numeric(row[col]):
                    next_col = col
                    break
            if next_col:
                summary_value = row[next_col]
                value_cell_id = f"{sheet_name}_{row_num}_{next_col}"

        # ── Extract time series ──────────────────────────────────────────────
        time_series: dict[str, Any] = {}
        for ts_col in ts_cols:
            if ts_col in row:
                period_label = tbl.time_period_labels.get(ts_col, ts_col)
                time_series[period_label] = row[ts_col]

        # ── Collect all cell IDs for this row ────────────────────────────────
        cell_ids = [
            f"{sheet_name}_{row_num}_{col}"
            for col in row.keys()
            if f"{sheet_name}_{row_num}_{col}" in graph.cells
        ]

        # ── Get formula from value cell ──────────────────────────────────────
        formula_raw: Optional[str] = None
        if value_cell_id and value_cell_id in graph.cells:
            formula_raw = graph.cells[value_cell_id].formula_raw

        # ── Create Indicator ─────────────────────────────────────────────────
        ind_id = _make_indicator_id(sheet_name, row_num, name, category)
        indicator = Indicator(
            id=ind_id,
            name=name,
            sheet=sheet_name,
            row=row_num,
            category=category or None,
            unit=unit,
            summary_value=summary_value,
            formula_readable=formula_raw,  # will be humanized in Phase 5
            time_series=time_series,
            cell_ids=cell_ids,
            value_cell_id=value_cell_id,
            table_id=table_id,
        )
        graph.add_indicator(indicator)
        indicator_ids.append(ind_id)

        # ── Back-link cells to this indicator ────────────────────────────────
        for cid in cell_ids:
            if cid in graph.cells:
                graph.cells[cid].indicator_id = ind_id
                graph.cells[cid].table_id = table_id

    # ── Create Table node ────────────────────────────────────────────────────
    from ..models.table import Table

    # Determine table type from sheet name
    sheet_lower = sheet_name.lower()
    if "参数" in sheet_name:
        table_type = "parameter"
    elif any(k in sheet_name for k in ["利润", "现金", "资产负债", "成本", "收入", "折旧"]):
        table_type = "report"
    else:
        table_type = "calculation"

    table = Table(
        id=table_id,
        name=sheet_name,
        sheet=sheet_name,
        table_type=table_type,
        header_rows=[tbl.header_row],
        data_row_range=[tbl.data_start, tbl.data_end],
        col_roles=tbl.col_roles,
        time_period_labels=tbl.time_period_labels,
        indicator_ids=indicator_ids,
    )
    graph.add_table(table)

    # Back-link cells to table
    for row_num in range(tbl.data_start, tbl.data_end + 1):
        row = rows.get(row_num, {})
        for col in row.keys():
            cid = f"{sheet_name}_{row_num}_{col}"
            if cid in graph.cells and graph.cells[cid].table_id is None:
                graph.cells[cid].table_id = table_id
