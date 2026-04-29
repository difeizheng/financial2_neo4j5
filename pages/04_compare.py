"""Page 4: Snapshot comparison."""
from __future__ import annotations
import json
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.snapshot import load_snapshot, diff_snapshots
from financial_kg.viz.propagation_graph import build_propagation_data
from financial_kg.viz.echarts_template import render_propagation_html

st.set_page_config(page_title="快照对比", layout="wide")
st.title("📊 快照对比")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]

@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)

graph = _load(task.id, task.output_dir)

snaps = db.list_snapshots(task.id)
if len(snaps) < 2:
    st.info("该任务快照不足 2 个，请先在「参数重算」页面创建快照。")
    st.stop()

snap_options = {f"{s.name} ({s.created_at[:19]})": s for s in snaps}
col1, col2 = st.columns(2)
with col1:
    label_a = st.selectbox("快照 A（基准）", list(snap_options.keys()), index=len(snaps) - 1)
with col2:
    label_b = st.selectbox("快照 B（对比）", list(snap_options.keys()), index=0)

if st.button("执行对比", type="primary"):
    rec_a = snap_options[label_a]
    rec_b = snap_options[label_b]

    if rec_a.id == rec_b.id:
        st.warning("请选择两个不同的快照")
        st.stop()

    with st.spinner("对比中..."):
        snap_a = load_snapshot(rec_a.filepath)
        snap_b = load_snapshot(rec_b.filepath)
        diff = diff_snapshots(snap_a, snap_b, graph)

    st.session_state["diff"] = diff
    st.session_state["diff_task_id"] = task.id

# ── Show diff results (persisted in session_state) ────────────────────────────
diff = st.session_state.get("diff")
if diff is None or st.session_state.get("diff_task_id") != task.id:
    st.stop()

st.subheader("汇总")
c1, c2, c3 = st.columns(3)
c1.metric("变化单元格数", diff.summary["total_changed_cells"])
c2.metric("受影响 Indicator 数", diff.summary["total_changed_indicators"])
c3.metric("涉及 Sheet 数", len(diff.summary["sheets_affected"]))

if diff.summary["sheets_affected"]:
    st.write("涉及 Sheet：", "、".join(diff.summary["sheets_affected"]))

if diff.affected_indicators:
    st.subheader("受影响 Indicator")

    # Sheet filter + name search
    col_sheet, col_name = st.columns(2)
    with col_sheet:
        all_sheets = sorted({i["sheet"] for i in diff.affected_indicators if i.get("sheet")})
        selected_sheets = st.multiselect("按 Sheet 筛选", all_sheets, default=[])
    with col_name:
        ind_search = st.text_input("搜索名称", placeholder="输入关键词...")

    filtered_indicators = diff.affected_indicators
    if selected_sheets:
        filtered_indicators = [i for i in filtered_indicators if i["sheet"] in selected_sheets]
    if ind_search:
        keyword = ind_search.lower()
        filtered_indicators = [i for i in filtered_indicators if keyword in i["name"].lower()]

    rows = [
        {
            "Indicator": i["name"],
            "Sheet": i["sheet"],
            "旧汇总值": i["old_summary"],
            "新汇总值": i["new_summary"],
            "变化单元格数": i["changed_cell_count"],
        }
        for i in filtered_indicators
    ]
    st.dataframe(rows, use_container_width=True)
    if ind_search or selected_sheets:
        st.caption(f"筛选结果：{len(rows)} / {len(diff.affected_indicators)} 个 Indicator")

if diff.changed_cells:
    with st.expander(f"变化单元格明细（共 {len(diff.changed_cells)} 条，显示前 200）"):
        rows = [
            {
                "Cell ID": c["id"],
                "Sheet": c["sheet"],
                "旧值": c["old"],
                "新值": c["new"],
                "公式": c["formula"] or "",
            }
            for c in diff.changed_cells[:200]
        ]
        st.dataframe(rows, use_container_width=True)

# ── Propagation Graph ─────────────────────────────────────────────────────────
if diff.changed_cells:
    st.subheader("变化传播图")

    # Search across ALL changed cells, display top 200 matches
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
        for c in candidates[:200]
    }
    if not cell_options:
        st.warning("无匹配的变化单元格，请调整搜索条件")
        st.stop()
    root_id = cell_options[st.selectbox("选择传播起点", list(cell_options.keys()))]
    if cell_search and len(candidates) > 200:
        st.caption(f"匹配 {len(candidates)} 个，显示前 200 个")

    col_d, col_s = st.columns(2)
    max_depth = col_d.slider("最大传播深度", 1, 15, 8)
    max_nodes = col_s.slider("最大节点数", 100, 2000, 500, 100)

    if st.button("生成传播图"):
        with st.spinner("构建传播图..."):
            data = build_propagation_data(graph, diff, root_id, max_depth, max_nodes)
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
