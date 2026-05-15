"""Comparison chart generators: waterfall, KPI dual-bar, multi-snapshot timeline."""
from __future__ import annotations

import json
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


# ── Multi-snapshot timeline data builder ──────────────────────────────────────

def build_timeline_data(
    snapshots: list,
    graph: FinancialGraph,
) -> list[dict]:
    """Build time series data for KPIs across multiple snapshots.

    snapshots: list of Snapshot objects (from task_db)
    Returns: [{name: str, values: {snapshot_label: value}}, ...]
    """
    kpi_ids = get_key_metrics(graph)

    result = []
    for ind_id in kpi_ids:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        name = ind.name or ind_id

        # For each snapshot, try to find the indicator's value
        values = {}
        for snap in snapshots:
            # Try to load snapshot and extract value for this indicator
            snap_obj = _load_snapshot_safe(snap)
            if snap_obj is None:
                continue

            # Find the indicator's value_cell_id or first cell_id
            for cid in ind.cell_ids[:5]:
                if cid in snap_obj.values:
                    v = snap_obj.values[cid]
                    if v is not None:
                        try:
                            values[snap.name] = float(v)
                            break
                        except (ValueError, TypeError):
                            continue

            # Fallback: use indicator summary_value
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
