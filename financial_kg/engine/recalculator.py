"""Incremental recalculation engine.

Given a set of changed cells (with new values), propagates changes through
the dependency DAG and updates the graph in-place.  Also syncs the Indicator
layer (summary_value, time_series).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.dependency import downstream_cells, build_subgraph_order
from financial_kg.engine.evaluator import evaluate_cell


@dataclass
class CellChange:
    cell_id: str
    old_value: Any
    new_value: Any
    formula: str | None = None


@dataclass
class RecalcResult:
    changed_cells: list[CellChange] = field(default_factory=list)
    error_cells: list[str] = field(default_factory=list)

    @property
    def affected_count(self) -> int:
        return len(self.changed_cells)


def recalculate(
    graph: FinancialGraph,
    updates: dict[str, Any],  # cell_id -> new_value
) -> RecalcResult:
    """Apply updates and propagate through the dependency graph.

    Args:
        graph: The FinancialGraph to mutate in-place.
        updates: Mapping of cell_id → new value for the seed cells.

    Returns:
        RecalcResult with all cells that changed value.
    """
    result = RecalcResult()

    # 1. Apply seed changes
    for cell_id, new_val in updates.items():
        cell = graph.cells.get(cell_id)
        if cell is None:
            continue
        old_val = cell.value
        cell.value = new_val
        if old_val != new_val:
            result.changed_cells.append(
                CellChange(cell_id, old_val, new_val, cell.formula_raw)
            )

    # 2. Find all downstream cells in topological order
    affected = downstream_cells(graph, updates.keys())

    # 3. Re-evaluate each affected formula cell
    for cell_id in affected:
        cell = graph.cells.get(cell_id)
        if cell is None or not cell.formula_raw:
            continue

        old_val = cell.value
        new_val = evaluate_cell(cell_id, graph)

        if new_val is None:
            result.error_cells.append(cell_id)
            continue

        cell.value = new_val
        if old_val != new_val:
            result.changed_cells.append(
                CellChange(cell_id, old_val, new_val, cell.formula_raw)
            )

    # 4. Sync Indicator layer for all changed cells
    _sync_indicators(graph, result.changed_cells)

    return result


def _sync_indicators(graph: FinancialGraph, changes: list[CellChange]) -> None:
    """Update Indicator summary_value and time_series for affected indicators."""
    dirty_indicators: set[str] = set()
    for change in changes:
        cell = graph.cells.get(change.cell_id)
        if cell and cell.indicator_id:
            dirty_indicators.add(cell.indicator_id)

    for ind_id in dirty_indicators:
        ind = graph.indicators.get(ind_id)
        if ind is None:
            continue

        # Rebuild time_series from cells
        new_ts: dict[str, Any] = {}
        summary_val = None

        for cell_id in ind.cell_ids:
            cell = graph.cells.get(cell_id)
            if cell is None:
                continue
            # Check if this cell corresponds to a time-series period
            tbl = graph.tables.get(cell.table_id) if cell.table_id else None
            if tbl and cell.col in tbl.col_roles:
                role = tbl.col_roles[cell.col]
                if role == "time_series":
                    period = tbl.time_period_labels.get(cell.col, cell.col)
                    new_ts[period] = cell.value
                elif role in ("total", "name"):
                    if isinstance(cell.value, (int, float)):
                        summary_val = cell.value

        if new_ts:
            ind.time_series = new_ts
        if summary_val is not None:
            ind.summary_value = summary_val
