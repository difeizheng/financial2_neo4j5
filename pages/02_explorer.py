"""Page 2: Interactive graph explorer — hierarchical navigation."""
from __future__ import annotations
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.viz.graph_viz import (
    build_cell_subgraph,
    build_indicator_cell_graph,
    build_indicator_subgraph,
    build_table_graph,
)
from financial_kg.viz.echarts_graph import (
    build_cell_subgraph_data,
    build_indicator_cell_graph_data,
    build_indicator_subgraph_data,
    build_table_graph_data,
)
from financial_kg.viz.echarts_template import render_graph_html
import json

st.title("🔍 图谱浏览")

# ── Task selector ─────────────────────────────────────────────────────────────
db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务，请先在「上传解析」页面上传 Excel。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]


@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load(task.id, task.output_dir)
stats = graph.stats()

# ── Overview metrics ──────────────────────────────────────────────────────────
m_cols = st.columns(6)
m_cols[0].metric("Sheets", len(stats["sheets"]))
m_cols[1].metric("Tables", stats["total_tables"])
m_cols[2].metric("Indicators", stats["total_indicators"])
m_cols[3].metric("Cells", stats["total_cells"])
m_cols[4].metric("公式 Cells", stats["formula_cells"])
unlinked = stats.get("unlinked_cells", 0)
m_cols[5].metric("未关联 Table", f"{unlinked:,}", delta=f"{unlinked/stats['total_cells']*100:.1f}%" if stats["total_cells"] else "")

st.divider()

# ── Navigation state ──────────────────────────────────────────────────────────
_NAV_KEY = f"nav_{task.id}"
if _NAV_KEY not in st.session_state:
    st.session_state[_NAV_KEY] = {"sheet": None, "table": None, "indicator": None, "cell": None}

nav = st.session_state[_NAV_KEY]

# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.header("层级导航")
max_nodes = st.sidebar.slider("最大节点数", 50, 2000, 500, 50)

sheets = sorted(stats["sheets"])
sheet_opts = ["(选择 Sheet)"] + sheets
sheet_idx = (sheets.index(nav["sheet"]) + 1) if nav["sheet"] in sheets else 0
new_sheet_raw = st.sidebar.selectbox("Sheet", sheet_opts, index=sheet_idx)
new_sheet = None if new_sheet_raw == "(选择 Sheet)" else new_sheet_raw
if new_sheet != nav["sheet"]:
    nav.update({"sheet": new_sheet, "table": None, "indicator": None, "cell": None})
    st.rerun()

if nav["sheet"]:
    tables_in_sheet = [t for t in graph.tables.values() if t.sheet == nav["sheet"]]
    tbl_ids = [t.id for t in tables_in_sheet]
    tbl_names = [t.name[:30] for t in tables_in_sheet]
    tbl_opts = ["(选择 Table)"] + tbl_names
    tbl_idx = (tbl_ids.index(nav["table"]) + 1) if nav["table"] in tbl_ids else 0
    new_tbl_name = st.sidebar.selectbox("Table", tbl_opts, index=tbl_idx)
    new_tbl = None
    if new_tbl_name != "(选择 Table)":
        matched = [t for t in tables_in_sheet if t.name[:30] == new_tbl_name]
        new_tbl = matched[0].id if matched else None
    if new_tbl != nav["table"]:
        nav.update({"table": new_tbl, "indicator": None, "cell": None})
        st.rerun()

if nav["table"]:
    tbl_obj = graph.tables.get(nav["table"])
    inds_in_table = [graph.indicators[i] for i in (tbl_obj.indicator_ids if tbl_obj else []) if i in graph.indicators]
    ind_ids = [i.id for i in inds_in_table]
    ind_names = [i.name[:30] for i in inds_in_table]
    ind_opts = ["(选择 Indicator)"] + ind_names
    ind_idx = (ind_ids.index(nav["indicator"]) + 1) if nav["indicator"] in ind_ids else 0
    new_ind_name = st.sidebar.selectbox("Indicator", ind_opts, index=ind_idx)
    new_ind = None
    if new_ind_name != "(选择 Indicator)":
        matched = [i for i in inds_in_table if i.name[:30] == new_ind_name]
        new_ind = matched[0].id if matched else None
    if new_ind != nav["indicator"]:
        nav.update({"indicator": new_ind, "cell": None})
        st.rerun()

if nav["indicator"]:
    ind_obj = graph.indicators.get(nav["indicator"])
    cells_in_ind = [graph.cells[c] for c in (ind_obj.cell_ids if ind_obj else []) if c in graph.cells]
    cell_ids = [c.id for c in cells_in_ind]
    cell_opts = ["(选择 Cell)"] + cell_ids
    cell_idx = (cell_ids.index(nav["cell"]) + 1) if nav["cell"] in cell_ids else 0
    new_cell = st.sidebar.selectbox("Cell", cell_opts, index=cell_idx)
    if new_cell == "(选择 Cell)":
        new_cell = None
    if new_cell != nav["cell"]:
        nav["cell"] = new_cell
        st.rerun()

# ── Main area ─────────────────────────────────────────────────────────────────

# ── Render engine toggle ──────────────────────────────────────────────────────
_ENGINE_KEY = f"viz_engine_{task.id}"
if _ENGINE_KEY not in st.session_state:
    st.session_state[_ENGINE_KEY] = "pyvis"

st.sidebar.header("渲染引擎")
st.session_state[_ENGINE_KEY] = st.sidebar.radio(
    "选择渲染引擎",
    ["pyvis", "echarts"],
    format_func=lambda x: "Pyvis (vis.js)" if x == "pyvis" else "ECharts (可切换布局)",
    index=0 if st.session_state[_ENGINE_KEY] == "pyvis" else 1,
    label_visibility="collapsed",
)

def _render_html(path: str, height: int = 640) -> None:
    with open(path, encoding="utf-8") as f:
        components.html(f.read(), height=height, scrolling=False)


def _render_echarts(data: dict, height: int = 640, layout: str = "force") -> None:
    html = render_graph_html(json.dumps(data, ensure_ascii=False, default=str), height=f"{height}px")
    components.html(html, height=height, scrolling=False)


def _render_graph(pyvis_builder, data_builder, *args, height: int = 640, layout: str = "force", **kwargs):
    engine = st.session_state[_ENGINE_KEY]
    if engine == "pyvis":
        _render_html(pyvis_builder(*args, **kwargs), height=height)
    else:
        _render_echarts(data_builder(*args, **kwargs), height=height, layout=layout)


# Cell level
if nav["cell"]:
    cell = graph.cells[nav["cell"]]
    st.subheader(f"Cell: {nav['cell']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("值", str(cell.value))
    c2.metric("上游依赖", len(cell.dependencies))
    c3.metric("下游被依赖", len(cell.dependents))
    st.write(f"**公式**: `{cell.formula_raw or '无'}`")
    depth = st.slider("展开深度", 1, 5, 2)
    if st.button("生成依赖子图"):
        with st.spinner("渲染中..."):
            _render_graph(
                build_cell_subgraph, build_cell_subgraph_data,
                graph, nav["cell"],
                depth=depth,
            )

# Indicator level
elif nav["indicator"]:
    ind = graph.indicators[nav["indicator"]]
    st.subheader(f"Indicator: {ind.name}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("分类", ind.category or "—")
    c2.metric("单位", ind.unit or "—")
    val_str = ind.display_value if ind.display_value is not None else str(ind.summary_value or "—")
    c3.metric("汇总值", val_str)
    c4.metric("时间序列点数", len(ind.time_series))
    if ind.formula_readable:
        st.write(f"**公式**: `{ind.formula_readable}`")
    if ind.description:
        st.caption(ind.description)

    ind_obj = graph.indicators.get(nav["indicator"])
    cells_in_ind = [graph.cells[c] for c in (ind_obj.cell_ids if ind_obj else []) if c in graph.cells]
    if cells_in_ind:
        st.subheader(f"Cell 列表（{len(cells_in_ind)} 个）")
        rows = [
            {
                "ID": c.id,
                "值": c.value,
                "公式": c.formula_raw or "",
                "上游依赖": len(c.dependencies),
                "下游被依赖": len(c.dependents),
            }
            for c in cells_in_ind
        ]
        st.dataframe(rows, use_container_width=True)

    if st.button("生成 Cell 关系图"):
        with st.spinner("渲染中..."):
            _render_graph(
                build_indicator_cell_graph, build_indicator_cell_graph_data,
                graph, nav["indicator"],
            )

# Table level
elif nav["table"]:
    tbl = graph.tables[nav["table"]]
    st.subheader(f"Table: {tbl.name}")
    c1, c2, c3 = st.columns(3)
    c1.metric("类型", tbl.table_type)
    row_range = f"{tbl.data_row_range[0]}–{tbl.data_row_range[-1]}" if tbl.data_row_range else "—"
    c2.metric("行范围", row_range)
    c3.metric("Indicator 数", len(tbl.indicator_ids))

    inds_in_table = [graph.indicators[i] for i in tbl.indicator_ids if i in graph.indicators]
    if inds_in_table:
        st.subheader(f"Indicator 列表（{len(inds_in_table)} 个）")
        rows = []
        for ind in inds_in_table:
            val_str = ind.display_value if ind.display_value is not None else (
                f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
                else str(ind.summary_value or "")
            )
            rows.append({
                "名称": ind.name,
                "分类": ind.category or "",
                "单位": ind.unit or "",
                "汇总值": val_str,
                "公式": ind.formula_readable or "",
                "时间序列点数": len(ind.time_series),
            })
        st.dataframe(rows, use_container_width=True)

    if st.button("生成指标关系图"):
        with st.spinner("渲染中..."):
            _render_graph(
                build_indicator_subgraph, build_indicator_subgraph_data,
                graph, nav["table"],
            )

# Sheet level
elif nav["sheet"]:
    st.subheader(f"Sheet: {nav['sheet']}")
    tables_in_sheet = [t for t in graph.tables.values() if t.sheet == nav["sheet"]]
    unlinked_by_sheet = graph.get_unlinked_cells()
    orphan_cells = len(unlinked_by_sheet.get(nav["sheet"], []))

    if tables_in_sheet:
        st.subheader(f"Table 列表（{len(tables_in_sheet)} 个）")
        rows = []
        for tbl in tables_in_sheet:
            header_rows = sorted(tbl.header_rows)
            if not header_rows:
                header_display = "—"
            elif len(header_rows) == 1:
                header_display = str(header_rows[0])
            else:
                header_display = f"{header_rows[0]}–{header_rows[-1]}"
            ts_cols = len(tbl.time_period_labels)
            rows.append({
                "名称": tbl.name,
                "类型": tbl.table_type,
                "行范围": f"{tbl.data_row_range[0]}–{tbl.data_row_range[1]}" if tbl.data_row_range else "—",
                "表头行": header_display,
                "时间序列列": ts_cols if ts_cols else "—",
                "Indicator": len(tbl.indicator_ids),
                "上游 Table": len(tbl.fed_by),
                "下游 Table": len(tbl.feeds_into),
            })
        st.dataframe(rows, use_container_width=True)

    if orphan_cells > 0:
        st.caption(f"未归属 Cell（无 Indicator）: {orphan_cells} 个")

    if st.button("生成表间关系图"):
        with st.spinner("渲染中..."):
            _render_graph(
                build_table_graph, build_table_graph_data,
                graph, nav["sheet"],
            )

# Overview (no selection)
else:
    st.subheader("全量 Indicator 列表")
    rows = []
    for ind in list(graph.indicators.values())[:5000]:
        val_str = ind.display_value if ind.display_value is not None else (
            f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
            else str(ind.summary_value or "")
        )
        rows.append({
            "ID": ind.id,
            "名称": ind.name,
            "分类": ind.category or "",
            "单位": ind.unit or "",
            "汇总值": val_str,
            "Sheet": ind.sheet,
            "时间序列点数": len(ind.time_series),
        })
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("该任务暂无 Indicator 数据。")

    # Orphan cells summary
    unlinked = graph.get_unlinked_cells()
    total_unlinked = graph.unlinked_cell_count()
    if total_unlinked > 0:
        st.divider()
        st.subheader(f"未关联 Table 的 Cell（共 {total_unlinked:,} 个）")
        orphan_rows = []
        for sheet, cell_ids in sorted(unlinked.items(), key=lambda x: -len(x[1])):
            orphan_rows.append({
                "Sheet": sheet,
                "数量": len(cell_ids),
                "占该 Sheet Cell 比例": f"{len(cell_ids) / sum(1 for c in graph.cells.values() if c.sheet == sheet) * 100:.1f}%",
            })
        st.dataframe(orphan_rows, use_container_width=True)

        if st.checkbox("展开查看孤儿 Cell ID"):
            for sheet, cell_ids in sorted(unlinked.items(), key=lambda x: -len(x[1])):
                st.caption(f"**{sheet}** ({len(cell_ids)} 个)")
                st.text(", ".join(cell_ids[:200]))
                if len(cell_ids) > 200:
                    st.caption(f"... 及其他 {len(cell_ids) - 200} 个")

