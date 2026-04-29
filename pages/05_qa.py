"""Page 5: LLM-powered financial Q&A."""
from __future__ import annotations
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.llm import QAEngine
from financial_kg.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    save_config,
)

st.set_page_config(page_title="智能问答", layout="wide")
st.title("💬 财务模型智能问答")

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

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("LLM 配置")
    base_url = st.text_input("Base URL", value=LLM_BASE_URL or "https://api.openai.com/v1")
    api_key = st.text_input("API Key", value=LLM_API_KEY or "", type="password")
    model = st.text_input("Model", value=LLM_MODEL or "gpt-4o-mini")
    top_k = st.slider("检索 Indicator 数量 (top-k)", 3, 20, 8)

    st.divider()
    st.header("Neo4j 配置")
    use_neo4j = st.checkbox("启用 Neo4j 图遍历", value=False)
    neo4j_uri = st.text_input("URI", value=NEO4J_URI)
    neo4j_user = st.text_input("User", value=NEO4J_USER)
    neo4j_pwd = st.text_input("Password", value=NEO4J_PASSWORD, type="password")

    st.divider()
    if st.button("保存配置到 .env", type="secondary"):
        save_config(
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_pwd,
        )
        st.success("配置已保存到 .env 文件")


@st.cache_resource(show_spinner="连接 Neo4j...")
def _get_neo4j(uri: str, user: str, pwd: str):
    try:
        from financial_kg.storage.neo4j_store import Neo4jStore
        return Neo4jStore(uri, user, pwd)
    except Exception as e:
        st.warning(f"Neo4j 连接失败：{e}")
        return None


neo4j_store = None
if use_neo4j and neo4j_pwd.strip():
    neo4j_store = _get_neo4j(neo4j_uri, neo4j_user, neo4j_pwd)


@st.cache_resource(show_spinner="初始化问答引擎...")
def _get_engine(task_id: str, _graph, _neo4j, base_url: str, api_key: str, model: str):
    return QAEngine(
        graph=_graph,
        neo4j_store=_neo4j,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=model,
        task_id=task_id,
    )


engine = _get_engine(task.id, graph, neo4j_store, base_url, api_key, model)

# ── Chat ──────────────────────────────────────────────────────────────────────
if "qa_history" not in st.session_state:
    st.session_state.qa_history = []

# Render existing chat history
for msg in st.session_state.qa_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

question = st.chat_input("请输入财务问题，如：2030年动态总投资是多少？")

if question:
    # Show user message immediately
    with st.chat_message("user"):
        st.markdown(question)
    st.session_state.qa_history.append({"role": "user", "content": question})

    # Stream assistant response
    with st.chat_message("assistant"):
        # Use a mutable container so the generator can write back to outer scope
        state = {"full_answer": "", "retrieval": None, "cypher": None}

        def _stream():
            for event_type, data in engine.ask_stream(
                question,
                chat_history=st.session_state.qa_history,
                top_k=top_k,
            ):
                if event_type == "retrieval":
                    state["retrieval"] = data
                elif event_type == "cypher":
                    state["cypher"] = data
                elif event_type == "chunk":
                    state["full_answer"] += data
                    yield data
                elif event_type in ("answer", "error"):
                    state["full_answer"] = data
                    yield data

        st.write_stream(_stream())

    st.session_state.qa_history.append({"role": "assistant", "content": state["full_answer"]})
    st.session_state["_last_retrieval"] = state["retrieval"]
    st.session_state["_last_cypher"] = state["cypher"]

# Show retrieval context for the last response
last_retrieval = st.session_state.get("_last_retrieval")
if last_retrieval and last_retrieval.contexts:
    with st.expander(f"检索上下文（{len(last_retrieval.contexts)} 个指标）"):
        for ctx in last_retrieval.contexts:
            ind = ctx.indicator
            st.markdown(
                f"**{ind.name}** — 匹配方式: `{ctx.match_reason}` 分数: {ctx.match_score:.2f}"
            )
            if ind.time_series:
                ts_items = list(ind.time_series.items())
                query_years = getattr(last_retrieval, "query_years", [])
                if query_years:
                    hits = [(k, v) for k, v in ts_items if any(y in str(k) for y in query_years)]
                    if hits:
                        st.caption("查询年份: " + "  ".join(f"{k}={v}" for k, v in hits))
                st.caption("  ".join(f"{p}={v}" for p, v in ts_items[:5]))
            if ctx.upstream:
                st.caption("上游: " + ", ".join(u.name for u in ctx.upstream))
            if ctx.downstream:
                st.caption("被依赖: " + ", ".join(d.name for d in ctx.downstream))
            st.divider()

last_cypher = st.session_state.get("_last_cypher")
if last_cypher and last_cypher[0]:
    with st.expander("Cypher 查询"):
        st.code(last_cypher[0], language="cypher")
        if last_cypher[1]:
            st.text(last_cypher[1])

if st.button("清空对话"):
    st.session_state.qa_history = []
    st.session_state.pop("_last_retrieval", None)
    st.session_state.pop("_last_cypher", None)
    st.rerun()
