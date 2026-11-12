# -*- coding: utf-8 -*-
"""Agent 核心：生成 → 自审 → 安全校验 → 执行 → 结果检查 → 重试。

这是"Agent"和"Text-to-SQL 脚本"的区别所在：
不是把问题丢给模型生成 SQL 就执行，而是每一步都可被否决、可被打回重来。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from .db import Database, QueryResult
from .guard import SqlGuardError, check_sql
from .llm import LLM, LLMError


# ---------------------------------------------------------------- 数据结构


@dataclass
class Trace:
    """一步执行记录，前端用来还原 Agent 的思考过程。"""
    step: int
    phase: str          # generate / review / guard / execute / check / retry / summarize
    title: str
    detail: str
    ok: bool = True
    data: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    ok: bool
    question: str
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    retries: int = 0
    trace: list[Trace] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


# ---------------------------------------------------------------- 提示词

SQL_PROMPT = """你是 SQLite 数据分析师。根据表结构和用户问题，写出一条 SELECT 查询。

规则：
1. 只输出 SQLite 语法的 SELECT 语句，不要输出解释文字。
2. 只用表结构里存在的表和列，不要编造。
3. 日期字段是 TEXT 格式 'YYYY-MM-DD'，用 strftime 或 substr 处理。
4. 金额、数量等数值列已是数值类型，直接参与计算。
5. 需要按时间聚合时用 strftime('%Y-%m', order_date)。
6. 结果列用 AS 起可读别名。
7. 除非用户明确要明细，否则优先聚合，避免返回几百行。
8. 计算比率时注意分母为 0，用 NULLIF 或 CASE 保护。
9. 粒度要跟问题一致：用户问某个类别 / 某个地区的汇总指标，就直接给一个汇总数，
   不要额外按月份拆开。只有用户明确提到"趋势""每月""各月"时才按月分组。
10. 用户没说时间范围时，默认统计全部数据，不要自己加时间过滤。

表结构：
{schema}
"""

REVIEW_PROMPT = """你是 SQL 审查员。检查这条 SQL 是否真的回答了用户的问题。

用户问题：{question}

待审查 SQL：
{sql}

逐项检查：
1. 选的字段是否对应问题关心的指标
2. 过滤条件（时间范围、状态、地区）是否符合问题
3. 聚合函数与分组是否正确（例如"每月"必须有按月分组）
4. 比率型指标的分母是否正确
5. 是否遗漏了问题里提到的限定条件

只输出 JSON，不要其他内容：
{{"ok": true 或 false, "issue": "发现的问题，没有问题就空字符串", "fixed_sql": "修正后的完整 SQL，如果 ok 为 true 就填空字符串"}}
"""

FIX_PROMPT = """你上一次写的 SQL 没能通过检查，请修正。

用户问题：{question}

上一次的 SQL：
{sql}

问题原因：
{error}

表结构：
{schema}

请只输出修正后的 SELECT 语句，不要解释。
"""

SUMMARY_PROMPT = """用中文简洁回答用户问题，基于给定查询结果。

用户问题：{question}
执行的 SQL：{sql}
查询结果（最多 20 行）：{data}
总行数：{count}

要求：
1. 结果不超过 5 行时，可以逐项列出关键数字。
2. 结果超过 5 行时，绝对不要罗列所有数字。只总结三件事：整体趋势（上升/下降/平稳）、
   最大值和最小值分别是哪个及其数值、有没有明显异常的月份或项。
3. 涉及比率给百分比，保留一位小数。
4. 结果为空时直说"没有符合条件的数据"，并说明可能原因。
5. 只能使用结果里出现过的数字，不得编造。
6. 不超过 120 字。
"""


class DataAgent:
    def __init__(self, db: Database, llm: LLM, max_retry: int = 3):
        self.db = db
        self.llm = llm
        self.max_retry = max_retry
        self._review_fixed_sql = ""

    # ---------------------------------------------------------------- 主流程

    def ask(self, question: str) -> AgentResult:
        question = (question or "").strip()
        if not question:
            return AgentResult(ok=False, question=question, error="问题为空")

        result = AgentResult(ok=False, question=question)
        schema = self.db.schema_text()
        step = 0
        sql = ""
        last_error = ""
        empty_seen = 0

        def log(phase, title, detail, ok=True, data=None):
            nonlocal step
            step += 1
            result.trace.append(
                Trace(step=step, phase=phase, title=title, detail=detail,
                      ok=ok, data=data or {})
            )

        for attempt in range(1, self.max_retry + 1):
            if attempt > 1:
                result.retries = attempt - 1

            # ---------------- 1. 生成 SQL ----------------
            try:
                if attempt == 1:
                    raw = self.llm.chat([
                        {"role": "system", "content": "你只输出 SQL，不输出其他内容。"},
                        {"role": "user", "content": SQL_PROMPT.format(schema=schema)},
                        {"role": "user", "content": f"用户问题：{question}"},
                    ], temperature=0.0)
                    log("generate", f"第 {attempt} 次生成 SQL", "根据表结构与问题生成查询")
                else:
                    raw = self.llm.chat([
                        {"role": "system", "content": "你只输出 SQL，不输出其他内容。"},
                        {"role": "user", "content": FIX_PROMPT.format(
                            question=question, sql=sql, error=last_error, schema=schema)},
                    ], temperature=0.0)
                    log("retry", f"第 {attempt} 次重试", f"原因：{last_error}")
            except LLMError as e:
                result.error = f"生成 SQL 失败：{e}"
                log("generate", "生成失败", result.error, ok=False)
                return result

            sql = LLM.extract_sql(raw)
            log("generate", "生成的 SQL", sql, data={"sql": sql})

            # ---------------- 2. 模型自审 ----------------
            if attempt == 1:
                reviewed = self._review(question, sql)
                if reviewed:
                    log("review", "自审发现问题", reviewed, ok=False)
                    last_error = f"自审未通过：{reviewed}"
                    fixed = self._review_fixed_sql
                    if fixed:
                        sql = fixed
                        log("review", "已采用修正版 SQL", sql, data={"sql": sql})
                else:
                    log("review", "自审通过", "字段、过滤条件、聚合方式与问题一致")

            # ---------------- 3. 安全校验 ----------------
            try:
                safe_sql = check_sql(sql)
                log("guard", "安全校验通过", "单条 SELECT，无写操作，无注释")
            except SqlGuardError as e:
                last_error = f"SQL 未通过安全校验：{e}。请重写为单条 SELECT 查询。"
                log("guard", "安全校验拦截", str(e), ok=False)
                continue

            # ---------------- 4. 执行 ----------------
            try:
                qr: QueryResult = self.db.query(safe_sql)
                log("execute", "查询执行成功",
                    f"返回 {qr.row_count} 行，{len(qr.columns)} 列",
                    data={"columns": qr.columns, "row_count": qr.row_count})
            except sqlite3.Error as e:
                # 关键：把可用表清单一起喂回去。
                # 模型经常幻觉出不存在的表（比如把 orders.region 当成 regions 表），
                # 光说 "no such table" 它会继续犯，必须明确告诉它只有哪些表。
                tables = "、".join(self.db.tables.keys())
                last_error = (
                    f"SQL 执行报错：{e}。\n"
                    f"数据库里只有这些表：{tables}，不存在其他表。\n"
                    f"请检查是否把某个列误当成了表名。"
                )
                log("execute", "查询执行失败", f"{e}（已回传可用表清单）", ok=False)
                continue

            # ---------------- 5. 结果检查 ----------------
            problem = self._check_result(qr, question)
            if problem:
                empty_seen += 1
                log("check", "结果异常", problem, ok=False)
                # 空结果可能是问题本身无数据，重试一次后接受
                if qr.row_count == 0 and empty_seen >= 2:
                    log("check", "空结果判定为真实情况", "连续两次查询为空，视为无匹配数据")
                else:
                    last_error = problem
                    continue
            else:
                log("check", "结果检查通过", "行数、列值均正常")

            # ---------------- 6. 总结 ----------------
            result.sql = safe_sql
            result.columns = qr.columns
            result.rows = qr.rows
            result.ok = True
            result.summary = self._summarize(question, safe_sql, qr)
            log("summarize", "生成结论", result.summary)
            return result

        result.sql = sql
        result.error = last_error or "多次尝试仍未得到可用结果"
        log("retry", "已达最大重试次数", result.error, ok=False)
        return result

    # ---------------------------------------------------------------- 子步骤

    def _review(self, question: str, sql: str) -> str:
        """让模型审查自己写的 SQL，返回问题描述；空串表示通过。

        结果存入 _review_fixed_sql 供调用方取用。
        """
        self._review_fixed_sql = ""
        try:
            raw = self.llm.chat([
                {"role": "system", "content": "你只输出 JSON，不输出其他内容。"},
                {"role": "user", "content": REVIEW_PROMPT.format(question=question, sql=sql)},
            ], temperature=0.0, max_tokens=800)
        except LLMError:
            # 自审失败不阻断主流程，当作通过
            return ""

        obj = self._parse_json(raw)
        if not obj:
            return ""
        if obj.get("ok") is True:
            return ""
        self._review_fixed_sql = str(obj.get("fixed_sql") or "").strip()
        return str(obj.get("issue") or "自审未通过").strip()

    @staticmethod
    def _parse_json(text: str) -> dict:
        if not text:
            return {}
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
        raw = m.group(1) if m else text
        # 退一步：抓第一个完整大括号
        if not raw.strip().startswith("{"):
            m2 = re.search(r"\{.*\}", raw, re.S)
            if not m2:
                return {}
            raw = m2.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _check_result(self, qr: QueryResult, question: str) -> str:
        """结果合理性检查。返回问题描述，空串表示正常。"""
        if qr.row_count == 0:
            return ("查询返回 0 行。请检查：筛选条件是否过严、日期范围是否正确、"
                    "枚举值是否写错（例如状态值是 completed 而不是 complete）")

        # 整列全空：通常是 JOIN 错了或列名错
        ncol = len(qr.columns)
        for i in range(ncol):
            if all(r[i] is None for r in qr.rows):
                return f"第 {i + 1} 列（{qr.columns[i]}）全部为空，可能是关联条件或列名错误"

        # 数值列出现异常量级
        if qr.row_count >= 3:
            for i, col in enumerate(qr.columns):
                vals = [r[i] for r in qr.rows if isinstance(r[i], (int, float))]
                if len(vals) < 3:
                    continue
                if all(v == 0 for v in vals):
                    return f"列 {col} 全部为 0，可能是计算逻辑有误"
        return ""

    def _summarize(self, question: str, sql: str, qr: QueryResult) -> str:
        preview = [dict(zip(qr.columns, r)) for r in qr.rows[:20]]
        try:
            text = self.llm.chat([
                {"role": "system", "content": "你是数据分析师，回答简洁准确。"},
                {"role": "user", "content": SUMMARY_PROMPT.format(
                    question=question, sql=sql,
                    data=json.dumps(preview, ensure_ascii=False, default=str),
                    count=qr.row_count)},
            ], temperature=0.2, max_tokens=500)
            return text.strip()
        except LLMError:
            return f"查询返回 {qr.row_count} 行结果，共 {len(qr.columns)} 列。"
