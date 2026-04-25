from __future__ import annotations
from typing import Optional
import networkx as nx

from .cell import Cell
from .indicator import Indicator
from .table import Table


class FinancialGraph:
    """Container for the three-layer financial knowledge graph.

    Layer 1: Cell graph (NetworkX DiGraph) — DEPENDS_ON edges from formulas
    Layer 2: Indicator dict — business-level line items
    Layer 3: Table dict — sheet-level logical tables
    """

    def __init__(self, source_file: str = ""):
        self.source_file = source_file
        # Layer 1: cell_id -> Cell
        self.cells: dict[str, Cell] = {}
        # Layer 1: directed dependency graph (cell_id -> cell_id)
        self.cell_graph: nx.DiGraph = nx.DiGraph()
        # Layer 2: indicator_id -> Indicator
        self.indicators: dict[str, Indicator] = {}
        # Layer 3: table_id -> Table
        self.tables: dict[str, Table] = {}

    # ── Cell layer ──────────────────────────────────────────────────────────

    def add_cell(self, cell: Cell) -> None:
        self.cells[cell.id] = cell
        self.cell_graph.add_node(cell.id)

    def add_dependency(self, from_id: str, to_id: str) -> None:
        """from_id DEPENDS_ON to_id (from_id's formula references to_id)."""
        self.cell_graph.add_edge(from_id, to_id)
        if from_id in self.cells and to_id not in self.cells[from_id].dependencies:
            self.cells[from_id].dependencies.append(to_id)
        if to_id in self.cells and from_id not in self.cells[to_id].dependents:
            self.cells[to_id].dependents.append(from_id)

    def get_cell(self, cell_id: str) -> Optional[Cell]:
        return self.cells.get(cell_id)

    # ── Indicator layer ──────────────────────────────────────────────────────

    def add_indicator(self, indicator: Indicator) -> None:
        self.indicators[indicator.id] = indicator

    # ── Table layer ──────────────────────────────────────────────────────────

    def add_table(self, table: Table) -> None:
        self.tables[table.id] = table

    # ── Stats ────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        formula_cells = sum(1 for c in self.cells.values() if c.formula_raw)
        return {
            "total_cells": len(self.cells),
            "formula_cells": formula_cells,
            "dependency_edges": self.cell_graph.number_of_edges(),
            "total_indicators": len(self.indicators),
            "total_tables": len(self.tables),
            "sheets": list({c.sheet for c in self.cells.values()}),
        }
