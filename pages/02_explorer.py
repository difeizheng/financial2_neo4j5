"""Page 2: Interactive graph explorer — hierarchical navigation with table views,
time series charts, and enhanced search."""
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
    build_indicator_graph_data,
)
from financial_kg.viz.echarts_template import render_graph_html
from financial_kg.viz.qa_chart import render_time_series_html
import json

st.set_page_config(layout="wide")
st.title("图谱浏览")

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

# ── Navigation state ──────────────────────────────────────────────────────────
_NAV_KEY = f"nav_{task.id}"
if _NAV_KEY not in st.session_state:
    st.session_state[_NAV_KEY] = {"sheet": None, "table": None, "indicator": None, "cell": None}

nav = st.session_state[_NAV_KEY]

# ── Render engine ─────────────────────────────────────────────────────────────
_ENGINE_KEY = f"viz_engine_{task.id}"
if _ENGINE_KEY not in st.session_state:
    st.session_state[_ENGINE_KEY] = "echarts"

# ── Top bar: task + engine + max_nodes ────────────────────────────────────────
top_bar = st.columns([3, 2, 2])

with top_bar[1]:
    engine = st.radio(
        "渲染引擎",
        ["pyvis", "echarts"],
        format_func=lambda x: "Pyvis" if x == "pyvis" else "ECharts",
        horizontal=True,
        index=0 if st.session_state[_ENGINE_KEY] == "pyvis" else 1,
        label_visibility="collapsed",
    )
    st.session_state[_ENGINE_KEY] = engine

with top_bar[2]:
    max_nodes = st.slider("最大节点", 50, 3000, 500, 100)


def _render_html(path: str, height: int = 640) -> None:
    with open(path, encoding="utf-8") as f:
        components.html(f.read(), height=height, scrolling=False)


def _render_echarts(data: dict, height: int = 640, layout: str = "force") -> None:
    html = render_graph_html(json.dumps(data, ensure_ascii=False, default=str), height=f"{height}px", default_layout=layout)
    components.html(html, height=height, scrolling=False)


def _render_graph(pyvis_builder, data_builder, *args, height: int = 640, layout: str = "force", **kwargs):
    eng = st.session_state[_ENGINE_KEY]
    if eng == "pyvis":
        _render_html(pyvis_builder(*args, **kwargs), height=height)
    else:
        _render_echarts(data_builder(*args, **kwargs), height=height, layout=layout)


def _navigate_to(level: str, value):
    """Navigate to a specific level and rerun."""
    if level == "sheet":
        nav.update({"sheet": value, "table": None, "indicator": None, "cell": None})
    elif level == "table":
        nav.update({"table": value, "indicator": None, "cell": None})
    elif level == "indicator":
        nav.update({"indicator": value, "cell": None})
    elif level == "cell":
        nav["cell"] = value
    st.rerun()


def _highlight(text: str, query: str) -> str:
    """Wrap query matches in **bold** markdown."""
    if not query or not text:
        return text
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text
    return text[:idx] + "**" + text[idx:idx + len(query)] + "**" + text[idx + len(query):]


# ── Sidebar: Search + Sheet selector ──────────────────────────────────────────
st.sidebar.header("搜索")
search_query = st.sidebar.text_input("关键词", placeholder="指标名 / Cell ID / 表名", label_visibility="collapsed")

search_types = st.sidebar.multiselect(
    "类型",
    ["Sheet", "Table", "Indicator", "Cell"],
    default=["Indicator", "Table", "Cell"],
)
search_sheets = ["(全部)"] + sorted(stats["sheets"])
search_sheet_filter = st.sidebar.selectbox("Sheet", search_sheets)

if search_query:
    q = search_query.lower()
    matched = []

    if "Sheet" in search_types:
        for s in stats["sheets"]:
            if (not search_sheet_filter or search_sheet_filter == "(全部)" or s == search_sheet_filter) and q in s.lower():
                matched.append(("Sheet", s, s))

    if "Table" in search_types:
        for tbl in graph.tables.values():
            if search_sheet_filter != "(全部)" and tbl.sheet != search_sheet_filter:
                continue
            if q in (tbl.name or "").lower() or q in tbl.id.lower():
                matched.append(("Table", tbl.name, tbl.id))

    if "Indicator" in search_types:
        for ind in graph.indicators.values():
            if search_sheet_filter != "(全部)" and ind.sheet != search_sheet_filter:
                continue
            if q in (ind.name or "").lower() or q in ind.id.lower():
                matched.append(("Indicator", ind.name, ind.id))

    if "Cell" in search_types:
        for c in graph.cells.values():
            if search_sheet_filter != "(全部)" and c.sheet != search_sheet_filter:
                continue
            if q in c.id.lower():
                matched.append(("Cell", c.id, c.id))

    st.sidebar.caption(f"结果 ({min(len(matched), 100)})")
    for i, (typ, name, nid) in enumerate(matched[:100]):
        hl = _highlight(name or nid, search_query)
        label = f"[{typ}] {hl}"
        if st.sidebar.button(label, key=f"srch_{typ}_{nid}", use_container_width=True):
            if typ == "Sheet":
                _navigate_to("sheet", nid)
            elif typ == "Table":
                tbl = graph.tables.get(nid)
                if tbl:
                    _navigate_to("sheet", tbl.sheet)
                    # Need to navigate to table after sheet
                    st.session_state[_NAV_KEY]["table"] = nid
                    st.rerun()
            elif typ == "Indicator":
                ind = graph.indicators.get(nid)
                if ind:
                    _navigate_to("sheet", ind.sheet)
                    st.session_state[_NAV_KEY]["table"] = ind.table_id if ind.table_id else None
                    st.session_state[_NAV_KEY]["indicator"] = nid
                    st.rerun()
            elif typ == "Cell":
                cell = graph.cells.get(nid)
                if cell and cell.indicator_id:
                    ind = graph.indicators.get(cell.indicator_id)
                    if ind:
                        _navigate_to("sheet", ind.sheet)
                        st.session_state[_NAV_KEY]["table"] = ind.table_id if ind.table_id else None
                        st.session_state[_NAV_KEY]["indicator"] = cell.indicator_id
                        st.session_state[_NAV_KEY]["cell"] = nid
                        st.rerun()

    if not matched:
        st.sidebar.info("无匹配结果")

st.sidebar.divider()
st.sidebar.header("层级导航")

# Sheet selector
sheets = sorted(stats["sheets"])
sheet_opts = ["(选择 Sheet)"] + sheets
sheet_idx = (sheets.index(nav["sheet"]) + 1) if nav["sheet"] in sheets else 0
new_sheet_raw = st.sidebar.selectbox("Sheet", sheet_opts, index=sheet_idx)
new_sheet = None if new_sheet_raw == "(选择 Sheet)" else new_sheet_raw
if new_sheet != nav["sheet"]:
    _navigate_to("sheet", new_sheet)

if nav["sheet"]:
    tables_in_sheet = [t for t in graph.tables.values() if t.sheet == nav["sheet"]]
    tbl_names_map = {t.name[:40]: t.id for t in tables_in_sheet}
    tbl_opts = ["(选择 Table)"] + list(tbl_names_map.keys())
    tbl_idx = (list(tbl_names_map.values()).index(nav["table"]) + 1) if nav["table"] in tbl_names_map.values() else 0
    new_tbl_name = st.sidebar.selectbox("Table", tbl_opts, index=tbl_idx)
    new_tbl = tbl_names_map.get(new_tbl_name) if new_tbl_name != "(选择 Table)" else None
    if new_tbl != nav["table"]:
        _navigate_to("table", new_tbl)

if nav["table"]:
    tbl_obj = graph.tables.get(nav["table"])
    inds_in_table = [graph.indicators[i] for i in (tbl_obj.indicator_ids if tbl_obj else []) if i in graph.indicators]
    ind_names_map = {i.name[:40]: i.id for i in inds_in_table}
    ind_opts = ["(选择 Indicator)"] + list(ind_names_map.keys())
    ind_idx = (list(ind_names_map.values()).index(nav["indicator"]) + 1) if nav["indicator"] in ind_names_map.values() else 0
    new_ind_name = st.sidebar.selectbox("Indicator", ind_opts, index=ind_idx)
    new_ind = ind_names_map.get(new_ind_name) if new_ind_name != "(选择 Indicator)" else None
    if new_ind != nav["indicator"]:
        _navigate_to("indicator", new_ind)

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
        _navigate_to("cell", new_cell)

# ── Breadcrumb (clickable to jump to level) ───────────────────────────────────
st.subheader("导航路径")
bc_parts = []
levels_data = [
    ("Sheet", nav["sheet"]),
    ("Table", nav["table"]),
    ("Indicator", nav["indicator"]),
    ("Cell", nav["cell"]),
]

# Find the deepest non-None level
deepest = 0
for i, (_, v) in enumerate(levels_data):
    if v is not None:
        deepest = i

bc_cols = st.columns(4)
for i, (level_name, level_val) in enumerate(levels_data):
    with bc_cols[i]:
        if i <= deepest:
            display = level_val if level_val else f"未选择 {level_name}"
            if level_val and i < deepest:
                if st.button(f"{level_name}: {display}", key=f"bc_{level_name.lower()}", use_container_width=True):
                    if i == 0:
                        nav.update({"sheet": None, "table": None, "indicator": None, "cell": None})
                    elif i == 1:
                        nav.update({"table": None, "indicator": None, "cell": None})
                    elif i == 2:
                        nav.update({"indicator": None, "cell": None})
                    st.rerun()
            elif i == deepest:
                st.markdown(f"**{level_name}: {display}**")
            else:
                st.caption(f"{level_name}: {display}")
        else:
            st.caption(f"未选择 {level_name}")

st.divider()

# ── Main area ─────────────────────────────────────────────────────────────────

# Cell level
if nav["cell"]:
    cell = graph.cells[nav["cell"]]
    st.subheader(f"Cell: {nav['cell']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("值", str(cell.value))
    c2.metric("上游依赖", len(cell.dependencies))
    c3.metric("下游被依赖", len(cell.dependents))
    st.write(f"**公式**: `{cell.formula_raw or '无'}`")

    # Upstream dependency table
    if cell.dependencies:
        st.subheader(f"上游依赖（{len(cell.dependencies)} 个）")
        dep_rows = []
        for dep_id in sorted(cell.dependencies):
            dep_cell = graph.cells.get(dep_id)
            dep_rows.append({
                "Cell ID": dep_id,
                "值": dep_cell.value if dep_cell else "—",
                "公式": (dep_cell.formula_raw or "")[:60] if dep_cell else "",
                "Sheet": dep_cell.sheet if dep_cell else "",
            })
        st.dataframe(dep_rows, use_container_width=True, hide_index=True, height=min(len(dep_rows) * 35 + 38, 300))

    # Downstream dependents table
    if cell.dependents:
        st.subheader(f"下游被依赖（{len(cell.dependents)} 个）")
        dpt_rows = []
        for dpt_id in sorted(cell.dependents):
            dpt_cell = graph.cells.get(dpt_id)
            dpt_rows.append({
                "Cell ID": dpt_id,
                "值": dpt_cell.value if dpt_cell else "—",
                "公式": (dpt_cell.formula_raw or "")[:60] if dpt_cell else "",
                "Sheet": dpt_cell.sheet if dpt_cell else "",
            })
        st.dataframe(dpt_rows, use_container_width=True, hide_index=True, height=min(len(dpt_rows) * 35 + 38, 300))

    depth = st.slider("展开深度", 1, 5, 2)
    _render_graph(
        build_cell_subgraph, build_cell_subgraph_data,
        graph, nav["cell"],
        depth=depth,
        layout="layered",
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

    # Time series chart
    if ind.time_series:
        st.subheader("时间序列")
        html = render_time_series_html(
            series_data=[{"name": ind.name, "values": ind.time_series}],
            title=f"{ind.name} 趋势",
            height="350px",
        )
        components.html(html, height=350)

    # Cell list with navigation
    ind_obj = graph.indicators.get(nav["indicator"])
    cells_in_ind = [graph.cells[c] for c in (ind_obj.cell_ids if ind_obj else []) if c in graph.cells]
    if cells_in_ind:
        st.subheader(f"Cell 列表（{len(cells_in_ind)} 个）")
        rows = []
        for c in cells_in_ind:
            rows.append({
                "ID": c.id,
                "值": c.value,
                "公式": c.formula_raw or "",
                "上游依赖": len(c.dependencies),
                "下游被依赖": len(c.dependents),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True, height=min(len(rows) * 35 + 38, 400))

        # Navigation buttons for cells
        nav_cols = st.columns(min(len(cells_in_ind), 5))
        for j, c in enumerate(cells_in_ind[:5]):
            with nav_cols[j % 5]:
                short = c.id.split("_", 1)[-1] if "_" in c.id else c.id
                if st.button(f"Cell: {short}", key=f"go_cell_{c.id}", use_container_width=True):
                    _navigate_to("cell", c.id)
        if len(cells_in_ind) > 5:
            st.caption(f"... 及其他 {len(cells_in_ind) - 5} 个 Cell")

    _render_graph(
        build_indicator_cell_graph, build_indicator_cell_graph_data,
        graph, nav["indicator"],
        layout="concentric",
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
            val = ind.display_value if ind.display_value is not None else (
                f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
                else str(ind.summary_value or "")
            )
            ts_len = len(ind.time_series)
            ts_tag = f" ({ts_len}年)" if ts_len > 0 else ""
            rows.append({
                "名称": ind.name,
                "分类": ind.category or "",
                "单位": ind.unit or "",
                "汇总值": val,
                "时间序列": ts_tag,
                "公式": ind.formula_readable or "",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True, height=min(len(rows) * 35 + 38, 500))

        # Navigation buttons for indicators
        nav_cols = st.columns(min(len(inds_in_table), 5))
        for j, ind in enumerate(inds_in_table[:5]):
            with nav_cols[j % 5]:
                short = ind.name[:20] if ind.name else ind.id[-20:]
                if st.button(f"{short}", key=f"go_ind_{ind.id}", use_container_width=True):
                    _navigate_to("indicator", ind.id)
        if len(inds_in_table) > 5:
            st.caption(f"... 及其他 {len(inds_in_table) - 5} 个 Indicator")

        # Indicator dependency edges
        dep_edges = []
        for ind in inds_in_table:
            for dep_id in ind.depends_on_indicators:
                dep_ind = graph.indicators.get(dep_id)
                dep_edges.append({
                    "来源": ind.name[:30],
                    "依赖": (dep_ind.name or dep_id)[:30] if dep_ind else dep_id[-30:],
                    "依赖ID": dep_id,
                })
        if dep_edges:
            with st.expander(f"Indicator 依赖关系（{len(dep_edges)} 条）"):
                st.dataframe(dep_edges, use_container_width=True, hide_index=True, height=min(len(dep_edges) * 35 + 38, 300))

    _render_graph(
        build_indicator_subgraph, build_indicator_subgraph_data,
        graph, nav["table"],
        layout="layered",
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
            header_display = "—"
            if header_rows:
                header_display = str(header_rows[0]) if len(header_rows) == 1 else f"{header_rows[0]}–{header_rows[-1]}"
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
        st.dataframe(rows, use_container_width=True, hide_index=True, height=min(len(rows) * 35 + 38, 400))

        # Navigation buttons for tables
        nav_cols = st.columns(min(len(tables_in_sheet), 5))
        for j, tbl in enumerate(tables_in_sheet[:5]):
            with nav_cols[j % 5]:
                short = tbl.name[:20] if tbl.name else tbl.id[-20:]
                if st.button(f"{short}", key=f"go_tbl_{tbl.id}", use_container_width=True):
                    _navigate_to("table", tbl.id)
        if len(tables_in_sheet) > 5:
            st.caption(f"... 及其他 {len(tables_in_sheet) - 5} 个 Table")

        # Table dependency edges
        tdeps = []
        for tbl in tables_in_sheet:
            for target_id in tbl.feeds_into:
                t = graph.tables.get(target_id)
                tdeps.append({
                    "来源": tbl.name[:30],
                    "流向": (t.name or target_id)[:30] if t else target_id[-30:],
                    "目标Sheet": t.sheet if t else "",
                })
        if tdeps:
            with st.expander(f"Table 依赖关系（{len(tdeps)} 条）"):
                st.dataframe(tdeps, use_container_width=True, hide_index=True, height=min(len(tdeps) * 35 + 38, 250))

    if orphan_cells > 0:
        st.caption(f"未归属 Cell（无 Indicator）: {orphan_cells} 个")

    _render_graph(
        build_table_graph, build_table_graph_data,
        graph, nav["sheet"],
        layout="concentric",
    )

# Overview (no selection) — full graph + enhanced stats
else:
    st.subheader("全量图谱概览")

    # Truncation warning
    total_indicators = stats["total_indicators"]
    if max_nodes < total_indicators:
        st.warning(f"最大节点数 {max_nodes} < 总指标数 {total_indicators}，将截断显示。")

    _render_echarts(
        build_indicator_graph_data(graph, max_nodes=max_nodes),
        height=800,
        layout="force",
    )

    st.divider()
    st.subheader("按 Sheet 统计")
    sheet_rows = []
    for sheet_name in sorted(stats["sheets"]):
        tbl_count = sum(1 for t in graph.tables.values() if t.sheet == sheet_name)
        ind_count = sum(1 for i in graph.indicators.values() if i.sheet == sheet_name)
        cell_count = sum(1 for c in graph.cells.values() if c.sheet == sheet_name)
        formula_count = sum(1 for c in graph.cells.values() if c.sheet == sheet_name and c.formula_raw)
        formula_pct = f"{formula_count / cell_count * 100:.0f}%" if cell_count > 0 else "—"
        sheet_rows.append({
            "Sheet": sheet_name,
            "Table": tbl_count,
            "Indicator": ind_count,
            "Cell": cell_count,
            "公式比例": formula_pct,
        })
    st.dataframe(sheet_rows, use_container_width=True, hide_index=True)
