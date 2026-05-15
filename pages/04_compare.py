"""Page 4: Snapshot comparison — KPI dashboard, waterfall, multi-start propagation."""
from __future__ import annotations

import json
import os
import sys
import tempfile

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.snapshot import load_snapshot, diff_snapshots
from financial_kg.engine.workspace import get_key_metrics
from financial_kg.viz.propagation_graph import build_propagation_data, build_multi_propagation_data
from financial_kg.viz.echarts_template import render_propagation_html
from financial_kg.viz.compare_viz import (
    compute_change_summary,
    build_indicator_change_chart,
    render_indicator_chart_html,
    build_heatmap_data,
    render_heatmap_html,
    export_diff_report_excel,
)
from financial_kg.viz.compare_charts import (
    build_kpi_data,
    render_kpi_dual_bar_html,
    build_waterfall_data,
    render_waterfall_html,
    build_timeline_data,
)
from financial_kg.viz.qa_chart import render_time_series_html
from financial_kg.engine.excel_export import export_modified_excel, find_original_excel

st.set_page_config(layout="wide")
st.title("快照对比")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()), label_visibility="collapsed")
task = task_options[selected_label]


@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load(task.id, task.output_dir)
snaps = db.list_snapshots(task.id)

if "locked_baseline" not in st.session_state:
    st.session_state["locked_baseline"] = None
if "locked_baseline_task" not in st.session_state:
    st.session_state["locked_baseline_task"] = None

# Reset lock if task changed
if st.session_state["locked_baseline_task"] != task.id:
    st.session_state["locked_baseline"] = None
    st.session_state["locked_baseline_task"] = None

# ── Snapshot selector (compact single-row layout) ────────────────────────────

if len(snaps) < 2:
    st.info("该任务快照不足 2 个，请在「参数重算」页面创建快照。")
    st.stop()

snap_options = {f"{s.name} ({s.created_at[:19]})": s for s in snaps}
sorted_snaps = sorted(snaps, key=lambda s: s.created_at, reverse=True)
latest_label = f"{sorted_snaps[0].name} ({sorted_snaps[0].created_at[:19]})"
second_label = f"{sorted_snaps[1].name} ({sorted_snaps[1].created_at[:19]})" if len(sorted_snaps) >= 2 else latest_label

sel_row = st.columns([3, 1, 3, 1, 2, 2])

with sel_row[0]:
    if st.session_state["locked_baseline"]:
        label_a = st.selectbox("基准快照", [st.session_state["locked_baseline"]])
    else:
        default_idx = len(snaps) - 1
        label_a = st.selectbox("基准快照", list(snap_options.keys()), index=default_idx)

with sel_row[1]:
    st.write("")
    st.write("")
    if st.button("⇄", help="交换 A/B", use_container_width=True):
        st.session_state["_swap"] = True

with sel_row[2]:
    label_b = st.selectbox("对比快照", list(snap_options.keys()), index=0)

with sel_row[3]:
    lock_cb = st.checkbox("🔒 锁定", key="lock_cb", help="锁定基准快照")

if lock_cb and st.session_state["locked_baseline"] is None:
    st.session_state["locked_baseline"] = label_a
    st.session_state["locked_baseline_task"] = task.id
elif not lock_cb:
    st.session_state["locked_baseline"] = None
    st.session_state["locked_baseline_task"] = None

with sel_row[4]:
    if st.button("对比最新两个", use_container_width=True):
        st.session_state["_auto_a"] = latest_label
        st.session_state["_auto_b"] = second_label
        st.session_state["_run_auto_diff"] = True

with sel_row[5]:
    if st.button("执行对比 ▶", type="primary", use_container_width=True):
        rec_a = snap_options[label_a]
        rec_b = snap_options[label_b]
        if rec_a.id == rec_b.id:
            st.warning("请选择两个不同的快照")
        else:
            st.session_state["_manual_a"] = label_a
            st.session_state["_manual_b"] = label_b
            st.session_state["_run_manual_diff"] = True

# Handle swap
if st.session_state.pop("_swap", False):
    st.session_state["_swapped_a"] = label_b
    st.session_state["_swapped_b"] = label_a
    st.session_state["_run_swap_diff"] = True

# ── Execute diff ──────────────────────────────────────────────────────────────

def _do_diff(a_label: str, b_label: str):
    if a_label not in snap_options or b_label not in snap_options:
        return
    rec_a = snap_options[a_label]
    rec_b = snap_options[b_label]
    if rec_a.id == rec_b.id:
        st.warning("请选择两个不同的快照")
        return
    with st.spinner("对比中..."):
        snap_a = load_snapshot(rec_a.filepath)
        snap_b = load_snapshot(rec_b.filepath)
        diff = diff_snapshots(snap_a, snap_b, graph)
    st.session_state["diff"] = diff
    st.session_state["diff_task_id"] = task.id
    st.session_state["snap_b_values"] = snap_b.values
    st.session_state.pop("prop_html", None)


if st.session_state.pop("_run_auto_diff", False):
    _do_diff(st.session_state.pop("_auto_a", ""), st.session_state.pop("_auto_b", ""))

if st.session_state.pop("_run_manual_diff", False):
    _do_diff(st.session_state.pop("_manual_a", ""), st.session_state.pop("_manual_b", ""))

if st.session_state.pop("_run_swap_diff", False):
    _do_diff(st.session_state.pop("_swapped_a", ""), st.session_state.pop("_swapped_b", ""))

# ── Show diff results ─────────────────────────────────────────────────────────

diff = st.session_state.get("diff")
if diff is None or st.session_state.get("diff_task_id") != task.id:
    st.stop()

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_kpi, tab_impact, tab_cells, tab_heatmap, tab_prop, tab_export = st.tabs([
    "关键指标",
    "影响分析",
    "变化明细",
    "热力图",
    "传播图",
    "导出",
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: 关键指标
# ══════════════════════════════════════════════════════════════════════════════
with tab_kpi:
    # KPI cards
    kpi_data = build_kpi_data(diff, graph)

    if kpi_data:
        n_show = min(len(kpi_data), 9)
        for ri in range((n_show + 2) // 3):
            mc = st.columns(3)
            for j, kpi in enumerate(kpi_data[ri * 3:(ri + 1) * 3]):
                delta_str = None
                if kpi["delta"] is not None:
                    delta_str = f"{kpi['delta']:+,.2f}"
                    if kpi["delta_pct"] is not None:
                        delta_str += f" ({kpi['delta_pct']:+.1f}%)"

                va_display = "—"
                vb_display = "—"
                if kpi["value_a"] is not None:
                    try:
                        va_display = f"{float(kpi['value_a']):,.2f}"
                    except (ValueError, TypeError):
                        va_display = str(kpi["value_a"])
                if kpi["value_b"] is not None:
                    try:
                        vb_display = f"{float(kpi['value_b']):,.2f}"
                    except (ValueError, TypeError):
                        vb_display = str(kpi["value_b"])

                with mc[j]:
                    st.metric(
                        label=kpi["name"],
                        value=f"A: {va_display} → B: {vb_display}",
                        delta=delta_str,
                        delta_color="inverse" if kpi.get("delta") is not None and kpi["delta"] < 0 else "normal",
                    )

        if len(kpi_data) > 9:
            with st.expander(f"查看全部 {len(kpi_data)} 个关键指标"):
                for kpi in kpi_data[9:]:
                    delta_str = ""
                    if kpi["delta"] is not None:
                        delta_str = f"Δ {kpi['delta']:+,.2f}"
                        if kpi["delta_pct"] is not None:
                            delta_str += f" ({kpi['delta_pct']:+.1f}%)"
                    st.caption(f"{kpi['name']}: A={kpi['value_a']} → B={kpi['value_b']}  {delta_str}")

        # A/B dual-bar chart
        st.divider()
        chart_html = render_kpi_dual_bar_html(
            kpi_data,
            snap_a_name=diff.snapshot_a,
            snap_b_name=diff.snapshot_b,
        )
        components.html(chart_html, height=420, scrolling=False)

    else:
        st.info("未找到匹配的关键指标（NPV / IRR / 收入 / 成本等）")

    # Multi-snapshot timeline (≥3 snapshots)
    if len(snaps) >= 3:
        st.divider()
        st.subheader("多快照趋势")
        with st.spinner("加载多快照数据..."):
            timeline_data = build_timeline_data(snaps, graph)

        if timeline_data:
            html = render_time_series_html(
                series_data=timeline_data[:8],
                title="关键指标跨快照变化",
                height="380px",
            )
            components.html(html, height=380, scrolling=False)
            if len(timeline_data) > 8:
                st.caption(f"显示前 {min(8, len(timeline_data))} 个指标，共 {len(timeline_data)} 个")
        else:
            st.info("无法从历史快照中提取指标值（快照值结构可能不同）")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: 影响分析
# ══════════════════════════════════════════════════════════════════════════════
with tab_impact:
    # Summary metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("变化单元格数", diff.summary["total_changed_cells"])
    c2.metric("受影响 Indicator 数", diff.summary["total_changed_indicators"])
    c3.metric("涉及 Sheet 数", len(diff.summary["sheets_affected"]))

    if diff.summary["sheets_affected"]:
        st.write("涉及 Sheet：", "、".join(diff.summary["sheets_affected"]))

    # Change summary
    if diff.changed_cells:
        summary = compute_change_summary(diff, graph)

        st.subheader("变更摘要")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("总增加量", f"{summary['total_increase']:,.2f}")
        r2.metric("总减少量", f"{summary['total_decrease']:,.2f}")
        r3.metric("最大变化单元格", summary["max_magnitude_cell"][:30])
        r4.metric("最大变化幅度", f"{summary['max_magnitude']:,.2f}")

        # Waterfall chart
        st.subheader("变化贡献瀑布图")
        wf_data = build_waterfall_data(diff, graph)
        wf_html = render_waterfall_html(wf_data, title=f"净变化: {wf_data['net_change']:+,.2f}")
        components.html(wf_html, height=420, scrolling=False)

        # Sheet ranking
        if summary["sheets_ranking"]:
            st.subheader("Sheet 变更排行")
            sheet_rows = [{"Sheet": s, "变化数": c} for s, c in summary["sheets_ranking"]]
            st.bar_chart(sheet_rows, x="Sheet", y="变化数", horizontal=True)

        # Top indicators
        if summary["top_indicators"]:
            st.subheader("Top 10 受影响 Indicator")
            ind_rows = [{"Indicator": n, "变化单元格数": c} for n, c in summary["top_indicators"]]
            st.bar_chart(ind_rows, x="Indicator", y="变化单元格数", horizontal=True)

        # Indicator change chart
        if st.button("查看指标变化图表", key="show_ind_chart"):
            chart_data = build_indicator_change_chart(diff, graph)
            html = render_indicator_chart_html(chart_data)
            components.html(html, height=520, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: 变化明细
# ══════════════════════════════════════════════════════════════════════════════
with tab_cells:
    all_sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})

    st.caption(f"共 {len(diff.changed_cells)} 条变化")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        selected_sheets = st.multiselect("按 Sheet 筛选", all_sheets, default=[], key="cm_sheets")
    with col_f2:
        search_kw = st.text_input("搜索", placeholder="Cell ID / Sheet / Indicator / 值", key="cm_search")

    filtered = diff.changed_cells
    if selected_sheets:
        filtered = [c for c in filtered if c.get("sheet") in selected_sheets]
    if search_kw:
        kw = search_kw.lower()
        filtered = [
            c for c in filtered
            if kw in c["id"].lower()
            or kw in c.get("sheet", "").lower()
            or kw in c.get("indicator_name", "").lower()
            or kw in str(c.get("old", "")).lower()
            or kw in str(c.get("new", "")).lower()
        ]

    if not filtered:
        st.info("无匹配的变化单元格")
    else:
        rows = [
            {
                "Cell ID": c["id"],
                "Sheet": c.get("sheet", ""),
                "旧值": c.get("old"),
                "新值": c.get("new"),
                "变化量": c.get("change_magnitude", 0),
                "方向": "↑ 增加" if c.get("direction") == "increase" else "↓ 减少",
                "公式": c.get("formula") or "",
                "Indicator": c.get("indicator_name", ""),
            }
            for c in filtered
        ]
        st.dataframe(rows, use_container_width=True, height=500)
        if search_kw or selected_sheets:
            st.caption(f"筛选结果：{len(rows)} / {len(diff.changed_cells)} 条")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: 热力图
# ══════════════════════════════════════════════════════════════════════════════
with tab_heatmap:
    view_mode = st.radio("视图模式", ["指标聚合图", "单元格热力图"], horizontal=True)

    if view_mode == "指标聚合图":
        chart_data = build_indicator_change_chart(diff, graph)
        if chart_data["items"]:
            html = render_indicator_chart_html(chart_data)
            components.html(html, height=520, scrolling=False)
        else:
            st.info("无关联 indicator 的变化单元格")

    else:
        sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})
        selected_sheet = st.selectbox("选择 Sheet", sheets)
        if selected_sheet:
            hdata = build_heatmap_data(graph, diff, sheet_name=selected_sheet)
            html = render_heatmap_html(hdata, sheet_name=selected_sheet)
            components.html(html, height=620, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 5: 传播图 (enhanced: multi-start)
# ══════════════════════════════════════════════════════════════════════════════
with tab_prop:
    # Mode selector
    prop_mode = st.radio(
        "传播模式",
        ["单起点", "全选变更", "按 Sheet 筛选"],
        horizontal=True,
    )

    if prop_mode == "单起点":
        cell_search = st.text_input("搜索传播起点", placeholder="输入 Cell ID、Sheet 名或值...")
        if cell_search:
            kw = cell_search.lower()
            candidates = [
                c for c in diff.changed_cells
                if kw in c["id"].lower()
                or kw in c.get("sheet", "").lower()
                or kw in str(c.get("old", "")).lower()
                or kw in str(c.get("new", "")).lower()
            ]
        else:
            candidates = diff.changed_cells

        cell_options = {
            f"{c['id']}  ({c['sheet']})  {c['old']} → {c['new']}": c["id"]
            for c in candidates[:500]
        }
        if cell_options:
            root_label = st.selectbox("选择传播起点", list(cell_options.keys()))
            root_ids = [cell_options[root_label]]
        else:
            root_ids = []
            st.warning("无匹配的变化单元格")

    elif prop_mode == "全选变更":
        root_ids = [c["id"] for c in diff.changed_cells[:2000]]
        st.success(f"全选 {len(root_ids)} 个变更单元格作为传播起点")

    else:  # 按 Sheet 筛选
        sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})
        selected_sheets = st.multiselect("选择 Sheet", sheets)
        if selected_sheets:
            root_ids = [c["id"] for c in diff.changed_cells if c.get("sheet") in selected_sheets][:1000]
            st.success(f"{len(root_ids)} 个变更单元格作为传播起点")
        else:
            root_ids = []

    if not root_ids:
        st.info("请选择传播起点")
        st.stop()

    col_d, col_s = st.columns(2)
    max_depth = col_d.slider("最大传播深度", 1, 15, 8)
    max_nodes = col_s.slider("最大节点数", 100, 2000, 500, 100)

    if st.button("生成传播图"):
        with st.spinner("构建传播图..."):
            if len(root_ids) == 1:
                data = build_propagation_data(graph, diff, root_ids[0], max_depth, max_nodes)
            else:
                data = build_multi_propagation_data(graph, diff, root_ids, max_depth, max_nodes)
            html = render_propagation_html(
                json.dumps(data, ensure_ascii=False, default=str)
            )
        st.session_state["prop_html"] = html
        st.session_state["prop_truncated"] = data["stats"]["truncated"]
        st.session_state["prop_nodes"] = data["stats"]["total_nodes"]

    if "prop_html" in st.session_state:
        if st.session_state.get("prop_truncated"):
            st.warning(f"图谱已截断至 {st.session_state['prop_nodes']} 个节点（下游更多）")
        components.html(st.session_state["prop_html"], height=780, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 6: 导出
# ══════════════════════════════════════════════════════════════════════════════
with tab_export:
    original_path = find_original_excel(task.id, task.output_dir)
    has_original = original_path is not None

    if has_original:
        st.success(f"已找到原始文件：{original_path}")
    else:
        st.warning("未自动找到原始 Excel，请手动上传")

    uploaded_original = None
    if not has_original:
        uploaded_original = st.file_uploader("上传原始 Excel 文件", type=["xlsx"], key="compare_original")

    can_export = has_original or uploaded_original

    formula_ids = {cid for cid, cell in graph.cells.items() if cell.formula_raw}

    st.subheader("导出修改后 Excel")

    col_export1, col_export2 = st.columns(2)

    with col_export1:
        if st.button("导出全量值", type="primary", use_container_width=True,
                     disabled=not can_export, help="所有单元格覆盖为快照值，公式变为常数"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_orig:
                if uploaded_original:
                    uploaded_original.seek(0)
                    tmp_orig.write(uploaded_original.read())
                else:
                    with open(original_path, "rb") as f:
                        tmp_orig.write(f.read())
                tmp_orig_path = tmp_orig.name

            snap_b_values = st.session_state.get("snap_b_values", {})
            out_path = os.path.join(task.output_dir, f"{task.id}_modified.xlsx")

            with st.spinner("导出中..."):
                export_modified_excel(tmp_orig_path, snap_b_values, out_path)
                os.unlink(tmp_orig_path)

            with open(out_path, "rb") as f:
                st.download_button(
                    "下载 全量值 Excel",
                    data=f,
                    file_name=f"{task.filename.rsplit('.', 1)[0]}_modified.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_all_values",
                )

    with col_export2:
        if st.button("保留公式导出", type="primary", use_container_width=True,
                     disabled=not can_export, help="公式单元格保留原公式，只覆盖常量值"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_orig:
                if uploaded_original:
                    uploaded_original.seek(0)
                    tmp_orig.write(uploaded_original.read())
                else:
                    with open(original_path, "rb") as f:
                        tmp_orig.write(f.read())
                tmp_orig_path = tmp_orig.name

            snap_b_values = st.session_state.get("snap_b_values", {})
            out_path = os.path.join(task.output_dir, f"{task.id}_modified_with_formulas.xlsx")

            with st.spinner("导出中..."):
                export_modified_excel(
                    tmp_orig_path, snap_b_values, out_path,
                    formula_cell_ids=formula_ids,
                )
                os.unlink(tmp_orig_path)

            with open(out_path, "rb") as f:
                st.download_button(
                    "下载 保留公式 Excel",
                    data=f,
                    file_name=f"{task.filename.rsplit('.', 1)[0]}_with_formulas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_with_formulas",
                )

    st.caption(
        f"公式单元格: {len(formula_ids):,} 个，"
        f"常量单元格: {len(graph.cells) - len(formula_ids):,} 个。"
        f"保留公式导出只覆盖 {len(graph.cells) - len(formula_ids):,} 个常量单元格。"
    )

    st.divider()
    st.subheader("导出差异报告")
    st.caption("生成 Excel 格式的差异报告，含汇总、变化明细、受影响 Indicator。")

    if st.button("导出差异报告", type="secondary"):
        report_path = os.path.join(task.output_dir, f"{task.id}_diff_report.xlsx")
        with st.spinner("生成中..."):
            export_diff_report_excel(diff, graph, report_path)

        with open(report_path, "rb") as f:
            st.download_button(
                "下载差异报告",
                data=f,
                file_name=f"{task.filename.rsplit('.', 1)[0]}_diff_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
