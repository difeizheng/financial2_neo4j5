"""Indicator catalog and relation graph for QA page sidebar."""
from __future__ import annotations

import json
from typing import Any

from financial_kg.models.graph import FinancialGraph

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"


def build_indicator_catalog(
    graph: FinancialGraph,
) -> dict[str, dict[str, list[dict]]]:
    """Organize all indicators by category → table hierarchy.

    Returns {category: {table_name: [{id, name, unit, value}, ...], ...}, ...}
    """
    catalog: dict[str, dict[str, list[dict]]] = {}

    for ind in graph.indicators.values():
        cat = ind.category or "未分类"
        tbl_name = "独立指标"
        if ind.table_id:
            tbl = graph.tables.get(ind.table_id)
            tbl_name = tbl.name if tbl else "未知表"

        entry = {
            "id": ind.id,
            "name": ind.name or ind.id,
            "unit": ind.unit or "",
            "value": ind.display_value or _format_value(ind.summary_value),
        }

        catalog.setdefault(cat, {}).setdefault(tbl_name, []).append(entry)

    # Sort indicators within each table by name
    for cat in catalog:
        for tbl in catalog[cat]:
            catalog[cat][tbl].sort(key=lambda x: x["name"])

    return catalog


def _format_value(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        if abs(val) >= 1e6:
            return f"{val:,.0f}"
        return f"{val:.2f}"
    return str(val)


def build_indicator_relation_graph(
    graph: FinancialGraph,
    indicator_id: str,
    depth: int = 2,
) -> dict:
    """Build upstream/downstream relation graph for a given indicator.

    Returns {nodes, edges, categories, stats} for ECharts consumption.
    """
    center = graph.indicators.get(indicator_id)
    if not center:
        return _empty_relation_data()

    # BFS for upstream (depends_on) and downstream (depended_by)
    upstream = _bfs_collect(graph, indicator_id, "depends_on_indicators", depth)
    downstream = _bfs_collect(graph, indicator_id, "depended_by_indicators", depth)

    nodes: list[dict] = []
    seen: set[str] = {indicator_id}

    # Center node
    nodes.append({
        "id": indicator_id,
        "name": center.name[:25] if center.name else indicator_id,
        "category": 0,
        "symbolSize": 36,
        "value": _format_value(center.summary_value),
        "unit": center.unit or "",
        "full_name": center.name,
        "formula": center.formula_readable or "",
    })

    # Upstream nodes
    for ind_id, d in upstream.items():
        if ind_id in seen:
            continue
        seen.add(ind_id)
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        nodes.append({
            "id": ind_id,
            "name": ind.name[:25] if ind.name else ind_id,
            "category": 1,
            "symbolSize": max(12, 28 - d * 5),
            "value": _format_value(ind.summary_value),
            "unit": ind.unit or "",
            "full_name": ind.name,
        })

    # Downstream nodes
    for ind_id, d in downstream.items():
        if ind_id in seen:
            continue
        seen.add(ind_id)
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        nodes.append({
            "id": ind_id,
            "name": ind.name[:25] if ind.name else ind_id,
            "category": 2,
            "symbolSize": max(12, 28 - d * 5),
            "value": _format_value(ind.summary_value),
            "unit": ind.unit or "",
            "full_name": ind.name,
        })

    # Edges
    edges: list[dict] = []
    # Upstream edges
    for ind_id in upstream:
        edges.append({"source": indicator_id, "target": ind_id, "relation": "依赖"})
    # Downstream edges
    for ind_id in downstream:
        edges.append({"source": indicator_id, "target": ind_id, "relation": "影响"})
    # Also add inter-dependency edges between upstream/downstream nodes
    for ind_id in seen:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        for dep_id in ind.depends_on_indicators[:3]:
            if dep_id in seen and dep_id != indicator_id and ind_id != indicator_id:
                edges.append({"source": ind_id, "target": dep_id, "relation": "依赖"})

    categories = [
        {"name": "核心指标", "itemStyle": {"color": "#f38ba8"}},
        {"name": "上游依赖", "itemStyle": {"color": "#89b4fa"}},
        {"name": "下游影响", "itemStyle": {"color": "#a6e3a1"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "upstream_count": sum(1 for n in nodes if n["category"] == 1),
            "downstream_count": sum(1 for n in nodes if n["category"] == 2),
        },
    }


def _bfs_collect(
    graph: FinancialGraph,
    start_id: str,
    attr: str,
    max_depth: int,
) -> dict[str, int]:
    """BFS collect related indicator IDs up to max_depth."""
    result: dict[str, int] = {}
    queue = [(start_id, 0)]
    visited = {start_id}

    while queue:
        cur_id, cur_depth = queue.pop(0)
        if cur_depth >= max_depth:
            continue
        ind = graph.indicators.get(cur_id)
        if not ind:
            continue
        for dep_id in getattr(ind, attr, []):
            if dep_id not in visited and dep_id in graph.indicators:
                visited.add(dep_id)
                result[dep_id] = cur_depth + 1
                queue.append((dep_id, cur_depth + 1))

    return result


def _empty_relation_data() -> dict:
    return {
        "nodes": [],
        "edges": [],
        "categories": [
            {"name": "核心指标", "itemStyle": {"color": "#f38ba8"}},
            {"name": "上游依赖", "itemStyle": {"color": "#89b4fa"}},
            {"name": "下游影响", "itemStyle": {"color": "#a6e3a1"}},
        ],
        "stats": {"total_nodes": 0, "total_edges": 0, "upstream_count": 0, "downstream_count": 0},
    }


def render_relation_html(
    relation_data: dict,
    title: str = "指标关系图",
    height: str = "400px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts force-layout relation graph for indicator upstream/downstream."""
    if not relation_data.get("nodes"):
        return "<p style='color:#a6adc8;padding:20px;text-align:center;'>无关联数据</p>"

    nodes_json = json.dumps(relation_data["nodes"], ensure_ascii=False)
    edges_json = json.dumps(relation_data["edges"], ensure_ascii=False)
    cats_json = json.dumps([c["name"] for c in relation_data["categories"]], ensure_ascii=False)
    cat_items_json = json.dumps(relation_data["categories"], ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #181825; height: {height}; }}
  #chart {{ width: 100%; height: {height}; }}
</style>
</head>
<body>
<div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>
var chart = echarts.init(document.getElementById('chart'), 'dark', {{renderer: 'canvas'}});
var nodes = {nodes_json};
var edges = {edges_json};
var categories = {cat_items_json};

chart.setOption({{
  title: {{text: '{title}', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 13}}}},
  tooltip: {{
    trigger: 'item',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      if (params.dataType !== 'node') return '';
      var d = params.data;
      return '<b>' + (d.full_name || d.name) + '</b><br/>' +
        '值: ' + (d.value || '—') + ' ' + (d.unit || '') +
        (d.formula ? '<br/>公式: ' + d.formula.substring(0, 80) : '');
    }}
  }},
  legend: {{data: {cats_json}, top: 25, textStyle: {{color: '#a6adc8'}}}},
  series: [{{
    type: 'graph',
    layout: 'force',
    data: nodes,
    links: edges,
    categories: categories,
    roam: true,
    draggable: true,
    force: {{
      repulsion: 200,
      gravity: 0.1,
      edgeLength: [80, 150],
      friction: 0.7,
      layoutAnimation: true,
    }},
    edgeSymbol: ['none', 'arrow'],
    edgeSymbolSize: 8,
    emphasis: {{focus: 'adjacency', lineStyle: {{width: 3}}}},
    label: {{show: true, position: 'right', fontSize: 11, color: '#cdd6f4'}},
    lineStyle: {{color: 'source', curveness: 0.15}},
    animationDurationUpdate: 300,
    animationEasingUpdate: 'cubicInOut',
  }}]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""
