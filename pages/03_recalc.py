"""Page 3: Parameter modification and incremental recalculation."""
from __future__ import annotations
import os
import sys
import uuid

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.snapshot import create_snapshot

st.title("⚙️ 参数修改 & 增量重算")

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

st.info("修改参数单元格的值，系统将自动传播计算所有下游受影响单元格。")

# ── Parameter search ─────────────────────────────────────────────────────────
st.subheader("搜索参数单元格")
search_kw = st.text_input("关键词（Indicator 名称）", placeholder="如：建设期")

matching_inds = []
if search_kw:
    matching_inds = [
        ind for ind in graph.indicators.values()
        if search_kw in (ind.name or "")
    ]
    if matching_inds:
        st.write(f"找到 {len(matching_inds)} 个匹配 Indicator：")
        for ind in matching_inds[:20]:
            st.write(f"- `{ind.id}` — {ind.name}  值={ind.summary_value}  单位={ind.unit or ''}")
    else:
        st.write("未找到匹配项")

# ── Manual cell edit ─────────────────────────────────────────────────────────
st.subheader("修改单元格值")

# Build fuzzy lookup: cell_id → (indicator_name, table_name, sheet, row, col)
_cell_lookup: dict[str, dict] = {}
for cid, cell in graph.cells.items():
    ind_name = ""
    if cell.indicator_id and cell.indicator_id in graph.indicators:
        ind_name = graph.indicators[cell.indicator_id].name
    tbl_name = ""
    if cell.table_id and cell.table_id in graph.tables:
        tbl_name = graph.tables[cell.table_id].name
    _cell_lookup[cid] = {
        "ind_name": ind_name,
        "tbl_name": tbl_name,
        "sheet": cell.sheet,
        "row": cell.row,
        "col": cell.col,
        "value": cell.value,
    }

def _fuzzy_match_cells(kw: str) -> list[tuple[str, dict]]:
    """Return list of (cell_id, info) matching keyword across id, indicator, table, sheet."""
    kw_lower = kw.lower()
    results = []
    for cid, info in _cell_lookup.items():
        # Match Cell ID, Indicator name, Table name, Sheet name
        if (kw_lower in cid.lower()
                or kw_lower in info["ind_name"].lower()
                or kw_lower in info["tbl_name"].lower()
                or kw_lower in info["sheet"].lower()):
            results.append((cid, info))
    return results

MAX_DISPLAY = 200

cell_search = st.text_input(
    "Cell ID / Indicator 名称 / Table 名称（支持模糊查询）",
    placeholder="如：建设期 / 参数输入表_4 / 资金筹措",
    key="cell_search",
)

cell_id = ""
if cell_search:
    matched = _fuzzy_match_cells(cell_search)
    if matched:
        st.caption(f"匹配 {len(matched)} 个 Cell，显示前 {min(len(matched), MAX_DISPLAY)} 个")
        opts = {}
        for cid, info in matched[:MAX_DISPLAY]:
            label = f"{cid}"
            parts = []
            if info["ind_name"]:
                parts.append(info["ind_name"])
            if info["tbl_name"]:
                parts.append(f"[{info['tbl_name'][:20]}]")
            parts.append(f"值={info['value']}")
            if parts:
                label += " — " + " ".join(parts)
            opts[label] = cid
        selected_label = st.selectbox("选择 Cell", list(opts.keys()))
        cell_id = opts[selected_label]
    else:
        st.write("未找到匹配项")
else:
    cell_id = st.text_input("或直接输入 Cell ID", placeholder="参数输入表_4_I", key="cell_id_direct")

with st.form("recalc_form"):
    _cid = st.text_input("选中的 Cell ID（可手动覆盖）", value=cell_id or "", key="final_cell_id")
    new_value_str = st.text_input("新值", placeholder="5")
    snap_before_name = st.text_input("保存「修改前」快照名称（留空跳过）", value="before")
    snap_after_name = st.text_input("保存「修改后」快照名称（留空跳过）", value="after")
    submitted = st.form_submit_button("执行重算", type="primary")

if submitted and _cid:
    cell = graph.cells.get(_cid)
    if cell is None:
        st.error(f"Cell {_cid!r} 不存在")
    else:
        # Parse new value
        try:
            new_val = float(new_value_str) if "." in new_value_str else int(new_value_str)
        except ValueError:
            new_val = new_value_str

        # Snapshot before
        if snap_before_name.strip():
            snap_b = create_snapshot(graph, task.id, snap_before_name.strip())
            db.save_snapshot(str(uuid.uuid4())[:8], task.id, snap_before_name.strip(), snap_b.filepath)
            st.write(f"快照「{snap_before_name}」已保存：`{snap_b.filepath}`")

        with st.spinner("重算中..."):
            result = recalculate(graph, {_cid: new_val})

        # Snapshot after
        if snap_after_name.strip():
            snap_a = create_snapshot(graph, task.id, snap_after_name.strip())
            db.save_snapshot(str(uuid.uuid4())[:8], task.id, snap_after_name.strip(), snap_a.filepath)
            st.write(f"快照「{snap_after_name}」已保存：`{snap_a.filepath}`")

        st.success(f"重算完成：{result.affected_count} 个单元格发生变化，{len(result.error_cells)} 个求值失败")

        if result.changed_cells:
            st.subheader("变化单元格（前 100 条）")
            rows = [
                {"Cell ID": c.cell_id, "旧值": c.old_value, "新值": c.new_value, "公式": c.formula or ""}
                for c in result.changed_cells[:100]
            ]
            st.dataframe(rows, use_container_width=True)

        if result.error_cells:
            with st.expander(f"求值失败的单元格 ({len(result.error_cells)})"):
                st.write(result.error_cells[:50])
