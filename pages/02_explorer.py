"""Page 2: Interactive graph explorer."""
from __future__ import annotations
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.viz.graph_viz import build_indicator_graph, build_cell_subgraph

st.set_page_config(page_title="图谱浏览", layout="wide")
st.title("🔍 图谱浏览")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务，请先在「上传解析」页面上传 Excel。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]

# Load graph (cache by task_id)
@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)

graph = _load(task.id, task.output_dir)

# ── Sidebar filters ──────────────────────────────────────────────────────────
st.sidebar.header("筛选")
sheets = sorted({c.sheet for c in graph.cells.values()})
sheet_filter = st.sidebar.selectbox("Sheet", ["全部"] + sheets)
view_mode = st.sidebar.radio("视图", ["Indicator/Table 层", "Cell 依赖子图"])
max_nodes = st.sidebar.slider("最大节点数", 50, 500, 200, 50)

sheet_arg = None if sheet_filter == "全部" else sheet_filter

# ── Main view ────────────────────────────────────────────────────────────────
if view_mode == "Indicator/Table 层":
    if st.button("生成图谱"):
        with st.spinner("渲染中..."):
            html_path = build_indicator_graph(graph, sheet_filter=sheet_arg, max_nodes=max_nodes)
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        components.html(html, height=720, scrolling=False)

    # Indicator table
    st.subheader("Indicator 列表")
    inds = [i for i in graph.indicators.values() if not sheet_arg or i.sheet == sheet_arg]
    rows = []
    for ind in inds[:500]:
        rows.append({
            "ID": ind.id,
            "名称": ind.name,
            "分类": ind.category or "",
            "单位": ind.unit or "",
            "汇总值": ind.summary_value,
            "Sheet": ind.sheet,
            "时间序列点数": len(ind.time_series),
        })
    if rows:
        st.dataframe(rows, use_container_width=True)

else:
    search_query = st.text_input("搜索 Cell（Sheet 名、行号、列号，支持模糊）", placeholder="如：参数输入表_4 或 _I")

    matched_ids: list[str] = []
    if search_query.strip():
        q = search_query.strip().lower()
        matched_ids = [
            cid for cid in graph.cells
            if q in cid.lower()
        ][:200]

    cell_id = ""
    if matched_ids:
        cell_id = st.selectbox(f"匹配到 {len(matched_ids)} 个 Cell（显示前 200）", matched_ids)
    elif search_query.strip():
        st.warning("未找到匹配的 Cell ID")

    depth = st.slider("展开深度", 1, 5, 2)
    if cell_id and st.button("生成依赖子图"):
        with st.spinner("渲染中..."):
            html_path = build_cell_subgraph(graph, cell_id, depth=depth)
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        components.html(html, height=620, scrolling=False)

        cell = graph.cells[cell_id]
        st.write(f"**值**: {cell.value}  |  **公式**: `{cell.formula_raw or '无'}`")
        st.write(f"**依赖**: {len(cell.dependencies)} 个上游  |  **被依赖**: {len(cell.dependents)} 个下游")
