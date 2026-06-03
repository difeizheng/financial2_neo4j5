"""Comparison chart generators: waterfall, KPI dual-bar, multi-snapshot timeline."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.snapshot import SnapshotDiff
from financial_kg.engine.workspace import get_key_metrics

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"

_COLORS = [
    "#89b4fa", "#a6e3a1", "#fab387", "#f38ba8",
    "#cba6f7", "#94e2d5", "#f9e2af", "#74c7ec",
]


# ── KPI dual-bar chart ───────────────────────────────────────────────────────

def build_kpi_data(
    diff: SnapshotDiff,
    graph: FinancialGraph,
) -> list[dict]:
    """Extract KPI A/B values, delta, and delta_pct from diff + graph.

    Returns list of {name, value_a, value_b, delta, delta_pct}.
    """
    kpi_ids = get_key_metrics(graph)

    # Build old/new value maps from diff.changed_cells
    # For KPIs we need indicator-level summary values from affected_indicators
    ind_a_values: dict[str, Any] = {}
    ind_b_values: dict[str, Any] = {}

    # Map indicator name -> summary values from affected_indicators
    for ind in diff.affected_indicators:
        name = ind.get("name", "")
        if name:
            ind_a_values[name] = ind.get("old_summary")
            ind_b_values[name] = ind.get("new_summary")

    # Also check cell-level values for KPIs
    for cell in diff.changed_cells:
        ind_name = cell.get("indicator_name", "")
        if ind_name:
            # Only set if not already set from affected_indicators
            if ind_name not in ind_a_values and "old" in cell:
                ind_a_values[ind_name] = cell["old"]
            if ind_name not in ind_b_values and "new" in cell:
                ind_b_values[ind_name] = cell["new"]

    result = []
    for ind_id in kpi_ids:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        name = ind.name or ind_id
        value_a = ind_a_values.get(name)
        value_b = ind_b_values.get(name)

        # Fallback: use indicator summary_value
        if value_a is None:
            value_a = ind.summary_value
        if value_b is None:
            value_b = ind.summary_value

        delta = None
        delta_pct = None
        if value_a is not None and value_b is not None:
            try:
                delta = float(value_b) - float(value_a)
                if abs(delta) > 1e-9 and float(value_a) != 0:
                    delta_pct = (delta / abs(float(value_a))) * 100
                else:
                    delta = None
            except (ValueError, TypeError):
                delta = None

        result.append({
            "name": name,
            "value_a": value_a,
            "value_b": value_b,
            "delta": delta,
            "delta_pct": delta_pct,
        })

    # Sort: KPIs with changes first, then by delta magnitude
    result.sort(key=lambda x: abs(x.get("delta") or 0), reverse=True)
    return result


# ── Bullet chart (KPI comparison) ────────────────────────────────────────────

def render_bullet_chart_html(
    kpi_data: list[dict],
    snap_a_name: str = "快照 A",
    snap_b_name: str = "快照 B",
    height: str = "500px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts bullet chart: B value as bar, A value as reference mark line."""
    if not kpi_data:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无关键指标数据</p>"

    names = [k["name"][:30] for k in kpi_data]
    values_b = []
    values_a = []
    deltas = []
    for k in kpi_data:
        try:
            vb = float(k["value_b"]) if k["value_b"] is not None else 0
            va = float(k["value_a"]) if k["value_a"] is not None else 0
            values_b.append(vb)
            values_a.append(va)
            d = k.get("delta")
            deltas.append(d if d is not None else 0)
        except (ValueError, TypeError):
            values_b.append(0)
            values_a.append(0)
            deltas.append(0)

    names_json = json.dumps(names, ensure_ascii=False)
    vb_json = json.dumps(values_b)
    va_json = json.dumps(values_a)

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
var names = {names_json};
var valuesB = {vb_json};
var valuesA = {va_json};

chart.setOption({{
  title: {{text: 'KPI 子弹图 — 柱=B值，参考线=A值', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 14}}}},
  tooltip: {{
    trigger: 'axis',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      var idx = params[0].dataIndex;
      return '<b>' + names[idx] + '</b><br/>' +
        'A (基准): ' + valuesA[idx].toFixed(2) + '<br/>' +
        'B (对比): ' + valuesB[idx].toFixed(2);
    }}
  }},
  grid: {{left: 150, right: 30, top: 50, bottom: 10, containLabel: true}},
  xAxis: {{type: 'value', splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}, axisLabel: {{color: '#a6adc8'}}}},
  yAxis: {{type: 'category', data: names, inverse: true, axisLabel: {{color: '#ccc', fontSize: 11}}, axisLine: {{lineStyle: {{color: '#313244'}}}}}},
  series: [
    {{
      name: '{snap_b_name}',
      type: 'bar',
      data: valuesB.map(function(v, i) {{
        var color = v >= valuesA[i] ? '#a6e3a1' : '#f38ba8';
        return {{value: v, itemStyle: {{color: color}}}};
      }}),
      barWidth: 18
    }},
    {{
      name: '{snap_a_name}',
      type: 'scatter',
      symbol: 'diamond',
      symbolSize: 14,
      symbolRotate: 90,
      itemStyle: {{color: '#fab387'}},
      data: valuesA.map(function(v, i) {{ return [v, i]; }})
    }}
  ],
  legend: {{
    data: ['{snap_b_name}', '{snap_a_name} (参考)'],
    top: 30,
    textStyle: {{color: '#a6adc8'}},
    formatter: function(name) {{
      return name === '{snap_a_name} (参考)' ? '◆ ' + '{snap_a_name} (参考)' : '■ ' + '{snap_b_name}';
    }}
  }}
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


def render_kpi_dual_bar_html(
    kpi_data: list[dict],
    snap_a_name: str = "快照 A",
    snap_b_name: str = "快照 B",
    height: str = "420px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts dual-bar chart: KPI A vs B side by side."""
    if not kpi_data:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无关键指标数据</p>"

    names = [k["name"][:25] for k in kpi_data]
    values_a = []
    values_b = []
    for k in kpi_data:
        try:
            values_a.append(float(k["value_a"]) if k["value_a"] is not None else 0)
        except (ValueError, TypeError):
            values_a.append(0)
        try:
            values_b.append(float(k["value_b"]) if k["value_b"] is not None else 0)
        except (ValueError, TypeError):
            values_b.append(0)

    names_json = json.dumps(names, ensure_ascii=False)
    va_json = json.dumps(values_a)
    vb_json = json.dumps(values_b)

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
chart.setOption({{
  title: {{text: '关键指标 A/B 对比', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 14}}}},
  tooltip: {{trigger: 'axis', backgroundColor: '#1e1e2e', borderColor: '#313244', textStyle: {{color: '#cdd6f4'}}}},
  legend: {{data: ['{snap_a_name}', '{snap_b_name}'], top: 30, textStyle: {{color: '#a6adc8'}}}},
  grid: {{left: 8, right: 30, top: 60, bottom: 40, containLabel: true}},
  xAxis: {{type: 'category', data: {names_json}, axisLabel: {{color: '#a6adc8', rotate: 45, fontSize: 11}}, axisLine: {{lineStyle: {{color: '#313244'}}}}}},
  yAxis: {{type: 'value', splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}, axisLabel: {{color: '#a6adc8'}}}},
  series: [
    {{name: '{snap_a_name}', type: 'bar', data: {va_json}, itemStyle: {{color: '#89b4fa'}}, barWidth: '35%'}},
    {{name: '{snap_b_name}', type: 'bar', data: {vb_json}, itemStyle: {{color: '#fab387'}}, barWidth: '35%'}}
  ]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


# ── Waterfall chart ──────────────────────────────────────────────────────────

def build_waterfall_data(
    diff: SnapshotDiff,
    graph: FinancialGraph,
) -> dict:
    """Build waterfall data: baseline → sheet contributions → final.

    Returns {items: [{name, value, type}], baseline, final, total_increase, total_decrease}.
    """
    sheet_contrib: dict[str, float] = {}
    total_increase = 0.0
    total_decrease = 0.0

    for cell in diff.changed_cells:
        sheet = cell.get("sheet", "Unknown")
        mag = cell.get("change_magnitude", 0) or 0
        direction = cell.get("direction", "unchanged")

        if direction == "decrease":
            mag = -mag
            total_decrease += abs(mag)
        else:
            total_increase += mag

        sheet_contrib[sheet] = sheet_contrib.get(sheet, 0) + mag

    # Sort sheets by absolute contribution
    sorted_sheets = sorted(sheet_contrib.items(), key=lambda x: abs(x[1]), reverse=True)

    # Build waterfall items
    items = []
    baseline = 0.0  # relative view: start from 0, show contributions
    current = 0.0

    for sheet, contrib in sorted_sheets:
        items.append({
            "name": sheet,
            "value": round(contrib, 2),
            "type": "contribution",
            "color": "#5470c6" if contrib > 0 else "#ee6666",
        })
        current += contrib

    items.insert(0, {
        "name": "基准",
        "value": 0,
        "type": "base",
        "color": "#91cc75",
    })

    items.append({
        "name": "净变化",
        "value": round(current, 2),
        "type": "total",
        "color": "#91cc75",
    })

    return {
        "items": items,
        "total_increase": round(total_increase, 2),
        "total_decrease": round(total_decrease, 2),
        "net_change": round(current, 2),
    }


def render_waterfall_html(
    waterfall_data: dict,
    title: str = "变化贡献瀑布图",
    height: str = "420px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts waterfall chart: sheet contributions to net change."""
    items = waterfall_data.get("items", [])
    if len(items) <= 2:
        return f"<p style='color:#a6adc8;padding:40px;text-align:center;'>{title}：数据不足</p>"

    names = [i["name"] for i in items]
    values = [i["value"] for i in items]
    types = [i["type"] for i in items]
    colors = [i.get("color", "#5470c6") for i in items]

    # Compute transparent bars for waterfall effect
    # Each contribution bar starts from cumulative previous value
    cumulative = [0] * len(values)
    running = 0
    for i in range(len(values)):
        if types[i] == "base":
            cumulative[i] = 0
        elif types[i] == "total":
            cumulative[i] = 0
        else:
            cumulative[i] = running
            running += values[i]

    names_json = json.dumps(names, ensure_ascii=False)
    values_json = json.dumps(values)
    cumulative_json = json.dumps(cumulative)
    types_json = json.dumps(types)
    colors_json = json.dumps(colors)
    total_increase = waterfall_data.get("total_increase", 0)
    total_decrease = waterfall_data.get("total_decrease", 0)
    net_change = waterfall_data.get("net_change", 0)

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
var names = {names_json};
var values = {values_json};
var cumulative = {cumulative_json};
var types = {types_json};
var colors = {colors_json};

// Build waterfall data: transparent stack + visible bar
var baseData = [];
var visibleData = [];
for (var i = 0; i < values.length; i++) {{
  if (types[i] === 'base') {{
    baseData.push(0);
    visibleData.push({{value: values[i], itemStyle: {{color: '#91cc75'}}}});
  }} else if (types[i] === 'total') {{
    baseData.push(0);
    visibleData.push({{value: values[i], itemStyle: {{color: '#91cc75'}}}});
  }} else {{
    baseData.push(cumulative[i]);
    visibleData.push({{value: values[i], itemStyle: {{color: values[i] >= 0 ? '#5470c6' : '#ee6666'}}}});
  }}
}}

chart.setOption({{
  title: {{text: '{title}', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 14}}}},
  tooltip: {{
    trigger: 'axis',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      var idx = params[0].dataIndex;
      var v = values[idx];
      if (types[idx] === 'base') return '<b>基准</b>: 0';
      if (types[idx] === 'total') return '<b>净变化</b>: ' + v.toFixed(2);
      return '<b>' + names[idx] + '</b><br/>' +
        (v >= 0 ? '↑ 增加: ' : '↓ 减少: ') + Math.abs(v).toFixed(2);
    }}
  }},
  legend: {{data: ['累计', '变化'], top: 30, textStyle: {{color: '#a6adc8'}}}},
  grid: {{left: 8, right: 30, top: 60, bottom: 80, containLabel: true}},
  xAxis: {{type: 'category', data: names, axisLabel: {{color: '#a6adc8', rotate: 45, fontSize: 10, interval: 0}}, axisLine: {{lineStyle: {{color: '#313244'}}}}}},
  yAxis: {{type: 'value', splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}, axisLabel: {{color: '#a6adc8'}}}},
  series: [
    {{name: '累计', type: 'bar', stack: 'total', data: baseData.map(function(v) {{ return {{value: v, itemStyle: {{color: 'transparent'}}}}; }}), barWidth: '50%'}},
    {{name: '变化', type: 'bar', stack: 'total', data: visibleData, barWidth: '50%'}}
  ]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


# ── Tornado chart ─────────────────────────────────────────────────────────────

_LEVEL_TO_KEY = {
    "Sheet": "sheet_contrib",
    "Indicator": "indicator_contrib",
    "Category": "category_contrib",
}


def build_tornado_data(
    diff: SnapshotDiff,
    graph: FinancialGraph,
    top_n: int = 30,
    level: str = "Sheet",
    precomputed: dict | None = None,
) -> dict:
    """Build tornado (diverging bar) data: top N items by |delta|, split +/−.

    Args:
        diff: snapshot diff (used as fallback if precomputed absent)
        graph: financial graph
        top_n: number of top items to display
        level: aggregation level — "Sheet" | "Indicator" | "Category"
        precomputed: summary dict from compute_change_summary, with
            sheet_contrib / indicator_contrib / category_contrib fields.
            When provided, aggregation is O(N) once at diff time (cached).
            When None, falls back to recomputing from diff.changed_cells.

    Returns:
        {
            items: [{name, value}],
            all_names, all_pos_values, all_neg_values,
        }
    """
    contrib: dict[str, float] = {}
    if precomputed and level in _LEVEL_TO_KEY:
        contrib = precomputed.get(_LEVEL_TO_KEY[level], {}) or {}
    else:
        # Fallback: recompute from cells (only Sheet level for compatibility)
        for cell in diff.changed_cells:
            sheet = cell.get("sheet", "(无 Sheet)")
            mag = cell.get("change_magnitude", 0) or 0
            if not isinstance(mag, (int, float)):
                continue
            if cell.get("direction") == "decrease":
                mag = -mag
            contrib[sheet] = contrib.get(sheet, 0.0) + mag

    # Sort by absolute contribution, take top N
    sorted_items = sorted(contrib.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n]

    # Truncate indicator names for axis labels (full name in tooltip)
    display_names = [n[:25] + "…" if len(n) > 25 else n for n, _ in sorted_items]
    pos_values = [round(v, 2) if v >= 0 else 0 for _, v in sorted_items]
    neg_values = [round(-v, 2) if v < 0 else 0 for _, v in sorted_items]
    full_names = [n for n, _ in sorted_items]

    return {
        "items": [{"name": n, "value": round(v, 2)} for n, v in sorted_items],
        "all_names": display_names,
        "all_pos_values": pos_values,
        "all_neg_values": neg_values,
        "full_names": full_names,
    }


def render_tornado_html(
    tornado_data: dict,
    title: str = "变化贡献龙卷风图",
    snap_a_name: str = "基准",
    snap_b_name: str = "对比",
    level: str = "Sheet",
    height: str = "420px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts tornado (diverging bar) chart. Positive bars right, negative left.

    Uses display names (truncated) for axis labels and full names in tooltip.
    """
    names = tornado_data.get("all_names", [])
    pos_values = tornado_data.get("all_pos_values", [])
    neg_values = tornado_data.get("all_neg_values", [])
    full_names = tornado_data.get("full_names", names)

    if not names:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无变化数据</p>"

    names_json = json.dumps(names, ensure_ascii=False)
    full_names_json = json.dumps(full_names, ensure_ascii=False)
    pos_json = json.dumps(pos_values)
    neg_json = json.dumps(neg_values)
    full_title = f"{title} (按{level})"

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
var names = {names_json};
var fullNames = {full_names_json};
var pos = {pos_json};
var neg = {neg_json};

chart.setOption({{
  title: {{text: '{full_title}', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 14}}}},
  tooltip: {{
    trigger: 'axis',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      var idx = params[0].dataIndex;
      var p = pos[idx];
      var n = neg[idx];
      var lines = ['<b>' + fullNames[idx] + '</b>'];
      if (p > 0) lines.push('↑ 增加: +' + p.toFixed(2));
      if (n > 0) lines.push('↓ 减少: -' + n.toFixed(2));
      var net = p - n;
      lines.push('净变化: ' + (net >= 0 ? '+' : '') + net.toFixed(2));
      return lines.join('<br/>');
    }}
  }},
  legend: {{data: ['增加', '减少'], top: 30, textStyle: {{color: '#a6adc8'}}}},
  grid: [
    {{left: 10, right: '50%', top: 50, bottom: 20}},
    {{left: '50%', right: 10, top: 50, bottom: 20}}
  ],
  xAxis: [
    {{
      gridIndex: 0, type: 'value', inverse: true,
      axisLabel: {{show: false}},
      splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}},
      axisLine: {{lineStyle: {{color: '#313244'}}}}
    }},
    {{
      gridIndex: 1, type: 'value',
      axisLabel: {{show: false}},
      splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}},
      axisLine: {{lineStyle: {{color: '#313244'}}}}
    }}
  ],
  yAxis: [
    {{
      gridIndex: 0, type: 'category', inverse: true,
      data: names,
      axisLabel: {{color: '#ccc', fontSize: 11}},
      axisLine: {{lineStyle: {{color: '#313244'}}}},
      axisTick: {{show: false}}
    }},
    {{
      gridIndex: 1, type: 'category', inverse: true,
      data: names,
      axisLabel: {{show: false}},
      axisLine: {{lineStyle: {{color: '#313244'}}}},
      axisTick: {{show: false}}
    }}
  ],
  series: [
    {{
      name: '减少', type: 'bar', xAxisIndex: 0, yAxisIndex: 0,
      data: neg.map(function(v) {{ return {{value: v, itemStyle: {{color: '#f38ba8'}}}}; }}),
      barWidth: '60%', label: {{show: true, position: 'left', color: '#f38ba8', fontSize: 10}}
    }},
    {{
      name: '增加', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
      data: pos.map(function(v) {{ return {{value: v, itemStyle: {{color: '#a6e3a1'}}}}; }}),
      barWidth: '60%', label: {{show: true, position: 'right', color: '#a6e3a1', fontSize: 10}}
    }}
  ],
  dataZoom: [
    {{type: 'slider', yAxisIndex: 0, right: 2, top: 50, bottom: 20, width: 8, show: true}},
    {{type: 'inside', yAxisIndex: 0}}
  ]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


# ── Change distribution histogram ────────────────────────────────────────────

def build_change_distribution(
    diff: SnapshotDiff,
    bin_count: int = 10,
) -> dict:
    """Build histogram of |change_magnitude| over all changed cells.

    Log-scale bins (each bin is 10x the previous) handle the heavy-tailed
    distribution typical in financial models (a few huge changes, many small).

    Returns:
        {
            bins: [{label, lo, hi, count}],
            total_cells, total_log_sum,
            min_mag, max_mag, median_mag,
        }
    """
    import math

    magnitudes: list[float] = []
    for c in diff.changed_cells:
        mag = c.get("change_magnitude", 0)
        if isinstance(mag, (int, float)) and mag > 0:
            magnitudes.append(float(mag))

    if not magnitudes:
        return {"bins": [], "total_cells": 0, "min_mag": 0, "max_mag": 0, "median_mag": 0}

    magnitudes.sort()
    min_mag = magnitudes[0]
    max_mag = magnitudes[-1]
    median_mag = magnitudes[len(magnitudes) // 2]

    # Log-scale bins from 10^floor(log10(min)) to 10^ceil(log10(max))
    log_min = math.floor(math.log10(min_mag))
    log_max = math.ceil(math.log10(max_mag))
    if log_max <= log_min:
        log_max = log_min + 1

    bins = []
    for i in range(log_min, log_max):
        lo = 10 ** i
        hi = 10 ** (i + 1)
        if i == log_min:
            count = sum(1 for m in magnitudes if lo <= m < hi)
        elif i == log_max - 1:
            count = sum(1 for m in magnitudes if lo <= m <= hi)
        else:
            count = sum(1 for m in magnitudes if lo <= m < hi)
        if count > 0:
            bins.append({
                "label": f"10^{i}",
                "lo": lo,
                "hi": hi,
                "count": count,
            })

    return {
        "bins": bins,
        "total_cells": len(magnitudes),
        "min_mag": min_mag,
        "max_mag": max_mag,
        "median_mag": median_mag,
    }


def render_change_distribution_html(
    dist_data: dict,
    snap_a_name: str = "基准",
    snap_b_name: str = "对比",
    height: str = "380px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts log-scale histogram of change magnitudes."""
    bins = dist_data.get("bins", [])
    if not bins:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无数值型变化</p>"

    labels = [b["label"] for b in bins]
    counts = [b["count"] for b in bins]
    labels_json = json.dumps(labels)
    counts_json = json.dumps(counts)

    median = dist_data.get("median_mag", 0)
    total = dist_data.get("total_cells", 0)
    subtitle = f"共 {total:,} 个变化单元格 · 中位数 {median:.2f}"

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
var labels = {labels_json};
var counts = {counts_json};

chart.setOption({{
  title: {{
    text: '变化幅度分布（对数刻度）',
    subtext: '{subtitle}',
    left: 'center',
    textStyle: {{color: '#cdd6f4', fontSize: 14}},
    subtextStyle: {{color: '#a6adc8', fontSize: 11}}
  }},
  tooltip: {{
    trigger: 'axis',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      var idx = params[0].dataIndex;
      return '<b>' + labels[idx] + '</b><br/>' +
        '区间: [' + Math.pow(10, idx) + ', ' + Math.pow(10, idx + 1) + ')<br/>' +
        '单元格数: ' + counts[idx];
    }}
  }},
  grid: {{left: 60, right: 30, top: 70, bottom: 40}},
  xAxis: {{
    type: 'category', data: labels,
    axisLabel: {{color: '#a6adc8'}},
    axisLine: {{lineStyle: {{color: '#313244'}}}}
  }},
  yAxis: {{
    type: 'value', name: '单元格数',
    nameTextStyle: {{color: '#a6adc8'}},
    axisLabel: {{color: '#a6adc8'}},
    splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}
  }},
  series: [{{
    type: 'bar',
    data: counts.map(function(v) {{
      return {{value: v, itemStyle: {{
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          {{offset: 0, color: '#89b4fa'}}, {{offset: 1, color: '#45475a'}}
        ])
      }}}};
    }}),
    barWidth: '70%',
    label: {{show: true, position: 'top', color: '#cdd6f4', fontSize: 11}}
  }}]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


# ── Category breakdown ────────────────────────────────────────────────────────

def build_category_breakdown(
    diff: SnapshotDiff,
    graph: FinancialGraph,
) -> dict:
    """Aggregate change_magnitude by indicator.category.

    Returns:
        {
            items: [{category, increase, decrease, net, cell_count}],
        }
    """
    # Build indicator_name -> category lookup
    name_to_category: dict[str, str] = {}
    for ind in graph.indicators.values():
        if ind.name and ind.category:
            name_to_category[ind.name] = ind.category

    by_cat: dict[str, dict] = {}
    for c in diff.changed_cells:
        cat = "(无 Indicator)"
        ind_name = c.get("indicator_name", "") or ""
        if ind_name:
            cat = name_to_category.get(ind_name, "(未分类)")
        if cat not in by_cat:
            by_cat[cat] = {"category": cat, "increase": 0.0, "decrease": 0.0, "cell_count": 0}
        mag = c.get("change_magnitude", 0) or 0
        if not isinstance(mag, (int, float)):
            mag = 0
        direction = c.get("direction", "")
        if direction == "increase":
            by_cat[cat]["increase"] += mag
        elif direction == "decrease":
            by_cat[cat]["decrease"] += mag
        by_cat[cat]["cell_count"] += 1

    items = []
    for v in by_cat.values():
        v["net"] = v["increase"] - v["decrease"]
        v["increase"] = round(v["increase"], 2)
        v["decrease"] = round(v["decrease"], 2)
        v["net"] = round(v["net"], 2)
        items.append(v)

    # Sort by absolute net change
    items.sort(key=lambda x: abs(x["net"]), reverse=True)
    return {"items": items}


def render_category_breakdown_html(
    cat_data: dict,
    snap_a_name: str = "基准",
    snap_b_name: str = "对比",
    height: str = "420px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """ECharts horizontal stacked bar: per-category increase vs decrease."""
    items = cat_data.get("items", [])
    if not items:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无类别聚合数据</p>"

    # Display truncated names; full names in tooltip
    names = [i["category"][:18] + "…" if len(i["category"]) > 18 else i["category"] for i in items]
    full_names = [i["category"] for i in items]
    increases = [i["increase"] for i in items]
    decreases = [i["decrease"] for i in items]
    nets = [i["net"] for i in items]
    counts = [i["cell_count"] for i in items]

    names_json = json.dumps(names, ensure_ascii=False)
    full_names_json = json.dumps(full_names, ensure_ascii=False)
    inc_json = json.dumps(increases)
    dec_json = json.dumps(decreases)
    net_json = json.dumps(nets)
    counts_json = json.dumps(counts)

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
var names = {names_json};
var fullNames = {full_names_json};
var incs = {inc_json};
var decs = {dec_json};
var nets = {net_json};
var counts = {counts_json};

chart.setOption({{
  title: {{text: '按类别聚合变化', left: 'center', textStyle: {{color: '#cdd6f4', fontSize: 14}}}},
  tooltip: {{
    trigger: 'axis',
    backgroundColor: '#1e1e2e',
    borderColor: '#313244',
    textStyle: {{color: '#cdd6f4'}},
    formatter: function(params) {{
      var idx = params[0].dataIndex;
      var lines = ['<b>' + fullNames[idx] + '</b>',
        '↑ 增加: +' + incs[idx].toFixed(2),
        '↓ 减少: -' + decs[idx].toFixed(2),
        '净变化: ' + (nets[idx] >= 0 ? '+' : '') + nets[idx].toFixed(2),
        '单元格数: ' + counts[idx]];
      return lines.join('<br/>');
    }}
  }},
  legend: {{data: ['增加', '减少'], top: 30, textStyle: {{color: '#a6adc8'}}}},
  grid: {{left: 10, right: 60, top: 70, bottom: 20}},
  xAxis: {{
    type: 'value',
    axisLabel: {{color: '#a6adc8'}},
    splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}
  }},
  yAxis: {{
    type: 'category', inverse: true,
    data: names,
    axisLabel: {{color: '#ccc', fontSize: 11}},
    axisLine: {{lineStyle: {{color: '#313244'}}}}
  }},
  series: [
    {{
      name: '减少', type: 'bar',
      data: decs.map(function(v) {{ return {{value: -v, itemStyle: {{color: '#f38ba8'}}}}; }}),
      barWidth: '50%',
      label: {{
        show: true, position: 'left', color: '#f38ba8', fontSize: 10,
        formatter: function(p) {{ return p.value < 0 ? p.value.toFixed(0) : ''; }}
      }}
    }},
    {{
      name: '增加', type: 'bar',
      data: incs.map(function(v) {{ return {{value: v, itemStyle: {{color: '#a6e3a1'}}}}; }}),
      barWidth: '50%',
      label: {{
        show: true, position: 'right', color: '#a6e3a1', fontSize: 10,
        formatter: function(p) {{ return p.value > 0 ? '+' + p.value.toFixed(0) : ''; }}
      }}
    }}
  ],
  dataZoom: [
    {{type: 'slider', yAxisIndex: 0, right: 2, top: 70, bottom: 20, width: 8, show: true}},
    {{type: 'inside', yAxisIndex: 0}}
  ]
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


# ── Multi-snapshot timeline data builder ──────────────────────────────────────

@lru_cache(maxsize=4)
def _build_timeline_data_cached(
    snap_filepaths: tuple[str, ...],
    snap_names: tuple[str, ...],
    kpi_info: tuple[tuple[str, str, tuple[str, ...]], ...],  # (ind_id, name, cell_ids)
) -> list[dict]:
    """Cached timeline builder — all args must be hashable.

    kpi_info: tuple of (ind_id, name, cell_ids) for each KPI indicator.
    """
    from financial_kg.engine.snapshot import load_snapshot

    result = []
    for ind_id, name, cell_ids in kpi_info:
        values = {}
        for i, fp in enumerate(snap_filepaths):
            try:
                snap_obj = load_snapshot(fp)
                for cid in cell_ids[:5]:
                    v = snap_obj.values.get(cid)
                    if v is not None:
                        try:
                            values[snap_names[i]] = float(v)
                            break
                        except (ValueError, TypeError):
                            continue
            except Exception:
                continue
        if len(values) >= 2:
            result.append({"name": name, "values": values})
    return result


def build_timeline_data(
    snapshots: list,
    graph: FinancialGraph,
) -> list[dict]:
    """Build time series data for KPIs across multiple snapshots.

    snapshots: list of Snapshot objects (from task_db)
    Returns: [{name: str, values: {snapshot_label: value}}, ...]
    """
    kpi_ids = get_key_metrics(graph)
    if not kpi_ids or not snapshots:
        return []

    snap_filepaths = tuple(s.filepath for s in snapshots)
    snap_names = tuple(s.name for s in snapshots)
    kpi_info = []
    for kid in kpi_ids:
        ind = graph.indicators.get(kid)
        if ind:
            kpi_info.append((kid, ind.name or kid, tuple(ind.cell_ids)))

    try:
        return _build_timeline_data_cached(snap_filepaths, snap_names, tuple(kpi_info))
    except Exception:
        # Fallback: uncached
        result = []
        for ind_id in kpi_ids:
            ind = graph.indicators.get(ind_id)
            if not ind:
                continue
            name = ind.name or ind_id
            values = {}
            for snap in snapshots:
                snap_obj = _load_snapshot_safe(snap)
                if snap_obj is None:
                    continue
                for cid in ind.cell_ids[:5]:
                    v = snap_obj.values.get(cid)
                    if v is not None:
                        try:
                            values[snap.name] = float(v)
                            break
                        except (ValueError, TypeError):
                            continue
                if snap.name not in values:
                    if ind.summary_value is not None:
                        values[snap.name] = ind.summary_value
            if len(values) >= 2:
                result.append({"name": name, "values": values})
        return result


def _load_snapshot_safe(snap_record):
    """Safely load a snapshot, returning None on failure."""
    from financial_kg.engine.snapshot import load_snapshot
    try:
        return load_snapshot(snap_record.filepath)
    except Exception:
        return None
