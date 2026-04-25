"""Page 1: Upload and parse an Excel financial model."""
from __future__ import annotations
import os
import sys
import time
import uuid
import tempfile

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.parser.excel_reader import read_excel
from financial_kg.parser.cell_extractor import build_cell_graph
from financial_kg.parser.indicator_builder import build_indicators
from financial_kg.parser.relationship_builder import infer_relationships
from financial_kg.storage.json_store import save_graph, load_graph, verify_cell_count
from financial_kg.storage.task_db import TaskDB
from financial_kg.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, save_config

st.set_page_config(page_title="上传解析", layout="wide")
st.title("📁 上传 Excel 财务模型")

db = TaskDB()

uploaded = st.file_uploader("选择 Excel 文件 (.xlsx)", type=["xlsx", "xls"])

if uploaded:
    task_id = st.text_input("任务 ID（留空自动生成）", value="")
    output_dir = st.text_input("输出目录", value="output")

    if st.button("开始解析", type="primary"):
        if not task_id.strip():
            task_id = str(uuid.uuid4())[:8]

        # Save uploaded file to temp
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(uploaded.read())
            tmp_path = tmp.name

        db.create_task(task_id, uploaded.name, output_dir)
        db.update_task(task_id, status="running")

        progress = st.progress(0, text="读取 Excel...")
        status_box = st.empty()

        try:
            t0 = time.time()
            sheet_cells = read_excel(tmp_path)
            total_raw = sum(len(v) for v in sheet_cells.values())
            progress.progress(20, text=f"读取完成：{len(sheet_cells)} 个 sheet，{total_raw:,} 个单元格")

            status_box.info("构建 Cell 层图谱...")
            graph = build_cell_graph(sheet_cells)
            progress.progress(50, text="Cell 层完成")

            status_box.info("构建 Indicator + Table 层...")
            build_indicators(sheet_cells, graph)
            infer_relationships(graph)
            progress.progress(80, text="Indicator/Table 层完成")

            status_box.info("保存 JSON...")
            paths = save_graph(graph, output_dir, task_id=task_id)
            progress.progress(100, text="保存完成")

            stats = graph.stats()
            db.update_task(
                task_id,
                status="done",
                cell_count=stats["total_cells"],
                indicator_count=stats["total_indicators"],
                table_count=stats["total_tables"],
                output_dir=output_dir,
            )

            elapsed = time.time() - t0
            st.success(f"解析完成！耗时 {elapsed:.1f}s")
            col1, col2, col3 = st.columns(3)
            col1.metric("Cell 节点", f"{stats['total_cells']:,}")
            col2.metric("Indicator 节点", f"{stats['total_indicators']:,}")
            col3.metric("Table 节点", f"{stats['total_tables']:,}")

            st.subheader("输出文件")
            for layer, path in paths.items():
                size_kb = os.path.getsize(path) / 1024
                st.write(f"- **{layer}**: `{path}` ({size_kb:.0f} KB)")

            check = verify_cell_count(graph, total_raw)
            status = "✅ 一致" if check["match"] else f"⚠️ 差异 {check['diff']:+d}"
            st.write(f"Cell 数量验证：{check['actual']:,} / {check['expected']:,}  {status}")

            st.session_state["current_task_id"] = task_id
            st.session_state["current_graph"] = graph

        except Exception as e:
            db.update_task(task_id, status="error", error_msg=str(e))
            st.error(f"解析失败：{e}")
        finally:
            os.unlink(tmp_path)

st.divider()
st.subheader("历史任务")
tasks = db.list_tasks()
if tasks:
    for t in tasks:
        icon = {"done": "✅", "running": "⏳", "error": "❌", "pending": "🕐"}.get(t.status, "?")
        st.write(f"{icon} **{t.id}** — {t.filename} — cells:{t.cell_count:,} inds:{t.indicator_count:,} ({t.created_at[:19]})")
else:
    st.info("暂无历史任务")

st.divider()
st.subheader("Neo4j 导入")

done_tasks = [t for t in tasks if t.status == "done"]
if not done_tasks:
    st.info("暂无已完成的任务可导入。")
else:
    neo4j_task_label = st.selectbox(
        "选择要导入的任务",
        [f"{t.id} — {t.filename}" for t in done_tasks],
        key="neo4j_task_select",
    )
    selected_neo4j_task = next(
        t for t in done_tasks if f"{t.id} — {t.filename}" == neo4j_task_label
    )

    col_uri, col_user, col_pwd = st.columns(3)
    neo4j_uri = col_uri.text_input("Neo4j URI", value=NEO4J_URI, key="n4j_uri")
    neo4j_user = col_user.text_input("User", value=NEO4J_USER, key="n4j_user")
    neo4j_pwd = col_pwd.text_input("Password", value=NEO4J_PASSWORD, type="password", key="n4j_pwd")

    if st.button("保存 Neo4j 配置到 .env"):
        save_config(neo4j_uri=neo4j_uri, neo4j_user=neo4j_user, neo4j_password=neo4j_pwd)
        st.success("Neo4j 配置已保存")

    col_import, col_clear = st.columns([1, 1])

    if col_import.button("导入到 Neo4j", type="primary"):
        if not neo4j_pwd.strip():
            st.error("请填写 Neo4j 密码")
        else:
            try:
                from financial_kg.storage.neo4j_store import Neo4jStore
                cells_path = os.path.join(
                    selected_neo4j_task.output_dir,
                    f"{selected_neo4j_task.id}_cells.json",
                )
                with st.spinner("加载图谱..."):
                    g = load_graph(cells_path)

                neo4j_progress = st.progress(0, text="连接 Neo4j...")
                step_msgs = [
                    "导入 Cell 节点...", "导入 Indicator 节点...", "导入 Table 节点...",
                    "导入 DEPENDS_ON 关系...", "导入 CALCULATES_FROM 关系...",
                    "导入 FEEDS_INTO 关系...", "导入 BELONGS_TO 关系...",
                ]
                step_idx = [0]

                def _progress_cb(msg: str) -> None:
                    pct = int((step_idx[0] / len(step_msgs)) * 100)
                    neo4j_progress.progress(pct, text=msg)
                    step_idx[0] += 1

                with Neo4jStore(neo4j_uri, neo4j_user, neo4j_pwd) as store:
                    counts = store.import_graph(g, task_id=selected_neo4j_task.id, progress_callback=_progress_cb)

                neo4j_progress.progress(100, text="导入完成！")
                st.success("Neo4j 导入成功")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Cell 节点", f"{counts.get('cells', 0):,}")
                c2.metric("Indicator 节点", f"{counts.get('indicators', 0):,}")
                c3.metric("Table 节点", f"{counts.get('tables', 0):,}")
                c4.metric("DEPENDS_ON 关系", f"{counts.get('depends_on', 0):,}")
            except Exception as e:
                st.error(f"Neo4j 导入失败：{e}")

    if col_clear.button("清空 Neo4j 数据库", type="secondary"):
        if not neo4j_pwd.strip():
            st.error("请填写 Neo4j 密码")
        elif st.session_state.get("_neo4j_clear_confirm"):
            try:
                from financial_kg.storage.neo4j_store import Neo4jStore
                with Neo4jStore(neo4j_uri, neo4j_user, neo4j_pwd) as store:
                    store.clear_database()
                st.success("Neo4j 数据库已清空")
                st.session_state["_neo4j_clear_confirm"] = False
            except Exception as e:
                st.error(f"清空失败：{e}")
        else:
            st.session_state["_neo4j_clear_confirm"] = True
            st.warning('再次点击"清空 Neo4j 数据库"确认操作')
