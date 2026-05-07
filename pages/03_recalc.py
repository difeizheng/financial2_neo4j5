"""Page 3: Parameter workspace — batch editing, scenarios, impact viz, history."""
from __future__ import annotations
import copy
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.snapshot import create_snapshot, SnapshotDiff
from financial_kg.engine.workspace import (
    WorkspaceState,
    load_workspace,
    save_workspace,
    Scenario,
    apply_and_recalc,
    rollback_record,
    get_key_metrics,
)
from financial_kg.viz.propagation_graph import build_propagation_data
from financial_kg.viz.echarts_template import render_propagation_html

st.set_page_config(layout="wide")
st.title("⚙️ 参数工作台")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]

# ── Load base graph (cached, read-only) ──────────────────────────────────────

@st.cache_resource(show_spinner="加载图谱...")
def _load_base(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)

base_graph = _load_base(task.id, task.output_dir)

# ── Load workspace state ─────────────────────────────────────────────────────

def _ws_key(tid: str) -> str:
    return f"ws_{tid}"

ws: WorkspaceState = load_workspace(task.id)
st.session_state[_ws_key(task.id)] = ws

# ── Section A: Scenario tabs ─────────────────────────────────────────────────

st.subheader("场景管理")

scenario_cols = st.columns([3, 1, 1])

with scenario_cols[0]:
    scenario_names = list(ws.scenarios.keys())
    idx = scenario_names.index(ws.active_scenario) if ws.active_scenario in scenario_names else 0
    ws.active_scenario = st.selectbox(
        "当前场景",
        scenario_names,
        index=idx,
        label_visibility="collapsed",
    )

with scenario_cols[1]:
    new_name = st.text_input("新场景名称", placeholder="如：乐观方案")

with scenario_cols[2]:
    if st.button("+ 新建场景", use_container_width=True):
        if new_name.strip() and new_name not in ws.scenarios:
            ws.scenarios[new_name.strip()] = Scenario(
                id=str(uuid.uuid4())[:8],
                task_id=task.id,
                name=new_name.strip(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            ws.active_scenario = new_name.strip()
            save_workspace(ws)
            st.rerun()
        else:
            st.warning("场景名称已存在")

# Scenario tabs as horizontal buttons
st.caption("快速切换场景：")
tab_cols = st.columns(min(len(scenario_names) + 1, 6))
for i, sname in enumerate(scenario_names):
    with tab_cols[i]:
        if st.button(
            sname,
            type="primary" if sname == ws.active_scenario else "secondary",
            use_container_width=True,
            key=f"tab_{sname}",
        ):
            ws.active_scenario = sname
            save_workspace(ws)
            st.rerun()

with tab_cols[min(len(scenario_names), 5)]:
    if st.button("删除场景", use_container_width=True, key="delete_scenario_btn"):
        if len(ws.scenarios) > 1 and ws.active_scenario != "基准":
            del ws.scenarios[ws.active_scenario]
            ws.active_scenario = "基准"
            save_workspace(ws)
            st.rerun()
        elif ws.active_scenario == "基准":
            st.warning("不能删除基准场景")

st.divider()

# ── Section B: Batch edit table ──────────────────────────────────────────────

st.subheader("批量编辑参数")

# Build parameter cell lookup
def _build_param_cells(graph):
    """Return list of dict for all parameter (non-formula) cells."""
    rows = []
    for cid, cell in graph.cells.items():
        if cell.formula_raw:  # skip formula cells
            continue
        ind_name = ""
        if cell.indicator_id and cell.indicator_id in graph.indicators:
            ind_name = graph.indicators[cell.indicator_id].name
        tbl_name = ""
        if cell.table_id and cell.table_id in graph.tables:
            tbl_name = graph.tables[cell.table_id].name
        rows.append({
            "Cell ID": cid,
            "Indicator 名称": ind_name,
            "Table 名称": tbl_name,
            "Sheet": cell.sheet or "",
            "当前值": cell.value,
        })
    return rows

@st.cache_data(show_spinner="构建参数列表...")
def _cached_param_cells(task_id: str, output_dir: str):
    g = load_graph(os.path.join(output_dir, f"{task_id}_cells.json"))
    return _build_param_cells(g)

all_param_cells = _cached_param_cells(task.id, task.output_dir)

# Filter controls
filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 1])

with filter_col1:
    param_search = st.text_input("搜索（Cell ID / Indicator / Table）", placeholder="输入关键词...")

with filter_col2:
    all_sheets = sorted(set(r["Sheet"] for r in all_param_cells if r["Sheet"]))
    selected_sheets = st.multiselect("按 Sheet 筛选", all_sheets, default=[])

with filter_col3:
    show_all = st.checkbox("展开全部参数", value=False)

# Apply filters
filtered = all_param_cells
if selected_sheets:
    filtered = [r for r in filtered if r["Sheet"] in selected_sheets]
if param_search:
    kw = param_search.lower()
    filtered = [
        r for r in filtered
        if kw in r["Cell ID"].lower()
        or kw in r["Indicator 名称"].lower()
        or kw in r["Table 名称"].lower()
    ]

# If not showing all, default to cells already modified in active scenario
scenario = ws.scenarios.get(ws.active_scenario)
scenario_override_ids = set(scenario.overrides.keys()) if scenario else set()

if not show_all and not param_search and not selected_sheets:
    # Show only modified cells + a way to expand
    modified_rows = [r for r in filtered if r["Cell ID"] in scenario_override_ids]
    if modified_rows:
        filtered = modified_rows
        st.caption(f"显示已修改的 {len(filtered)} 个参数（勾选「展开全部参数」查看全部）")
    else:
        st.caption("当前场景暂无修改。勾选「展开全部参数」浏览所有参数单元格。")
        filtered = []

MAX_EDITOR_ROWS = 500
if len(filtered) > MAX_EDITOR_ROWS:
    st.caption(f"共 {len(filtered)} 条，显示前 {MAX_EDITOR_ROWS} 条")
    filtered = filtered[:MAX_EDITOR_ROWS]

if filtered:
    import pandas as pd
    df = pd.DataFrame(filtered)
    df["场景值"] = df["当前值"]

    # Pre-fill with scenario overrides
    if scenario:
        for idx, row in df.iterrows():
            cid = row["Cell ID"]
            if cid in scenario.overrides:
                df.at[idx, "场景值"] = scenario.overrides[cid]

    # Also apply pending edits
    for idx, row in df.iterrows():
        cid = row["Cell ID"]
        if cid in ws.pending_edits:
            df.at[idx, "场景值"] = ws.pending_edits[cid]

    edited_df = st.data_editor(
        df,
        column_config={
            "Cell ID": st.column_config.TextColumn("Cell ID", disabled=True),
            "Indicator 名称": st.column_config.TextColumn("Indicator", disabled=True, width="medium"),
            "Table 名称": st.column_config.TextColumn("Table", disabled=True, width="medium"),
            "Sheet": st.column_config.TextColumn("Sheet", disabled=True, width="small"),
            "当前值": st.column_config.NumberColumn("当前值", disabled=True, width="small"),
            "场景值": st.column_config.NumberColumn("场景值", width="small"),
        },
        use_container_width=True,
        hide_index=True,
        key="param_editor",
    )

    # Detect changes
    pending: dict[str, Any] = {}
    for _, row in edited_df.iterrows():
        if row["场景值"] != row["当前值"]:
            # Try to parse value
            val = row["场景值"]
            if isinstance(val, str):
                try:
                    val = float(val) if "." in val else int(val)
                except (ValueError, TypeError):
                    pass
            pending[row["Cell ID"]] = val

    ws.pending_edits = pending

    # Status bar
    status_cols = st.columns([1, 1, 1, 1])
    with status_cols[0]:
        st.metric("已修改", len(pending))
    with status_cols[1]:
        st.metric("场景总数", len(ws.scenarios))
    with status_cols[2]:
        st.metric("历史记录", len(ws.history))

    with status_cols[3]:
        apply_clicked = st.button("应用并重算", type="primary", use_container_width=True)

    # Action buttons row
    action_cols = st.columns([1, 1, 2])
    with action_cols[0]:
        if st.button("清空修改", use_container_width=True):
            ws.pending_edits = {}
            save_workspace(ws)
            st.rerun()
    with action_cols[1]:
        if st.button("保存到场景", use_container_width=True):
            if scenario:
                scenario.overrides.update(pending)
                ws.pending_edits = {}
                save_workspace(ws)
                st.success(f"已保存 {len(pending)} 个修改到场景「{ws.active_scenario}」")
                st.rerun()

    # ── Execute recalculation ────────────────────────────────────────────
    if apply_clicked:
        if not pending and not (scenario and scenario.overrides):
            st.warning("暂无修改可应用")
        else:
            # Deepcopy base graph for this recalc
            working_graph = copy.deepcopy(base_graph)

            with st.spinner("重算中..."):
                result = apply_and_recalc(working_graph, ws, base_graph)

            st.success(f"重算完成：{result.affected_count} 个单元格变化，{len(result.error_cells)} 个求值失败")

            # Store working graph for downstream display
            st.session_state[f"working_graph_{task.id}"] = working_graph
            st.session_state[f"recalc_result_{task.id}"] = result
            st.rerun()

else:
    st.info("无匹配的参数单元格")

st.divider()

# ── Section C: Key metrics dashboard ─────────────────────────────────────────

recalc_result = st.session_state.get(f"recalc_result_{task.id}")
working_graph = st.session_state.get(f"working_graph_{task.id}")

if recalc_result is not None:
    st.subheader("关键指标变化")

    # Get old values from base_graph, new from working_graph
    key_ind_ids = get_key_metrics(base_graph)

    if key_ind_ids:
        metric_cols = st.columns(min(len(key_ind_ids), 5))
        for i, ind_id in enumerate(key_ind_ids[:10]):
            col = metric_cols[i % 5]
            ind = base_graph.indicators.get(ind_id)
            if ind is None:
                continue

            old_val = ind.summary_value
            working_ind = working_graph.indicators.get(ind_id) if working_graph else None
            new_val = working_ind.summary_value if working_ind else old_val

            delta = None
            delta_pct = None
            if old_val is not None and new_val is not None:
                try:
                    delta = float(new_val) - float(old_val)
                    if abs(delta) < 1e-9:
                        delta = None
                        delta_pct = None
                    else:
                        delta_pct = (delta / abs(float(old_val)) * 100) if old_val != 0 else None
                except (ValueError, TypeError):
                    pass

            with col:
                st.metric(
                    label=ind.name or ind_id,
                    value=new_val if new_val is not None else "—",
                    delta=f"{delta:+.2f} ({delta_pct:+.1f}%)" if delta is not None else None,
                    delta_color="normal" if delta is None else ("inverse" if delta < 0 else "normal"),
                )

    # Changed indicators table
    if recalc_result.changed_cells:
        # Collect affected indicators
        affected_ind_ids: set[str] = set()
        for cc in recalc_result.changed_cells:
            cell = working_graph.cells.get(cc.cell_id) if working_graph else None
            if cell and cell.indicator_id:
                affected_ind_ids.add(cell.indicator_id)

        if affected_ind_ids:
            with st.expander(f"全部受影响 Indicator（{len(affected_ind_ids)} 个）"):
                ind_rows = []
                for ind_id in sorted(affected_ind_ids):
                    base_ind = base_graph.indicators.get(ind_id)
                    work_ind = working_graph.indicators.get(ind_id) if working_graph else None
                    ind_rows.append({
                        "Indicator": base_ind.name if base_ind else ind_id,
                        "Sheet": base_ind.sheet if base_ind else "",
                        "旧汇总值": base_ind.summary_value if base_ind else None,
                        "新汇总值": work_ind.summary_value if work_ind else None,
                    })
                st.dataframe(ind_rows, use_container_width=True)

    # Error cells
    if recalc_result.error_cells:
        with st.expander(f"求值失败（{len(recalc_result.error_cells)} 个）"):
            st.write(recalc_result.error_cells[:50])

    st.divider()

    # ── Section D: Impact chain visualization ────────────────────────────────

    st.subheader("影响链可视化")

    changed_cell_ids = [c.cell_id for c in recalc_result.changed_cells]

    if changed_cell_ids:
        viz_col1, viz_col2 = st.columns([2, 1])

        with viz_col1:
            root_cell = st.selectbox(
                "选择传播起点",
                changed_cell_ids,
                format_func=lambda cid: f"{cid} — {base_graph.cells[cid].value if cid in base_graph.cells else ''}",
            )

            depth = st.slider("最大深度", 1, 15, 5)
            max_nodes = st.slider("最大节点数", 50, 2000, 500)

            if st.button("生成传播图", type="secondary"):
                # Build a pseudo-diff from recalc results
                diff_cells = [
                    {"id": c.cell_id, "old": c.old_value, "new": c.new_value, "formula": c.formula or "", "sheet": ""}
                    for c in recalc_result.changed_cells
                ]
                # Fill sheet info
                for dc in diff_cells:
                    cell = base_graph.cells.get(dc["id"])
                    if cell:
                        dc["sheet"] = cell.sheet or ""

                pseudo_diff = SnapshotDiff(
                    changed_cells=diff_cells,
                    affected_indicators=[],
                    summary={
                        "total_changed_cells": len(diff_cells),
                        "total_changed_indicators": 0,
                        "sheets_affected": [],
                    },
                )

                data = build_propagation_data(
                    base_graph, pseudo_diff, root_cell,
                    max_depth=depth, max_nodes=max_nodes,
                )

                html = render_propagation_html(data)
                components.html(html, height=600, scrolling=True)

        with viz_col2:
            st.caption(f"共 {len(changed_cell_ids)} 个变化单元格")
            st.caption("选择起点后点击「生成传播图」查看影响链")

    st.divider()

    # ── Section E: Modification history ──────────────────────────────────────

    st.subheader("修改历史")

    if ws.history:
        # Sort newest first
        sorted_history = sorted(ws.history, key=lambda r: r.timestamp, reverse=True)

        hist_data = []
        for r in sorted_history[:50]:
            hist_data.append({
                "时间": r.timestamp[:19],
                "场景": r.scenario,
                "Cell ID": r.cell_id,
                "Indicator": r.indicator_name,
                "旧值": r.old_value,
                "新值": r.new_value,
                "回滚": r.id,  # store record id for button
            })

        hist_df = st.dataframe(hist_data, use_container_width=True, hide_index=True)

        # Rollback buttons — use a separate section since dataframe buttons don't work
        st.caption("点击回滚将撤销该修改并重新计算：")
        rollback_cols = st.columns(min(len(sorted_history[:10]), 4))
        for i, r in enumerate(sorted_history[:10]):
            col = rollback_cols[i % 4]
            with col:
                label = f"回滚 {r.cell_id[:20]}... ({r.scenario})"
                if st.button(label, key=f"rollback_{r.id}", use_container_width=True):
                    updates = rollback_record(ws, r.id)
                    if updates is not None:
                        working_graph = copy.deepcopy(base_graph)
                        with st.spinner("回滚中..."):
                            result = apply_and_recalc(working_graph, ws, base_graph)
                        st.session_state[f"working_graph_{task.id}"] = working_graph
                        st.session_state[f"recalc_result_{task.id}"] = result
                        st.success(f"已回滚 {r.cell_id}，重算完成")
                        st.rerun()
                    else:
                        st.error("回滚失败：记录不存在")

        if len(ws.history) > 50:
            st.caption(f"仅显示最近 50 条，共 {len(ws.history)} 条")

        # Clear history
        if st.button("清空历史（保留最近 10 条）"):
            ws.history = ws.history[-10:]
            save_workspace(ws)
            st.rerun()
    else:
        st.info("暂无修改记录")
