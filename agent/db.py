# -*- coding: utf-8 -*-
"""SQLite 封装：把 CSV 载入内存库，提供 schema 描述与只读查询。

只用标准库，不依赖 duckdb / pandas，保证任何环境都能跑起来。
连接使用 check_same_thread=False 配合锁，因为 HTTP 服务是多线程的。
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import threading
from dataclasses import dataclass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    sql: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _infer_type(values: list[str]) -> str:
    """根据样本推断列类型。"""
    nums = 0
    non_empty = 0
    for v in values:
        if v is None or str(v).strip() == "":
            continue
        non_empty += 1
        try:
            float(v)
            nums += 1
        except (TypeError, ValueError):
            pass
    if non_empty == 0:
        return "TEXT"
    return "REAL" if nums / non_empty > 0.9 else "TEXT"


def _safe_ident(name: str) -> str:
    """表名/列名加双引号，防止注入与保留字冲突。"""
    return '"' + str(name).replace('"', '""') + '"'


class Database:
    """内存 SQLite，载入 CSV 供 Agent 查询。"""

    def __init__(self, conn: sqlite3.Connection | None = None):
        # HTTP 服务每个请求跑在独立线程，必须允许跨线程共享同一个连接，
        # 再用 RLock 把访问串行化，避免并发破坏连接状态。
        self.conn = conn or sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.tables: dict[str, list[dict]] = {}

    # ---------------------------------------------------------------- 载入

    def load_csv(self, table: str, text: str) -> int:
        """把 CSV 文本载入为表，返回行数。"""
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        records = [dict(r) for r in reader]
        if not records:
            raise ValueError(f"CSV 为空：{table}")

        headers = [h for h in records[0].keys() if h is not None and str(h).strip()]
        if not headers:
            raise ValueError(f"CSV 表头为空：{table}")

        col_types = {}
        for h in headers:
            sample = [str(r.get(h, "")) for r in records[:200]]
            col_types[h] = _infer_type(sample)

        cols_sql = ", ".join(f"{_safe_ident(h)} {col_types[h]}" for h in headers)
        placeholders = ", ".join("?" for _ in headers)
        insert_sql = f"INSERT INTO {_safe_ident(table)} VALUES ({placeholders})"

        cleaned = []
        for r in records:
            row = []
            for h in headers:
                v = r.get(h, "")
                if v is None or str(v).strip() == "":
                    row.append(None)
                elif col_types[h] == "REAL":
                    try:
                        row.append(float(v))
                    except ValueError:
                        row.append(None)
                else:
                    row.append(str(v).strip())
            cleaned.append(row)

        with self._lock:
            cur = self.conn.cursor()
            cur.execute(f"DROP TABLE IF EXISTS {_safe_ident(table)}")
            cur.execute(f"CREATE TABLE {_safe_ident(table)} ({cols_sql})")
            cur.executemany(insert_sql, cleaned)
            self.conn.commit()

        self.tables[table] = records
        return len(records)

    def load_csv_file(self, table: str, path: str) -> int:
        with open(path, encoding="utf-8-sig") as f:
            return self.load_csv(table, f.read())

    # ---------------------------------------------------------------- 描述

    def schema_text(self, sample_rows: int = 3) -> str:
        """给 LLM 看的库结构：建表语句 + 样例行 + 行数 + 低基数列取值。"""
        with self._lock:
            lines: list[str] = []
            names = "、".join(self.tables.keys())
            lines.append(f"-- 本数据库只有以下 {len(self.tables)} 张表：{names}。不存在其他表。")
            lines.append("-- 凡是表结构里没出现的名字，都只能是列，不能当表用。")
            lines.append("")

            for table in self.tables:
                cur = self.conn.execute(f"SELECT * FROM {_safe_ident(table)} LIMIT 0")
                col_names = [d[0] for d in cur.description]
                col_types = [d[1] for d in cur.description]

                n = self.conn.execute(
                    f"SELECT COUNT(*) FROM {_safe_ident(table)}"
                ).fetchone()[0]

                lines.append(f"-- 表 {table}（共 {n} 行）")
                cols = ", ".join(f"{c} {t}" for c, t in zip(col_names, col_types))
                lines.append(f"CREATE TABLE {table} ({cols});")

                rows = self.conn.execute(
                    f"SELECT * FROM {_safe_ident(table)} LIMIT {sample_rows}"
                ).fetchall()
                if rows:
                    lines.append("-- 样例数据：")
                    lines.append("-- " + " | ".join(col_names))
                    for r in rows:
                        lines.append("-- " + " | ".join(str(x) for x in r))

                for c in col_names:
                    try:
                        vals = self.conn.execute(
                            f"SELECT DISTINCT {_safe_ident(c)} FROM {_safe_ident(table)} "
                            f"WHERE {_safe_ident(c)} IS NOT NULL LIMIT 8"
                        ).fetchall()
                    except sqlite3.Error:
                        continue
                    if 1 < len(vals) <= 8:
                        joined = ", ".join(str(v[0]) for v in vals)
                        lines.append(f"-- {table}.{c} 常见取值：{joined}")
                lines.append("")
            return "\n".join(lines)

    # ---------------------------------------------------------------- 查询

    def query(self, sql: str, max_rows: int = 500) -> QueryResult:
        """只读执行。调用方应先用 guard 校验 SQL。"""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows)
            return QueryResult(columns=cols, rows=[tuple(r) for r in rows], sql=sql)

    # ---------------------------------------------------------------- 元信息

    def all_columns(self) -> dict[str, list[str]]:
        out = {}
        with self._lock:
            for table in self.tables:
                cur = self.conn.execute(f"SELECT * FROM {_safe_ident(table)} LIMIT 0")
                out[table] = [d[0] for d in cur.description]
        return out
