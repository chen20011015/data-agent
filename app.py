# -*- coding: utf-8 -*-
"""数据分析 Agent —— Streamlit 前端。

设计重点：把 Agent 的决策链路可视化。
用户看到的不只是答案，还有"它怎么想的、查了几次、哪一步被打回了"。
"""

from __future__ import annotations

import html
import os

import streamlit as st

from agent import DataAgent, Database, LLM, LLMError

st.set_page_config(
    page_title="数据分析 Agent",
    layout="wide",
    initial_sidebar_state="expanded",
)

SAMPLES = [
    "每个月的销售额趋势",
    "退款率最高的三个商品",
    "华东和华南的销售额对比",
    "哪个渠道客单价最高",
]


# ============================================================ 工具函数


def get_key() -> str:
    """优先读 Secrets，其次读侧栏输入，最后读环境变量。"""
    for name in ("API_KEY", "CHAT_API_KEY"):
        try:
            v = st.secrets.get(name, "")
            if v:
                return str(v).strip()
        except Exception:
            pass
    manual = st.session_state.get("manual_key", "").strip()
    if manual:
        return manual
    return os.getenv("API_KEY", "").strip()


PHASE_LABEL = {
    "generate": "生成",
    "review": "自审",
    "guard": "校验",
    "execute": "执行",
    "check": "检查",
    "retry": "重试",
    "summarize": "总结",
}


def render_trace(trace) -> None:
    """把 Agent 的每一步渲染成时间线。"""
    rows = []
    for t in trace:
        color = "#1a7f37" if t.ok else "#c0392b"
        mark = "OK" if t.ok else "FAIL"
        phase = PHASE_LABEL.get(t.phase, t.phase)
        detail = html.escape(t.detail or "")[:300]
        rows.append(
            f'<div style="display:flex;gap:10px;padding:6px 0;'
            f'border-bottom:1px solid #eee;font-size:13px;line-height:1.5">'
            f'<div style="flex:0 0 34px;color:#888">{t.step}</div>'
            f'<div style="flex:0 0 46px;color:#555">{phase}</div>'
            f'<div style="flex:0 0 44px;color:{color};font-weight:600">{mark}</div>'
            f'<div style="flex:1;color:#333;word-break:break-word">'
            f'<b>{html.escape(t.title)}</b>'
            + (f'<div style="color:#666;margin-top:2px;white-space:pre-wrap">{detail}</div>'
               if detail else "")
            + "</div></div>"
        )
    st.markdown(
        '<div style="border:1px solid #e5e5e5;border-radius:8px;padding:4px 12px;'
        'background:#fafafa">' + "".join(rows) + "</div>",
        unsafe_allow_html=True,
    )


def render_result(res) -> None:
    st.markdown(res.summary)

    if res.retries:
        st.caption(f"本轮共重试 {res.retries} 次")

    if res.ok and res.rows:
        data = [dict(zip(res.columns, r)) for r in res.rows]
        st.dataframe(data, use_container_width=True, height=min(340, 38 * len(data) + 40))

        # 两列且一列是数字时，顺手画个图
        if len(res.columns) == 2 and len(data) <= 60:
            numeric_col = None
            for c in res.columns:
                vals = [d[c] for d in data]
                if all(isinstance(v, (int, float)) for v in vals):
                    numeric_col = c
                    break
            if numeric_col:
                label_col = [c for c in res.columns if c != numeric_col][0]
                st.bar_chart(
                    {str(d[label_col]): d[numeric_col] for d in data},
                    height=260,
                )

    with st.expander("Agent 决策链路"):
        render_trace(res.trace)

    if res.sql:
        with st.expander("执行的 SQL"):
            st.code(res.sql, language="sql")

    if not res.ok:
        st.error(res.error or "未能完成查询")


# ============================================================ 侧栏

with st.sidebar:
    st.markdown("### 数据分析 Agent")
    st.caption("上传 CSV，用大白话提问，Agent 自己写 SQL、自己检查、自己改")

    st.divider()
    st.markdown("**1. 配置模型**")
    key_now = get_key()
    if key_now:
        st.success("API 已就绪", icon="✅")
    else:
        st.warning("未配置 Key")
    with st.expander("填写自己的 Key", expanded=not bool(key_now)):
        st.text_input(
            "API Key",
            type="password",
            key="manual_key",
            placeholder="留空则使用预置 Key",
            help="填了会立即用于本次会话，不会上传到任何服务器",
        )
        st.caption(f"当前模型：`{os.getenv('CHAT_MODEL', 'glm-4-flash')}`")

    st.divider()
    st.markdown("**2. 上传数据**")
    files = st.file_uploader(
        "支持 CSV，可多选（文件名即表名）",
        type=["csv"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if files:
        loaded = tuple(sorted((f.name, f.size) for f in files))
        if st.session_state.get("files_sig") != loaded:
            db = Database()
            ok, fail = [], []
            for f in files:
                table = os.path.splitext(f.name)[0].replace(" ", "_")
                try:
                    n = db.load_csv(table, f.getvalue().decode("utf-8-sig", errors="replace"))
                    ok.append(f"{table}（{n} 行）")
                except Exception as e:
                    fail.append(f"{f.name}：{e}")
            st.session_state.db = db
            st.session_state.files_sig = loaded
            st.session_state.messages = []
            st.session_state.load_msg = ok
            st.session_state.load_err = fail
            st.rerun()

    for msg in st.session_state.get("load_msg", []):
        st.caption(f"已载入 {msg}")
    for err in st.session_state.get("load_err", []):
        st.error(err)

    db = st.session_state.get("db")
    if db is not None and db.tables:
        with st.expander("查看表结构"):
            st.code(db.schema_text(sample_rows=2), language="sql")

    st.divider()
    if st.button("清空对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.caption("执行环境：内存 SQLite · 只读 · 拒绝一切写操作")


# ============================================================ 主区

st.title("数据分析 Agent")
st.caption("不是 Text-to-SQL 脚本：它会自审 SQL、拦截危险操作、检查结果、失败后自己改")

db: Database | None = st.session_state.get("db")

if db is None or not db.tables:
    st.info(
        "### 开始使用\n"
        "1. 在左侧上传一个或多个 CSV（文件名就是表名）\n"
        "2. 在下方输入问题，比如「每个月的销售额趋势」\n"
        "3. 展开「Agent 决策链路」可以看到它每一步在想什么"
    )
    st.markdown("**技术栈**：`Python 标准库 SQLite` · `Streamlit` · `OpenAI 兼容 API` · `ReAct + Verifier`")
    st.stop()

st.markdown(
    f"当前数据：**{len(db.tables)} 张表** ｜ "
    + " · ".join(f"`{t}`" for t in db.tables)
)

# 示例问题
if not st.session_state.messages:
    cols = st.columns(4)
    for col, q in zip(cols, SAMPLES):
        if col.button(q, use_container_width=True, key=f"s_{q}"):
            st.session_state.messages.append({"role": "user", "content": q})
            st.rerun()

# 历史
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            render_result(m["result"])

# 输入
question = st.chat_input("问点什么，比如：退款率最高的三个商品")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    st.rerun()

# 生成回答
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    pending = st.session_state.messages[-1]["content"]
    with st.chat_message("assistant"):
        if not get_key():
            st.error("未配置 API Key，无法生成 SQL。请在左侧填写。")
            st.session_state.messages.pop()
        else:
            with st.spinner("Agent 正在分析…"):
                try:
                    agent = DataAgent(db, LLM(key=get_key()), max_retry=3)
                    res = agent.ask(pending)
                    render_result(res)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": res.summary, "result": res}
                    )
                except LLMError as e:
                    st.error(f"模型调用失败：{e}")
                    st.session_state.messages.pop()
                except Exception as e:
                    st.error(f"未预期的错误：{type(e).__name__}: {e}")
                    st.session_state.messages.pop()
