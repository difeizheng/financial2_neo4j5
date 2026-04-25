"""Formula evaluator: re-evaluates a cell's formula using the formulas library.

The `formulas` library expects inputs keyed by raw Excel reference strings
(e.g. 'F5', '$I$250', 'F5:BE5', '参数输入表!I250').  Our cell IDs use the
format "{sheet}_{row}_{col}".  This module bridges the two representations.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Optional

import numpy as np

try:
    from formulas import Parser as _FormulaParser
    _PARSER = _FormulaParser()
    _FORMULAS_AVAILABLE = True
except ImportError:
    _FORMULAS_AVAILABLE = False

from financial_kg.models.graph import FinancialGraph


# ── Sheet name mapping for Excel formula quirks ───────────────────────────────
# Some Excel files have formulas referencing wrong/alias sheet names
# Map formula sheet names to actual storage sheet names
_SHEET_NAME_ALIASES = {
    '表1-式样及构造信息表': '表1-资金筹措及还本付息表',
    '表1-资金筹措及还本付息信息表': '表1-资金筹措及还本付息表',
}


def _normalize_sheet_name(sheet: str, graph: FinancialGraph) -> str:
    """Resolve sheet name aliases to actual storage names."""
    # First check alias mapping
    if sheet in _SHEET_NAME_ALIASES:
        return _SHEET_NAME_ALIASES[sheet]

    # Check if sheet exists in graph
    actual_sheets = set()
    for cid in graph.cells.keys():
        parts = cid.rsplit('_', 2)
        if len(parts) == 3:
            actual_sheets.add(parts[0])

    if sheet in actual_sheets:
        return sheet

    # Try to find similar sheet name
    for actual in actual_sheets:
        # Check if names are similar (same prefix)
        if sheet.split('-')[0] == actual.split('-')[0] if '-' in sheet else False:
            # Could be alias, use actual
            return actual

    return sheet


# ── Reference key helpers ────────────────────────────────────────────────────

def _cell_id_to_ref(cell_id: str, formula_sheet: str) -> str:
    """Convert 'Sheet_row_col' → Excel reference key expected by formulas lib.

    Same-sheet cells: 'col + row'  (e.g. 'I250')
    Cross-sheet cells: 'Sheet!col+row' (e.g. '参数输入表!I250')
    """
    # cell_id format: "{sheet}_{row}_{col}"
    # sheet names may contain underscores, so split from the right
    parts = cell_id.rsplit("_", 2)
    if len(parts) != 3:
        return cell_id
    sheet, row, col = parts
    ref = f"{col}{row}"
    if sheet != formula_sheet:
        ref = f"{sheet}!{ref}"
    return ref


def _build_input_map(
    func_inputs: dict,
    formula_sheet: str,
    graph: FinancialGraph,
) -> dict[str, np.ndarray]:
    """Build the kwargs dict for a compiled formulas function.

    func_inputs is the OrderedDict from func.inputs (raw Excel ref strings).
    We look up each referenced cell's current value from the graph.
    """
    kwargs: dict[str, np.ndarray] = {}
    for raw_key in func_inputs:
        value = _resolve_input_key(raw_key, formula_sheet, graph)
        kwargs[raw_key] = value
    return kwargs


def _resolve_input_key(
    raw_key: str,
    formula_sheet: str,
    graph: FinancialGraph,
) -> np.ndarray:
    """Resolve a single formulas input key to a numpy array."""
    # Determine sheet and address
    if "!" in raw_key:
        sheet_part, addr_part = raw_key.split("!", 1)
        sheet_part = sheet_part.strip("'")
        # Normalize sheet name (handle aliases)
        sheet_part = _normalize_sheet_name(sheet_part, graph)
    else:
        sheet_part = formula_sheet
        addr_part = raw_key

    addr_part = addr_part.replace("$", "")

    # Range reference (e.g. F5:BE5)
    if ":" in addr_part:
        return _resolve_range(sheet_part, addr_part, graph)

    # Single cell
    cell_id = _addr_to_cell_id(sheet_part, addr_part)
    cell = graph.cells.get(cell_id)
    val = cell.value if cell is not None else None
    return np.array([[_coerce(val)]])


def _resolve_range(sheet: str, addr: str, graph: FinancialGraph) -> np.ndarray:
    """Resolve a range like 'F5:BE5' to a 2-D numpy array."""
    # Normalize sheet name
    sheet = _normalize_sheet_name(sheet, graph)

    start, end = addr.split(":", 1)
    start_col, start_row = _split_col_row(start)
    end_col, end_row = _split_col_row(end)

    from openpyxl.utils import column_index_from_string, get_column_letter
    sc = column_index_from_string(start_col)
    ec = column_index_from_string(end_col)
    sr, er = int(start_row), int(end_row)

    rows = []
    for r in range(sr, er + 1):
        row_vals = []
        for c in range(sc, ec + 1):
            col_letter = get_column_letter(c)
            cell_id = f"{sheet}_{r}_{col_letter}"
            cell = graph.cells.get(cell_id)
            row_vals.append(_coerce(cell.value if cell else None))
        rows.append(row_vals)

    return np.array(rows)


def _split_col_row(addr: str):
    """Split 'BE5' → ('BE', '5')."""
    m = re.match(r"([A-Za-z]+)(\d+)", addr)
    if not m:
        raise ValueError(f"Cannot parse cell address: {addr!r}")
    return m.group(1).upper(), m.group(2)


def _addr_to_cell_id(sheet: str, addr: str) -> str:
    col, row = _split_col_row(addr)
    return f"{sheet}_{row}_{col}"


def _coerce(val: Any) -> Any:
    """Convert Python value to something numpy/formulas can handle.

    Handles:
    - None -> 0.0 (Excel treats empty cells as 0 in arithmetic)
    - ISO date strings -> Excel serial number (for arithmetic)
    - Numbers -> float
    - Booleans -> bool
    - Strings -> str
    - Error values -> error string (preserve for propagation)
    """
    if val is None:
        # Excel treats empty cells as 0 in arithmetic operations
        # (but not in COUNT/COUNTA functions)
        return 0.0

    # Handle error values (preserve for propagation)
    if isinstance(val, str) and val in ('#NUM!', '#VALUE!', '#DIV/0!', '#REF!', '#N/A'):
        return val

    # Handle ISO date strings (e.g., '2030-08-31T00:00:00')
    if isinstance(val, str) and 'T00:00:00' in val:
        try:
            # Parse ISO date
            dt = datetime.fromisoformat(val.replace('T00:00:00', ''))
            # Convert to Excel serial number
            # Excel uses 1899-12-30 as day 0 (due to 1900-02-29 bug)
            excel_epoch = datetime(1899, 12, 30)
            serial = (dt - excel_epoch).days
            return float(serial)
        except Exception:
            pass

    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return val
    return str(val)


# ── Main evaluation function ─────────────────────────────────────────────────

def evaluate_cell(cell_id: str, graph: FinancialGraph) -> Optional[Any]:
    """Re-evaluate a formula cell and return the new value.

    Returns None if the cell has no formula or evaluation fails.
    """
    if not _FORMULAS_AVAILABLE:
        return None

    cell = graph.cells.get(cell_id)
    if cell is None or not cell.formula_raw:
        return None

    formula = cell.formula_raw
    if not formula.startswith("="):
        formula = "=" + formula

    try:
        ast_result = _PARSER.ast(formula)
        func = ast_result[1].compile()
    except Exception:
        return None

    try:
        kwargs = _build_input_map(func.inputs, cell.sheet, graph)
        result = func(**kwargs)
    except Exception:
        return None

    return _extract_scalar(result)


def _extract_scalar(result: Any) -> Any:
    """Pull a Python scalar out of a numpy array result."""
    if isinstance(result, np.ndarray):
        flat = result.flatten()
        if flat.size == 0:
            return None
        val = flat[0]
        if isinstance(val, float) and np.isnan(val):
            return None
        # Convert numpy types to Python natives
        if isinstance(val, (np.integer,)):
            return int(val)
        if isinstance(val, (np.floating,)):
            return float(val)
        if isinstance(val, (np.bool_,)):
            return bool(val)
        return val
    return result
