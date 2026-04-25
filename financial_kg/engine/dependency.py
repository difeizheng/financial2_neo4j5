"""Dependency DAG utilities: topological sort and downstream BFS."""
from __future__ import annotations
from collections import deque
from typing import Iterable

import networkx as nx

from financial_kg.models.graph import FinancialGraph


def topological_order(graph: FinancialGraph) -> list[str]:
    """Return all cell IDs in topological (evaluation) order via Kahn's algorithm.

    Raises ValueError if a cycle is detected.
    """
    g = graph.cell_graph
    in_degree: dict[str, int] = {n: g.in_degree(n) for n in g.nodes}
    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for successor in g.successors(node):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(order) != g.number_of_nodes():
        cycle_nodes = set(g.nodes) - set(order)
        raise ValueError(f"Cycle detected among {len(cycle_nodes)} cells: {list(cycle_nodes)[:5]}")

    return order


def downstream_cells(graph: FinancialGraph, changed_ids: Iterable[str]) -> list[str]:
    """BFS from changed_ids; return all downstream (dependent) cell IDs in topological order.

    The returned list excludes the seed cells themselves and is sorted so that
    each cell appears after all its predecessors.

    Note: Edge direction is A → B meaning "A depends on B".
    So to find cells that depend on B (i.e., will be affected when B changes),
    we must look at PREDECESSORS of B, not successors.
    """
    g = graph.cell_graph
    seeds = set(changed_ids)
    visited: set[str] = set()
    queue: deque[str] = deque(seeds)

    while queue:
        node = queue.popleft()
        # Find cells that depend on this node (dependents = predecessors in our edge direction)
        for pred in g.predecessors(node):
            if pred not in visited and pred not in seeds:
                visited.add(pred)
                queue.append(pred)

    if not visited:
        return []

    # Return in topological order (only the affected subgraph)
    subgraph = g.subgraph(visited | seeds)
    try:
        full_order = list(nx.topological_sort(subgraph))
    except nx.NetworkXUnfeasible:
        full_order = list(visited)

    return [n for n in full_order if n in visited]


def build_subgraph_order(graph: FinancialGraph, cell_ids: Iterable[str]) -> list[str]:
    """Topological order for an explicit set of cell IDs (used by recalculator)."""
    nodes = set(cell_ids)
    subgraph = graph.cell_graph.subgraph(nodes)
    try:
        return list(nx.topological_sort(subgraph))
    except nx.NetworkXUnfeasible:
        return list(nodes)
