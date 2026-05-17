"""Financial overview panel builder for QA page Tab 1."""
from __future__ import annotations

import json
from typing import Any

from financial_kg.models.graph import FinancialGraph

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"

# Keywords for identifying key financial metrics
_KPI_KEYWORDS = [
    "总投资", "静态投资", "动态投资", "建设投资", "净利润", "利润总额",
    "营业收入", "经营成本", "总成本费用", "净现金流", "IRR", "内部收益率",
    "NPV", "净现值", "毛利率", "偿债备付率", "利息备付率", "增值税",
    "所得税", "税金", "借款", "资本金",
]


def get_overview_kpis(graph: FinancialGraph) -> list[dict[str, Any]]:
    """Extract top-level financial KPIs with values, trends, and unit.

    Returns list of {id, name, value, unit, time_series, score}.
    """
    kpis = []
    seen_names: set[str] = set()

    for ind in graph.indicators.values():
        name = ind.name or ""
        if not name:
            continue
        # Deduplicate by normalized name
        norm = name.replace("（", "(").replace("）", ")")
        if norm in seen_names:
            continue
        score = _kpi_score(name)
        if score < 1:
            continue
        seen_names.add(norm)

        kpis.append({
            "id": ind.id,
            "name": name,
            "value": ind.display_value or _format_value(ind.summary_value),
            "unit": ind.unit or "",
            "time_series": ind.time_series or {},
            "score": score,
        })

    # Sort: exact keyword match first, then by value presence, then by name length
    kpis.sort(key=lambda k: (-k["score"], 0 if k["value"] == "—" else -1, len(k["name"])))
    return kpis[:12]


def _kpi_score(name: str) -> int:
    """Score a name's likelihood of being a top-level KPI."""
    score = 0
    for kw in _KPI_KEYWORDS:
        if kw.lower() in name.lower():
            score += 2
            # Exact match bonus
            if name == kw or name.startswith(kw):
                score += 3
            break
    # Penalize overly long names (likely descriptions, not metrics)
    if len(name) > 30:
        return 0
    if len(name) > 20:
        score -= 1
    return score


def _format_value(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        if abs(val) >= 1e6:
            return f"{val:,.0f}"
        return f"{val:.2f}"
    return str(val)


def build_trend_chart_data(
    graph: FinancialGraph,
    category: str | None = None,
    limit: int = 5,
) -> dict:
    """Build ECharts-compatible time series data for top indicators.

    If category is specified, filter to that category's indicators.
    """
    candidates = []
    for ind in graph.indicators.values():
        if not ind.time_series:
            continue
        if category and ind.category != category:
            continue
        if not ind.name:
            continue
        score = _kpi_score(ind.name)
        if score >= 1:
            candidates.append((score, ind))

    candidates.sort(key=lambda x: (-x[0], len(x[1].name)))
    top = candidates[:limit]

    series = []
    all_periods: set[str] = set()
    for _, ind in top:
        for k in ind.time_series:
            all_periods.add(str(k))
        series.append({
            "name": ind.name,
            "values": ind.time_series,
        })

    periods = sorted(all_periods)
    return {"series": series, "periods": periods}


def render_overview_html(
    kpis: list[dict],
    chart_data: dict,
    height: str = "700px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Render a complete financial overview page as ECharts HTML.

    Shows KPI cards at top, then multi-line time series chart below.
    """
    # Build KPI card data
    card_html = ""
    for k in kpis[:8]:
        card_html += f"""
        <div class="kpi-card">
          <div class="kpi-name">{k["name"]}</div>
          <div class="kpi-val">{k["value"]}</div>
          <div class="kpi-unit">{k["unit"]}</div>
        </div>"""

    # Build ECharts time series
    periods = chart_data.get("periods", [])
    series_data = []
    for s in chart_data.get("series", []):
        vals = s.get("values", {})
        data = []
        for p in periods:
            raw = vals.get(p)
            if raw is None:
                data.append(None)
            elif isinstance(raw, (int, float)):
                data.append(raw)
            else:
                try:
                    data.append(float(raw))
                except (ValueError, TypeError):
                    data.append(None)
        if not all(d is None for d in data):
            series_data.append({"name": s["name"], "data": data})

    # Color palette
    colors = ["#89b4fa", "#a6e3a1", "#fab387", "#f38ba8", "#cba6f7", "#94e2d5"]
    for i, s in enumerate(series_data):
        s["color"] = colors[i % len(colors)]

    series_json = json.dumps(series_data, ensure_ascii=False)
    periods_json = json.dumps(periods, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #181825; font-family: -apple-system, sans-serif; }}
  .overview {{ width: 100%; min-height: {height}; padding: 16px; }}
  .title {{ color: #cdd6f4; font-size: 16px; font-weight: 600; margin-bottom: 12px; }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 8px; margin-bottom: 16px;
  }}
  .kpi-card {{
    background: #1e1e2e; border-radius: 8px; padding: 10px 12px;
    border: 1px solid #313244;
  }}
  .kpi-name {{ color: #a6adc8; font-size: 11px; margin-bottom: 4px; line-height: 1.2;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .kpi-val {{ color: #cdd6f4; font-size: 18px; font-weight: 700; }}
  .kpi-unit {{ color: #6c7086; font-size: 10px; margin-top: 2px; }}
  #chart {{ width: 100%; height: 350px; }}
</style>
</head>
<body>
<div class="overview">
  <div class="title">财务概览</div>
  <div class="kpi-grid">{card_html}</div>
  <div id="chart"></div>
</div>
<script src="{echarts_cdn}"></script>
<script>
var chart = echarts.init(document.getElementById('chart'), 'dark', {{renderer: 'canvas'}});
var series = {series_json};
var periods = {periods_json};

var optSeries = series.map(function(s, i) {{
  var colors = {json.dumps(colors, ensure_ascii=False)};
  return {{
    name: s.name,
    type: 'line',
    data: s.data,
    smooth: true,
    symbol: 'circle',
    symbolSize: 5,
    lineStyle: {{width: 2, color: colors[i % colors.length]}},
    itemStyle: {{color: colors[i % colors.length]}},
    areaStyle: {{color: colors[i % colors.length], opacity: 0.1}},
  }};
}});

chart.setOption({{
  title: {{text: '关键指标趋势', textStyle: {{color: '#cdd6f4', fontSize: 13}}, left: 'center', top: 8}},
  tooltip: {{trigger: 'axis', backgroundColor: '#1e1e2e', borderColor: '#313244', textStyle: {{color: '#cdd6f4'}}}},
  legend: {{data: series.map(function(s) {{ return s.name; }}), textStyle: {{color: '#a6adc8'}}, top: 35}},
  grid: {{left: 60, right: 30, top: 75, bottom: 40}},
  xAxis: {{type: 'category', data: periods, axisLabel: {{color: '#a6adc8', rotate: 30}}, axisLine: {{lineStyle: {{color: '#313244'}}}}}},
  yAxis: {{type: 'value', splitLine: {{lineStyle: {{color: '#313244', type: 'dashed'}}}}, axisLabel: {{color: '#a6adc8'}}}},
  series: optSeries,
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>"""


def build_category_overview(
    graph: FinancialGraph,
    category: str,
) -> dict:
    """Build overview for a specific category: top indicators + trend."""
    inds = []
    for ind in graph.indicators.values():
        if ind.category != category:
            continue
        if not ind.name:
            continue
        score = _kpi_score(ind.name)
        inds.append({
            "id": ind.id,
            "name": ind.name,
            "value": ind.display_value or _format_value(ind.summary_value),
            "unit": ind.unit or "",
            "time_series": ind.time_series or {},
            "score": score,
        })

    inds.sort(key=lambda x: (-x["score"], 0 if x["value"] == "—" else -1))
    return {"indicators": inds[:15]}
