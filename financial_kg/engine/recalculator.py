"""Incremental recalculation engine.

Given a set of changed cells (with new values), propagates changes through
the dependency DAG and updates the graph in-place.  Also syncs the Indicator
layer (summary_value, time_series).

When circular dependencies exist, iterative evaluation converges within the
strongly connected component (max 100 iterations, tolerance 1e-6).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.dependency import downstream_cells
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
    scc_iterations: int = 0

    @property
    def affected_count(self) -> int:
        return len(self.changed_cells)


def recalculate(
    graph: FinancialGraph,
    updates: dict[str, Any],  # cell_id -> new_value
    max_iter: int = 100,
    tol: float = 1e-9,
    scc_tol: float | None = None,
) -> RecalcResult:
    """Apply updates and propagate through the dependency graph.

    Three-pass convergence for mixed cyclic/acyclic graphs:
    1. Evaluate all affected cells in topological order
    2. SCC fixed-point convergence
    3. Re-eval non-cyclic cells in topo order
    4. Final SCC re-convergence

    No outer loop — bidirectional cyclic↔non-cyclic dependencies cause
    oscillation. The 4-step sequence always terminates.
    """
    if scc_tol is None:
        scc_tol = max(tol, 1e-6)
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

    # 3. Detect cyclic groups within affected cells
    g = graph.cell_graph
    affected_set = set(affected)
    sccs: list[list[str]] = []
    try:
        subgraph = g.subgraph(affected_set | set(updates.keys()))
        for scc in nx.strongly_connected_components(subgraph):
            in_affected = sorted(scc & affected_set)
            if len(in_affected) > 1:
                sccs.append(in_affected)
    except Exception:
        pass
    cyclic_cells = {c for group in sccs for c in group}

    # 4. Re-evaluate each affected formula cell (single pass for acyclic)
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

    # 5. Converge circular dependencies (if any).
    if cyclic_cells:
        sorted_cyclic = sorted(cyclic_cells)
        non_cyclic_affected = [c for c in affected if c not in cyclic_cells]

        # 5a. SCC fixed-point convergence
        for _iter in range(1, max_iter + 1):
            max_delta = 0.0
            for cell_id in sorted_cyclic:
                cell = graph.cells.get(cell_id)
                if cell is None or not cell.formula_raw:
                    continue
                old_val = cell.value
                new_val = evaluate_cell(cell_id, graph)
                if new_val is None:
                    continue
                cell.value = new_val
                try:
                    delta = abs(float(new_val) - float(old_val))
                except (TypeError, ValueError):
                    delta = 1.0
                max_delta = max(max_delta, delta)

            if max_delta <= scc_tol:
                break

        result.scc_iterations = _iter

        # 5b. Re-evaluate non-cyclic cells in topological order
        for cell_id in non_cyclic_affected:
            cell = graph.cells.get(cell_id)
            if cell is None or not cell.formula_raw:
                continue
            old_val = cell.value
            new_val = evaluate_cell(cell_id, graph)
            if new_val is not None and old_val != new_val:
                cell.value = new_val
                result.changed_cells.append(
                    CellChange(cell_id, old_val, new_val, cell.formula_raw)
                )

        # 5c. Final SCC re-convergence after non-cyclic values settled
        for _iter in range(1, max_iter + 1):
            max_delta = 0.0
            for cell_id in sorted_cyclic:
                cell = graph.cells.get(cell_id)
                if cell is None or not cell.formula_raw:
                    continue
                old_val = cell.value
                new_val = evaluate_cell(cell_id, graph)
                if new_val is None:
                    continue
                cell.value = new_val
                try:
                    delta = abs(float(new_val) - float(old_val))
                except (TypeError, ValueError):
                    delta = 1.0
                max_delta = max(max_delta, delta)

            if max_delta <= scc_tol:
                break

    # 6. Sync Indicator layer for all changed cells
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
