"""Page 5: Financial model Q&A — overview + explore + chat tabs."""
from __future__ import annotations

import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.llm import QAEngine
from financial_kg.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    save_config,
)
from financial_kg.viz.qa_chart import render_time_series_html
from financial_kg.viz.qa_relation import (
    build_indicator_catalog,
    build_indicator_relation_graph,
    render_relation_html,
)
from financial_kg.viz.qa_overview import (
    get_overview_kpis,
    build_trend_chart_data,
    render_overview_html,
    build_category_overview,
)

st.set_page_config(layout="wide")
st.title("财务模型智能问答")

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
    st.header("配置")

    with st.expander("LLM / Neo4j", expanded=False):
        base_url = st.text_input("Base URL", value=LLM_BASE_URL or "https://api.openai.com/v1", key="cfg_base")
        api_key = st.text_input("API Key", value=LLM_API_KEY or "", type="password", key="cfg_key")
        model = st.text_input("Model", value=LLM_MODEL or "gpt-4o-mini", key="cfg_model")
        top_k = st.slider("检索 top-k", 3, 20, 8, key="cfg_topk")
        st.divider()
        use_neo4j = st.checkbox("启用 Neo4j", value=False, key="cfg_neo4j")
        neo4j_uri = st.text_input("URI", value=NEO4J_URI, key="cfg_n4j_uri")
        neo4j_user = st.text_input("User", value=NEO4J_USER, key="cfg_n4j_user")
        neo4j_pwd = st.text_input("Password", value=NEO4J_PASSWORD, type="password", key="cfg_n4j_pwd")
        st.divider()
        if st.button("保存配置", type="secondary", key="cfg_save"):
            save_config(
                llm_base_url=base_url,
                llm_api_key=api_key,
                llm_model=model,
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_pwd,
            )
            st.success("已保存")

# ── Engine init ──────────────────────────────────────────────────────────────

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

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_explore, tab_chat = st.tabs(["财务概览", "指标探索", "智能问答"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: 财务概览 — KPI cards + trend chart, zero questions needed
# ═══════════════════════════════════════════════════════════════════════════════
with tab_overview:
    kpis = get_overview_kpis(graph)
    trend = build_trend_chart_data(graph, limit=6)
    html = render_overview_html(kpis, trend)
    components.html(html, height=700, scrolling=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: 指标探索 — category navigation + indicator table + relation graph
# ═══════════════════════════════════════════════════════════════════════════════
with tab_explore:
    # Gather real categories (skip unknown)
    all_cats: dict[str, int] = {}
    for ind in graph.indicators.values():
        cat = ind.category or "未分类"
        all_cats.setdefault(cat, 0)
        all_cats[cat] += 1
    sorted_cats = sorted(all_cats.keys(), key=lambda c: -all_cats[c])
    # Remove "未分类" from end
    if "未分类" in sorted_cats:
        sorted_cats.remove("未分类")
        sorted_cats.append("未分类")

    # Category button row (horizontal scroll)
    cat_cols = st.columns(min(len(sorted_cats), 8))
    for i, cat in enumerate(sorted_cats):
        with cat_cols[i % 8]:
            is_active = st.session_state.get("explore_cat") == cat
            label = f"● {cat}" if is_active else f"{cat} ({all_cats[cat]})"
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"ecat_{cat}", use_container_width=True, type=btn_type):
                st.session_state["explore_cat"] = cat

    active_cat = st.session_state.get("explore_cat")

    if active_cat:
        cat_data = build_category_overview(graph, active_cat)
        inds = cat_data.get("indicators", [])

        # Summary bar
        n_with_value = sum(1 for i in inds if i["value"] != "—")
        n_with_ts = sum(1 for i in inds if i["time_series"])
        st.caption(f"{active_cat} — {len(inds)} 个指标 | {n_with_value} 个有值 | {n_with_ts} 个有时间序列")

        # Indicator table
        if inds:
            df_rows = []
            for ind in inds:
                df_rows.append({
                    "指标": ind["name"],
                    "值": f"{ind['value']} {ind['unit']}".strip(),
                    "时间序列": "✅" if ind["time_series"] else "—",
                    "类别": active_cat,
                    "ID": ind["id"],
                })
            st.dataframe(df_rows, use_container_width=True, height=350, hide_index=True)

        # Relation graph for top indicator
        if inds and inds[0]["id"]:
            rel_id = st.session_state.get("explore_rel_id")
            if not rel_id:
                # Default to first indicator with relationships
                for ind in inds[:5]:
                    raw = graph.indicators.get(ind["id"])
                    if raw and (raw.depends_on_indicators or raw.depended_by_indicators):
                        rel_id = ind["id"]
                        st.session_state["explore_rel_id"] = rel_id
                        break

            if rel_id:
                relation = build_indicator_relation_graph(graph, rel_id)
                if relation["nodes"]:
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        html = render_relation_html(relation, title="指标关系图", height="400px")
                        components.html(html, height=400, scrolling=False)
                    with col_b:
                        st.caption("选择其他指标查看关系")
                        for ind in inds[:10]:
                            raw = graph.indicators.get(ind["id"])
                            if raw and (raw.depends_on_indicators or raw.depended_by_indicators):
                                label = ind["name"][:20]
                                if st.button(label, key=f"erel_{ind['id']}", use_container_width=True, type="secondary"):
                                    st.session_state["explore_rel_id"] = ind["id"]
                                    st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: 智能问答 — chat interface with sidebar catalog
# ═══════════════════════════════════════════════════════════════════════════════
with tab_chat:
    col_left, col_right = st.columns([3, 2])

    with col_left:
        # ── Quick questions ───────────────────────────────────────────────
        _QUICK = {
            "投资": ["动态总投资是多少？", "静态总投资是多少？", "建设投资是多少？", "建设期利息是多少？"],
            "收入利润": ["全期营业收入是多少？", "利润总额是多少？", "净利润是多少？"],
            "现金流": ["全期净现金流是多少？", "资本金内部收益率是多少？"],
            "税费": ["增值税是多少？", "所得税总额是多少？", "税金及附加是多少？"],
            "偿债": ["偿债备付率是多少？", "利息备付率是多少？", "借款偿还期是多少？"],
        }

        st.subheader("快速提问")
        for cat, qs in _QUICK.items():
            st.caption(cat)
            cols = st.columns(min(len(qs), 3))
            for i, q in enumerate(qs):
                with cols[i % 3]:
                    if st.button(q, key=f"qq_{q}", use_container_width=True, type="secondary"):
                        st.session_state["qa_ask_question"] = q

        # ── Chat input ────────────────────────────────────────────────────
        question = st.chat_input("或输入自定义问题...")

        if "qa_ask_question" in st.session_state:
            question = st.session_state.pop("qa_ask_question")

        # ── Chat history ──────────────────────────────────────────────────
        _CHAT_KEY = f"qa_chat_{task.id}"
        if _CHAT_KEY not in st.session_state:
            st.session_state[_CHAT_KEY] = db.load_qa_history(task.id) or []

        chat_history = st.session_state[_CHAT_KEY]

        def _persist_chat():
            db.save_qa_history(task.id, chat_history)

        # ── Helpers ───────────────────────────────────────────────────────

        def _build_structured_answer(question: str, state: dict) -> dict:
            retrieval = state.get("retrieval")
            text = state.get("full_answer", "")

            result = {
                "text": text,
                "metrics": [],
                "chart_data": [],
                "confidence": 0,
                "sources": [],
                "summary": "",
            }

            if not retrieval or not retrieval.contexts:
                result["confidence"] = 0
                result["summary"] = "未找到相关指标数据。"
                return result

            contexts = retrieval.contexts

            # Confidence
            avg_score = sum(c.match_score for c in contexts) / len(contexts) if contexts else 0
            max_score = max((c.match_score for c in contexts), default=0)
            has_time_series = sum(1 for c in contexts if c.indicator.time_series)
            result["confidence"] = min(100, int(
                (min(avg_score / 10, 1) * 40) +
                (min(max_score / 10, 1) * 30) +
                (min(len(contexts) / 8, 1) * 15) +
                (min(has_time_series / max(len(contexts), 1), 1) * 15)
            ))

            # Summary
            top = contexts[0].indicator if contexts else None
            if top:
                val = top.display_value or (
                    f"{top.summary_value:.2f}" if isinstance(top.summary_value, float)
                    else str(top.summary_value or "—")
                )
                result["summary"] = f"**{top.name}** = {val} {top.unit or ''}"

            # Metrics
            for ctx in contexts[:3]:
                ind = ctx.indicator
                val = ind.display_value if ind.display_value is not None else (
                    f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
                    else str(ind.summary_value or "—")
                )
                year_val = ""
                if retrieval.query_years and ind.time_series:
                    for k, v in ind.time_series.items():
                        if any(y in str(k) for y in retrieval.query_years):
                            year_val = f"{k}: {v}"
                            break
                result["metrics"].append({
                    "name": ind.name[:25],
                    "value": year_val or val,
                    "unit": ind.unit or "",
                })

            # Chart data
            for ctx in contexts:
                ind = ctx.indicator
                if ind.time_series:
                    if retrieval.query_years:
                        filtered = {k: v for k, v in ind.time_series.items()
                                   if any(y in str(k) for y in retrieval.query_years)}
                    else:
                        filtered = ind.time_series
                    if filtered:
                        result["chart_data"].append({
                            "name": ind.name,
                            "values": filtered,
                        })

            # Sources
            for ctx in contexts:
                ind = ctx.indicator
                val = ind.display_value if ind.display_value is not None else (
                    f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
                    else str(ind.summary_value or "—")
                )
                result["sources"].append({
                    "name": ind.name,
                    "sheet": ind.sheet,
                    "value": val,
                    "unit": ind.unit or "",
                    "score": ctx.match_score,
                    "indicator_id": ind.id,
                })

            return result

        def _generate_smart_follow_ups(answer_data: dict) -> list[str]:
            sources = answer_data.get("sources", [])
            chart_data = answer_data.get("chart_data", [])
            follow_ups: list[str] = []
            seen: set[str] = set()

            def _add(q: str):
                if q not in seen and q not in follow_ups:
                    follow_ups.append(q)
                    seen.add(q)

            if not sources:
                return follow_ups

            top_src = sources[0]
            top_ind = graph.indicators.get(top_src["indicator_id"])
            if not top_ind:
                return follow_ups

            if top_ind.formula_readable:
                _add(f"{top_ind.name}的计算公式是什么？")

            if top_ind.depends_on_indicators:
                up_names = [graph.indicators[d].name for d in top_ind.depends_on_indicators[:2]
                            if d in graph.indicators and graph.indicators[d].name]
                if up_names:
                    _add(f"{' 和 '.join(up_names)} 是多少？")

            if top_ind.depended_by_indicators:
                _add(f"{top_ind.name}影响哪些指标？")

            if chart_data:
                _add(f"{top_ind.name}的变化趋势如何？")

            if top_ind.category:
                same_cat = [ind for ind in graph.indicators.values()
                            if ind.category == top_ind.category and ind.id != top_ind.id and ind.name][:2]
                if same_cat:
                    names = " 和 ".join(s.name for s in same_cat)
                    if names:
                        _add(f"{top_ind.category}类别中 {names} 的值是多少？")

            return follow_ups[:4]

        def _render_structured_answer(data: dict):
            text = data.get("text", "")
            metrics = data.get("metrics", [])
            chart_data = data.get("chart_data", [])
            confidence = data.get("confidence", 0)
            sources = data.get("sources", [])
            summary = data.get("summary", "")

            if confidence > 0:
                conf_label = "高" if confidence >= 70 else ("中" if confidence >= 40 else "低")
                color = "#a6e3a1" if confidence >= 70 else ("#f9e2af" if confidence >= 40 else "#f38ba8")
                st.markdown(
                    f"<span style='color:{color};font-size:13px;'>置信度 {'█' * (confidence // 10)}"
                    f"{'░' * (10 - confidence // 10)} {confidence}% ({conf_label})</span>",
                    unsafe_allow_html=True,
                )

            if summary:
                st.markdown(summary)

            if metrics:
                m_cols = st.columns(min(len(metrics), 3))
                for i, m in enumerate(metrics):
                    with m_cols[i]:
                        st.metric(label=m["name"], value=str(m["value"]), delta=m["unit"] if m["unit"] else None)

            if chart_data:
                with st.expander(f"数据趋势（{len(chart_data)} 个指标）", expanded=True):
                    html = render_time_series_html(chart_data)
                    components.html(html, height=300, scrolling=False)

            if text:
                st.divider()
                st.markdown(text)

            if sources:
                with st.expander(f"数据来源（{len(sources)} 个指标）", expanded=False):
                    df_rows = []
                    for src in sources:
                        score_color = "🟢" if src["score"] >= 7 else ("🟡" if src["score"] >= 4 else "🔴")
                        df_rows.append({
                            "匹配": score_color,
                            "指标": src["name"],
                            "Sheet": src["sheet"],
                            "值": f"{src['value']} {src['unit']}".strip(),
                            "评分": f"{src['score']:.1f}",
                        })
                    st.dataframe(df_rows, use_container_width=True, hide_index=True)

            if sources:
                top_ind_id = sources[0]["indicator_id"]
                if st.session_state.get("qa_selected_relation") != top_ind_id:
                    st.session_state["qa_selected_relation"] = top_ind_id

        # ── Render chat history ───────────────────────────────────────────
        for msg in chat_history:
            if msg["role"] == "user":
                with st.chat_message("user"):
                    st.markdown(msg["content"])
            elif msg["role"] == "assistant":
                with st.chat_message("assistant"):
                    if isinstance(msg["content"], dict):
                        _render_structured_answer(msg["content"])
                    else:
                        st.markdown(msg["content"])

        # ── Process new question ──────────────────────────────────────────
        if question:
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                state: dict = {"full_answer": "", "retrieval": None, "cypher": None}
                status = st.empty()
                status.info("正在检索指标...")

                def _stream():
                    for event_type, data in engine.ask_stream(
                        question,
                        chat_history=chat_history,
                        top_k=top_k,
                    ):
                        if event_type == "retrieval":
                            state["retrieval"] = data
                            n = len(data.contexts) if data.contexts else 0
                            status.info(f"检索到 {n} 个相关指标，正在生成回答...")
                        elif event_type == "cypher":
                            state["cypher"] = data
                            status.info("正在生成回答...")
                        elif event_type == "error":
                            status.empty()
                            st.warning(data)
                            state["full_answer"] = f"(错误) {data}"
                            yield ""
                        elif event_type == "chunk":
                            status.empty()
                            state["full_answer"] += data
                            yield data
                        elif event_type == "answer":
                            status.empty()
                            state["full_answer"] = data
                            yield data

                st.write_stream(_stream())

            structured = _build_structured_answer(question, state)

            def _already_asked(q: str) -> bool:
                return any(m.get("role") == "user" and m.get("content") == q for m in chat_history[-5:])

            if not _already_asked(question):
                chat_history.append({"role": "user", "content": question})
            chat_history.append({"role": "assistant", "content": structured})
            _persist_chat()
            st.rerun()

        # ── Clear button ──────────────────────────────────────────────────
        if st.button("清空对话", type="secondary"):
            chat_history.clear()
            db.clear_qa_history(task.id)
            st.rerun()

        # ── Smart follow-ups ──────────────────────────────────────────────
        if chat_history and chat_history[-1]["role"] == "assistant":
            last_answer = chat_history[-1]["content"]
            if isinstance(last_answer, dict):
                suggestions = _generate_smart_follow_ups(last_answer)
                if suggestions:
                    st.divider()
                    st.caption("你可能还想问")
                    sug_cols = st.columns(min(len(suggestions), 3))
                    for i, sq in enumerate(suggestions):
                        with sug_cols[i % 3]:
                            if st.button(sq, key=f"fu_{sq}", use_container_width=True, type="secondary"):
                                chat_history.append({"role": "user", "content": sq})
                                _persist_chat()
                                st.rerun()

    with col_right:
        st.subheader("指标目录")
        catalog = build_indicator_catalog(graph)
        sorted_cats = sorted(catalog.keys(), key=lambda c: len(catalog[c]), reverse=True)

        for cat in sorted_cats:
            tables = catalog[cat]
            with st.expander(f"{cat} ({sum(len(v) for v in tables.values())})"):
                for tbl_name, inds in tables.items():
                    with st.expander(f"{tbl_name} ({len(inds)})"):
                        for ind in inds[:20]:  # limit to avoid rendering huge lists
                            label = f"{ind['name']}"
                            if ind["value"] and ind["value"] != "—":
                                label += f"  {ind['value']}"
                            if st.button(label, key=f"ind_{ind['id']}", use_container_width=True):
                                st.session_state["qa_ask_question"] = f"{ind['name']}是多少？"
                                st.rerun()

        selected_ind_id = st.session_state.get("qa_selected_relation")
        if selected_ind_id:
            relation = build_indicator_relation_graph(graph, selected_ind_id)
            if relation["nodes"]:
                st.divider()
                st.caption("关系图谱")
                html = render_relation_html(relation, height="300px")
                components.html(html, height=300, scrolling=False)
                if st.button("关闭图谱", key="close_relation", type="secondary"):
                    st.session_state["qa_selected_relation"] = None
                    st.rerun()
