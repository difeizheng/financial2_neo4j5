"""Page 6: Financial benefit analysis — graph-sourced financial calculator.

Reads real financial data from the uploaded Excel model's knowledge graph
and snapshot, displaying IRR/NPV/DSCR/cash flow per the Word report template.
Supports parameter editing with graph-based recalculation and full financial
table display.
"""
from __future__ import annotations

import copy
import datetime
import os
import sys
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.engine.financial_metrics import (
    FinanceParams,
    run_sensitivity_analysis,
)
from financial_kg.engine.kg_extractor import (
    extract_base_params,
    extract_financial_metrics,
    extract_full_table,
    extract_params_with_cell_ids,
    extract_yearly_data,
)
from financial_kg.engine.snapshot import create_snapshot, load_snapshot
from financial_kg.engine.workspace import (
    apply_and_recalc,
    load_workspace,
    save_workspace,
    Scenario,
)
from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.viz.finance_charts import (
    render_cashflow_chart,
    render_dscr_chart,
    render_ebitda_waterfall,
    render_profit_trend,
    render_sensitivity_spider,
)

def _to_year_projections(yearly_data: list) -> list:
    """Convert dict-based yearly data to YearProjection-like objects."""
    from financial_kg.engine.financial_metrics import YearProjection

    result = []
    for entry in yearly_data:
        if isinstance(entry, dict):
            result.append(YearProjection(
                year=entry.get("year", 0),
                revenue=entry.get("revenue", 0),
                operating_cost=entry.get("operating_cost", 0),
                ebitda=entry.get("ebitda", 0),
                income_tax=entry.get("income_tax", 0),
                debt_service=entry.get("debt_service", 0),
                net_cashflow=entry.get("net_cashflow", 0),
                cumulative_cashflow=entry.get("cumulative_cashflow", 0),
                dscr=entry.get("dscr"),
            ))
        else:
            result.append(entry)
    return result


def _format_delta(delta: float | None) -> str | None:
    """Format delta for st.metric display."""
    if delta is None:
        return None
    if abs(delta) < 1:
        return f"{delta*100:+.3f}%"
    return f"{delta:+,.2f}"


def _build_params_from_kg(kg_params: dict) -> FinanceParams:
    """Map KG-extracted params to FinanceParams fields for sensitivity analysis."""
    fp = FinanceParams()
    mapping = {
        "EPC投资": "epc_cost",
        "征地费": "land_cost",
        "长期借款利率": "loan_rate",
        "宽限期": "grace_periods",
        "上网电价": "electricity_price",
        "建设期": "construction_years",
        "运营起始年": "start_year",
        "经营成本": "operating_cost",
    }
    for kg_name, fp_attr in mapping.items():
        if kg_name in kg_params:
            val = kg_params[kg_name].get("value")
            if val is not None:
                try:
                    setattr(fp, fp_attr, float(val))
                except (ValueError, TypeError):
                    pass
    return fp


# Table ID mapping for Word template tables
FINANCE_TABLE_IDS = {
    "利润表（全投资）": "TBL_表7-利润表-全投资_3_B_BA",
    "现金流量表（全投资）": "TBL_表8-现金流量表-全投资_3_B_BE",
    "资产负债表": "TBL_表10-资产负债表_3_B_AZ",
    "资金筹措表": "TBL_表1-资金筹措及还本付息表_3_B_BE",
}


st.set_page_config(page_title="财务效益分析", layout="wide", page_icon="")

st.title("财务效益分析")

# ── Task selector ────────────────────────────────────────────────────────────

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]
task_options = {f"{t.id} — {t.filename}": t for t in tasks}

if not task_options:
    st.warning("暂无已完成的任务。请先在「上传解析」页面解析 Excel 模型。")
    st.stop()

selected_label = st.selectbox("选择任务", list(task_options.keys()), index=0)
task = task_options[selected_label]

# ── Load graph + snapshot + workspace ────────────────────────────────────────

@st.cache_resource(show_spinner="加载图谱...")
def _load_graph_cached(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load_graph_cached(task.id, task.output_dir)

# Load latest snapshot
snapshot_files = db.list_snapshot_files(task.id)
snapshot_values: dict = {}
if snapshot_files:
    latest_snap = snapshot_files[0]
    snap_path = latest_snap if os.path.isabs(latest_snap) or latest_snap.startswith("snapshots") else os.path.join("snapshots", task.id, latest_snap)
    if os.path.exists(snap_path):
        snap_data = load_snapshot(snap_path)
        snapshot_values = snap_data.values

# Load workspace for recalculation
ws = load_workspace(task.id)

# ── Extract data from graph ─────────────────────────────────────────────────

kg_params = extract_params_with_cell_ids(graph, snapshot_values)
kg_metrics = extract_financial_metrics(graph, snapshot_values)
kg_yearly = extract_yearly_data(graph, snapshot_values)

# Show deltas from recalculation result
result_key = f"fin_result_{task.id}"
result_data = st.session_state.get(result_key)
display_metrics = result_data["kg_metrics"] if result_data else kg_metrics
display_yearly = result_data["kg_yearly"] if result_data else kg_yearly
deltas = result_data.get("deltas", {}) if result_data else {}

# ── Scenario management (snapshots as scenarios) ────────────────────────────

snap_scenarios = db.list_snapshots(task.id)
active_snap_idx = st.session_state.get("fin_snap_idx", 0)
if active_snap_idx >= len(snap_scenarios):
    active_snap_idx = max(0, len(snap_scenarios) - 1)

scenario_cols = st.columns([len(s.name) * 2 + 3 for s in snap_scenarios] + [4] if snap_scenarios else [1])

if snap_scenarios:
    for i, s in enumerate(snap_scenarios):
        is_active = (i == active_snap_idx)
        label = f"{'●' if is_active else '○'} {s.name}"
        btn_type = "primary" if is_active else "secondary"
        if st.button(label, key=f"fin_sel_{s.id}", type=btn_type, use_container_width=True):
            st.session_state["fin_snap_idx"] = i
            active_snap_idx = i
            st.rerun()
else:
    st.info("该任务暂无快照场景。可在「参数工作台」创建快照。")

# ── Core KPI Dashboard (full-width) ─────────────────────────────────────────

st.subheader("核心指标看板")
kpi_cols = st.columns(5)

def _kpi(label: str, value: str, delta: str | None = None):
    st.metric(label, value, delta=delta)

with kpi_cols[0]:
    _kpi("IRR 税前", f"{display_metrics.get('irr_pre_tax', 0)*100:.2f}%",
         _format_delta(deltas.get("irr_pre_tax")))
    _kpi("IRR 税后", f"{display_metrics.get('irr_post_tax', 0)*100:.2f}%",
         _format_delta(deltas.get("irr_post_tax")))

with kpi_cols[1]:
    _kpi("资本金 IRR", f"{display_metrics.get('irr_equity', 0)*100:.2f}%",
         _format_delta(deltas.get("irr_equity")))
    if "npv_post_tax" in display_metrics:
        _kpi("NPV (税后)", f"{display_metrics['npv_post_tax']:,.0f} 万")
    else:
        st.metric("NPV (税后)", "—")

with kpi_cols[2]:
    if display_metrics.get("payback_post_tax", 0) > 0:
        _kpi("投资回收期", f"{display_metrics['payback_post_tax']:.2f} 年")
    _kpi("DSCR 均值", f"{display_metrics.get('avg_dscr', 0):.2f}",
         _format_delta(deltas.get("avg_dscr")))

with kpi_cols[3]:
    if "total_investment" in display_metrics:
        _kpi("总投资", f"{display_metrics['total_investment']:,.0f} 万")
    _kpi("DSCR 低值", f"{display_metrics.get('min_dscr', 0):.2f}")

with kpi_cols[4]:
    _kpi("累计现金流", f"{display_metrics.get('cumulative_cashflow', 0):,.0f} 万")
    avg_rev = sum(e.get("revenue", 0) for e in display_yearly if e.get("revenue", 0) > 0)
    n_rev = sum(1 for e in display_yearly if e.get("revenue", 0) > 0)
    _kpi("年均收入", f"{avg_rev / n_rev:,.0f} 万" if n_rev else "—")

# ── Two-column layout: Params (40%) + Tables (60%) ─────────────────────────

param_col, table_col = st.columns([2, 3])

with param_col:
    st.subheader("参数编辑")

    # Build parameter groups from extracted params
    param_groups: dict[str, list[tuple]] = {}
    group_keywords = {
        "投资参数": ["投资", "EPC", "征地", "监理", "不可预见", "流动资金", "静态投资", "动态投资"],
        "融资参数": ["贷款", "借款", "利率", "宽限期", "管理费", "承诺费", "还本", "付息"],
        "收入参数": ["电价", "售电", "收入", "电量", "负荷", "达产"],
        "成本参数": ["成本", "维修", "保险", "材料", "工资", "CPI", "购电", "损耗"],
        "税收/折旧": ["税", "折旧", "残值", "摊销"],
        "时间参数": ["开始", "竣工", "运营", "建设", "月份", "年"],
    }

    for name, pdata in kg_params.items():
        group = "其他参数"
        for gname, keywords in group_keywords.items():
            if any(kw in name for kw in keywords):
                group = gname
                break
        param_groups.setdefault(group, []).append((name, pdata))

    tab_names = list(param_groups.keys())
    tabs = st.tabs([f"{k} ({len(v)})" for k, v in param_groups.items()])

    edited_params: dict = {}

    for tab, group_name in zip(tabs, tab_names):
        with tab:
            fields = param_groups[group_name]
            rows = []
            for name, pdata in fields:
                val = pdata.get("value")
                unit = pdata.get("unit", "")
                rows.append({"参数": name, "值": val, "单位": unit})
            df = pd.DataFrame(rows[:50])

            edited_df = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                height=min(len(rows) * 35 + 40, 600),
                column_config={
                    "参数": st.column_config.TextColumn("参数", disabled=True),
                    "值": st.column_config.NumberColumn("值"),
                    "单位": st.column_config.TextColumn("单位", disabled=True),
                },
            )

            for _, row in edited_df.iterrows():
                edited_params[row["参数"]] = row["值"]

    if st.button("应用并计算", type="primary", use_container_width=True):
        # Map edited params to cell_ids — only changed values that exist in graph
        updates = {}
        for param_name, new_val in edited_params.items():
            pdata = kg_params.get(param_name, {})
            cell_id = pdata.get("cell_id")
            if cell_id and new_val is not None:
                original_val = pdata.get("value")
                # Only include if value actually changed
                if new_val != original_val:
                    updates[cell_id] = new_val

        # Filter out cell_ids that don't exist in the graph
        graph_cell_ids = set(graph.cell_graph.nodes()) if hasattr(graph, 'cell_graph') else set()
        if graph_cell_ids:
            missing = [cid for cid in updates if cid not in graph_cell_ids]
            for cid in missing:
                del updates[cid]

        if not updates:
            st.info("没有修改的参数，无需计算。")
        else:
            with st.spinner("正在计算..."):
                working_graph = copy.deepcopy(graph)
                ws.pending_edits = updates
                result = apply_and_recalc(working_graph, ws, graph)

                if result.error_cells:
                    st.error(f"计算错误: {len(result.error_cells)} 个单元格")

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                snap = create_snapshot(working_graph, task.id, f"分析场景_{timestamp}")
                snap_id = str(uuid.uuid4())[:8]
                db.save_snapshot(snap_id=snap_id, task_id=task.id, name=snap.name, filepath=snap.filepath)

                # Sync to workspace scenario so 03_recalc page sees this change
                scenario_name = f"财务分析_{timestamp}"
                if "财务效益分析" not in ws.scenarios:
                    ws.scenarios["财务效益分析"] = Scenario(
                        id=str(uuid.uuid4())[:8],
                        task_id=task.id,
                        name="财务效益分析",
                        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    )
                ws.scenarios["财务效益分析"].overrides.update(updates)
                ws.scenarios["财务效益分析"].recalc_result = {
                    "affected_count": result.affected_count,
                    "error_count": len(result.error_cells),
                    "snapshot": snap.name,
                }
                ws.active_scenario = "财务效益分析"
                save_workspace(ws)

                # Re-extract
                new_metrics = extract_financial_metrics(working_graph, snap.values)
                new_yearly = extract_yearly_data(working_graph, snap.values)

                st.session_state[result_key] = {
                    "kg_metrics": new_metrics,
                    "kg_yearly": new_yearly,
                    "deltas": {k: new_metrics[k] - kg_metrics[k] for k in kg_metrics if k in new_metrics},
                    "affected_count": result.affected_count,
                    "error_count": len(result.error_cells),
                }
            st.rerun()

with table_col:
    st.subheader("财务报表")

    for label, table_id in FINANCE_TABLE_IDS.items():
        df = extract_full_table(graph, table_id)
        if not df.empty:
            with st.expander(label, expanded=False):
                st.dataframe(df, use_container_width=True, height=400)

# ── Full-width chart tabs ───────────────────────────────────────────────────

if display_yearly:
    chart_tabs = st.tabs(["现金流预测", "DSCR趋势", "敏感性分析", "EBITDA分析", "利润趋势", "基础参数表"])

    start_year = 2023  # Default from graph data
    chart_data = _to_year_projections(display_yearly)

    with chart_tabs[0]:
        html = render_cashflow_chart(chart_data, start_year=start_year)
        components.html(html, height=500)

    with chart_tabs[1]:
        html = render_dscr_chart(chart_data, start_year=start_year)
        components.html(html, height=500)

    with chart_tabs[2]:
        # Auto-compute sensitivity from KG params
        fp = _build_params_from_kg(kg_params)
        sensitivity = run_sensitivity_analysis(fp)
        sens_matrix = sensitivity["matrix"]
        sens_rows = []
        for factor in ["revenue", "operating_cost", "investment"]:
            factor_data = sens_matrix.get(factor, {})
            for label in sorted(factor_data.keys()):
                d = factor_data[label]
                sens_rows.append({
                    "因子": {"revenue": "收入", "operating_cost": "经营成本", "investment": "投资"}.get(factor, factor),
                    "变动": label,
                    "IRR (税后)": f"{d['irr_post_tax']*100:.2f}%",
                    "NPV (税后)": f"{d['npv_post_tax']:,.2f}",
                    "回收期": f"{d['payback_post_tax']:.2f}",
                    "DSCR均值": f"{d['avg_dscr']:.2f}",
                    "DSCR低值": f"{d['min_dscr']:.2f}",
                })
        st.dataframe(pd.DataFrame(sens_rows), use_container_width=True, hide_index=True, height=200)
        factors = list(sens_matrix.keys())
        html = render_sensitivity_spider(factors, sens_matrix, display_metrics, metric_key="irr_post_tax")
        components.html(html, height=550)

    with chart_tabs[3]:
        html = render_ebitda_waterfall(chart_data, start_year=start_year)
        components.html(html, height=550)

    with chart_tabs[4]:
        html = render_profit_trend(chart_data, start_year=start_year)
        components.html(html, height=500)

    with chart_tabs[5]:
        if kg_params:
            param_rows = []
            for name, pdata in kg_params.items():
                param_rows.append({
                    "参数名称": name,
                    "数值": pdata.get("value", ""),
                    "单位": pdata.get("unit", ""),
                    "说明": pdata.get("description", "")[:80],
                })
            df = pd.DataFrame(param_rows)
            st.dataframe(df, use_container_width=True, hide_index=True, height=600)
        else:
            st.info("未提取到基础参数。")
else:
    st.info("选择任务后自动展示财务分析结果。")
