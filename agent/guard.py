# -*- coding: utf-8 -*-
"""SQL 安全校验层。

Agent 生成的 SQL 不能直接执行——模型偶尔会写出 DROP TABLE，
或者在被诱导时生成越权语句。这一层是硬闸门，不依赖模型自觉。
"""

from __future__ import annotations

import re

# 只读白名单：语句必须以 SELECT 或 WITH 开头
_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

# 黑名单关键字（出现在任何位置都拒绝）
_FORBIDDEN = [
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex", "truncate",
    "grant", "revoke", "load_extension",
]

# 注释：-- 和 /* */ 都可能用来藏语句
_COMMENT = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.S)


class SqlGuardError(ValueError):
    """SQL 未通过安全校验。"""


def _strip_strings(sql: str) -> str:
    """把字符串字面量替换成占位，避免把 'drop' 这种值误判成关键字。"""
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def check_sql(sql: str) -> str:
    """校验只读 SQL，返回清理后的语句；不通过则抛 SqlGuardError。"""
    if not sql or not sql.strip():
        raise SqlGuardError("SQL 为空")

    s = sql.strip().rstrip(";").strip()

    # 1) 必须单条语句（分号只能出现在末尾）
    if ";" in s:
        raise SqlGuardError("只允许单条 SQL 语句，检测到分号")

    # 2) 去注释后检查
    if _COMMENT.search(s):
        raise SqlGuardError("SQL 中不允许包含注释")

    # 3) 必须以 SELECT / WITH 开头
    if not _ALLOWED_START.match(s):
        raise SqlGuardError("只允许 SELECT 查询")

    # 4) 黑名单关键字（先剥掉字符串字面量，避免误判）
    plain = _strip_strings(s).lower()
    for kw in _FORBIDDEN:
        if re.search(rf"\b{kw}\b", plain):
            raise SqlGuardError(f"检测到禁止的关键字：{kw.upper()}")

    # 5) 括号配平
    if s.count("(") != s.count(")"):
        raise SqlGuardError("括号不匹配")

    return s


def explain_reject(sql: str) -> str:
    """给模型的修正提示（人类可读）。"""
    try:
        check_sql(sql)
        return ""
    except SqlGuardError as e:
        return str(e)
