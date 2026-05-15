"""ECharts financial chart generators."""

import json


def render_echarts_html(chart_option: dict, height: str = "500px") -> str:
    """Wrap ECharts option in HTML template."""
    option_json = json.dumps(chart_option, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{ margin: 0; padding: 0; overflow: hidden; }}
  #chart {{ width: 100%; height: {height}; min-width: 300px; }}
</style>
</head>
<body>
<div id="chart"></div>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
var chart = echarts.init(document.getElementById('chart'));
var option = {option_json};
chart.setOption(option);
window.addEventListener('resize', () => chart.resize());
setTimeout(() => chart.resize(), 200);
setTimeout(() => chart.resize(), 800);
setTimeout(() => chart.resize(), 1500);
</script>
</body>
</html>"""


def _val(p, key, default=0):
    """Get attribute from dict or object."""
    if hasattr(p, key):
        return getattr(p, key)
    return p.get(key, default)


def render_cashflow_chart(yearly_projections: list, start_year: int = 2026) -> str:
    """Stacked bar: revenue/cost/net cashflow + line: cumulative cashflow."""
    op_data = [p for p in yearly_projections if _val(p, "revenue") > 0]
    years = [str(start_year + (_val(p, "year") - 1)) for p in op_data]

    option = {
        "title": {"text": "现金流预测", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {"data": ["收入", "运营成本", "净现金流", "累计净现金流"], "top": 30},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "xAxis": {"type": "category", "data": years, "axisLabel": {"rotate": 45}},
        "yAxis": [
            {"type": "value", "name": "金额 (万美元)"},
            {"type": "value", "name": "累计 (万美元)"},
        ],
        "series": [
            {
                "name": "收入",
                "type": "bar",
                "stack": "total",
                "data": [round(_val(p, "revenue"), 1) for p in op_data],
                "itemStyle": {"color": "#5470c6"},
            },
            {
                "name": "运营成本",
                "type": "bar",
                "stack": "total",
                "data": [-round(_val(p, "operating_cost"), 1) for p in op_data],
                "itemStyle": {"color": "#ee6666"},
            },
            {
                "name": "净现金流",
                "type": "bar",
                "data": [round(_val(p, "net_cashflow"), 1) for p in op_data],
                "itemStyle": {"color": "#fac858"},
            },
            {
                "name": "累计净现金流",
                "type": "line",
                "yAxisIndex": 1,
                "data": [round(_val(p, "cumulative_cashflow"), 1) for p in op_data],
                "itemStyle": {"color": "#91cc75"},
                "smooth": True,
                "lineStyle": {"width": 3},
            },
        ],
    }
    return render_echarts_html(option)


def render_dscr_chart(yearly_projections: list, start_year: int = 2026) -> str:
    """DSCR trend line with safety threshold at 1.0."""
    dscr_data = [p for p in yearly_projections if _val(p, "dscr") is not None]
    years = [str(start_year + (_val(p, "year") - 1)) for p in dscr_data]
    dscr_values = [round(_val(p, "dscr"), 2) for p in dscr_data]

    option = {
        "title": {"text": "DSCR 偿债备付率趋势", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["DSCR", "安全线 (1.0)"], "top": 30},
        "grid": {"left": "3%", "right": "4%", "bottom": 60, "top": "15%", "containLabel": True},
        "xAxis": {"type": "category", "data": years, "axisLabel": {"rotate": 45, "fontSize": 11, "interval": "auto"}},
        "yAxis": {"type": "value", "name": "DSCR", "min": 0.5},
        "visualMap": {
            "show": False,
            "pieces": [{"lte": 1.0, "color": "#ee6666"}, {"gt": 1.0, "color": "#91cc75"}],
        },
        "series": [
            {
                "name": "DSCR",
                "type": "line",
                "data": dscr_values,
                "smooth": True,
                "lineStyle": {"width": 3},
                "itemStyle": {"color": "#91cc75"},
                "markLine": {
                    "silent": True,
                    "data": [{"yAxis": 1.0}],
                    "lineStyle": {"color": "#ee6666", "type": "dashed", "width": 2},
                    "label": {"formatter": "安全线 1.0", "position": "end"},
                },
            },
        ],
    }
    return render_echarts_html(option, height="450px")


def render_sensitivity_spider(
    factors: list[str],
    sensitivity_matrix: dict,
    base_metrics: dict,
    metric_key: str = "irr_post_tax",
) -> str:
    """Spider/radar chart for sensitivity analysis.

    Values converted to percentage (×100) for readability.
    """
    deltas = sorted(sensitivity_matrix.get(factors[0], {}).keys()) if factors else []

    # Collect all values to compute radar max
    all_vals = []
    for factor in factors:
        factor_data = sensitivity_matrix.get(factor, {})
        for d in deltas:
            val = factor_data.get(d, {}).get(metric_key, 0) * 100
            all_vals.append(abs(val))
    radar_max = max(max(all_vals) * 1.3, 1.0) if all_vals else 10.0

    factor_lines = []
    for factor in factors:
        factor_data = sensitivity_matrix.get(factor, {})
        values = [
            round(factor_data.get(d, {}).get(metric_key, 0) * 100, 2) for d in deltas
        ]
        factor_lines.append(
            {
                "name": {"revenue": "收入", "operating_cost": "经营成本", "investment": "投资"}.get(
                    factor, factor
                ),
                "value": values,
            }
        )

    option = {
        "title": {"text": "敏感性分析 — 税后IRR (%)", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}%"},
        "legend": {"data": [f["name"] for f in factor_lines], "top": 30},
        "radar": {
            "indicator": [{"name": d, "max": round(radar_max, 1)} for d in deltas],
            "shape": "polygon",
            "splitNumber": 5,
            "axisName": {"fontSize": 12},
        },
        "series": [
            {
                "type": "radar",
                "data": factor_lines,
                "areaStyle": {"opacity": 0.2},
            }
        ],
    }
    return render_echarts_html(option)


def render_ebitda_waterfall(yearly_projections: list, start_year: int = 2026) -> str:
    """EBITDA waterfall chart for first 10 operating years."""
    op_data = [p for p in yearly_projections if _val(p, "revenue") > 0][:10]
    years = [str(start_year + (_val(p, "year") - 1)) for p in op_data]

    option = {
        "title": {"text": "EBITDA 趋势 (前10年)", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["EBITDA", "所得税", "折旧"], "top": 30},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "xAxis": {"type": "category", "data": years},
        "yAxis": {"type": "value", "name": "万美元"},
        "series": [
            {
                "name": "EBITDA",
                "type": "bar",
                "data": [round(_val(p, "ebitda"), 1) for p in op_data],
                "itemStyle": {"color": "#5470c6"},
            },
            {
                "name": "所得税",
                "type": "bar",
                "data": [-round(_val(p, "income_tax"), 1) for p in op_data],
                "itemStyle": {"color": "#ee6666"},
            },
            {
                "name": "折旧",
                "type": "bar",
                "data": [-round(_val(p, "depreciation"), 1) for p in op_data],
                "itemStyle": {"color": "#fac858"},
            },
        ],
    }
    return render_echarts_html(option)


def render_profit_trend(yearly_projections: list, start_year: int = 2026) -> str:
    """Multi-line chart: revenue, operating cost, net profit over years."""
    op_data = [p for p in yearly_projections if _val(p, "revenue") > 0]
    years = [str(start_year + (_val(p, "year") - 1)) for p in op_data]

    option = {
        "title": {"text": "利润趋势", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["收入", "运营成本", "净利润"], "top": 30},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "xAxis": {"type": "category", "data": years, "axisLabel": {"rotate": 45}},
        "yAxis": {"type": "value", "name": "万美元"},
        "series": [
            {
                "name": "收入",
                "type": "line",
                "data": [round(_val(p, "revenue"), 1) for p in op_data],
                "itemStyle": {"color": "#5470c6"},
                "smooth": True,
            },
            {
                "name": "运营成本",
                "type": "line",
                "data": [round(_val(p, "operating_cost"), 1) for p in op_data],
                "itemStyle": {"color": "#ee6666"},
                "smooth": True,
            },
            {
                "name": "净利润",
                "type": "bar",
                "data": [round(_val(p, "net_cashflow", _val(p, "ebitda") - _val(p, "income_tax")), 1) for p in op_data],
                "itemStyle": {"color": "#91cc75"},
            },
        ],
    }
    return render_echarts_html(option, height="450px")
