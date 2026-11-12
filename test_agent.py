# -*- coding: utf-8 -*-
"""核心逻辑自测：不依赖任何 API Key，用 Mock 模型验证 Agent 循环。

重点验证三件事：
1. 正常路径能跑通
2. SQL 出错时能重试并自我修正
3. 危险 SQL 会被拦在门外
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.core import DataAgent           # noqa: E402
from agent.db import Database              # noqa: E402
from agent.guard import SqlGuardError, check_sql   # noqa: E402
from agent.llm import LLM                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  |  {detail}" if detail else ""))


class MockLLM:
    """按脚本返回预设内容，用来模拟模型的不同反应。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, temperature=0.1, max_tokens=2000):
        self.calls += 1
        if not self.script:
            return "SELECT 1"
        return self.script.pop(0)

    extract_sql = staticmethod(LLM.extract_sql)


# ============================================================ 1. 数据加载
print("=" * 66)
print("1. 数据加载与结构描述")

db = Database()
n1 = db.load_csv_file("orders", "demo_data/orders.csv")
n2 = db.load_csv_file("products", "demo_data/products.csv")
check("orders 载入", n1 > 1000, f"{n1} 行")
check("products 载入", n2 == 12, f"{n2} 行")

schema = db.schema_text()
check("schema 含建表语句", "CREATE TABLE orders" in schema)
check("schema 含行数", "共" in schema)
check("schema 含枚举值提示", "常见取值" in schema)
check("schema 含样例行", "样例数据" in schema)

# 数值列必须推断成数字，否则 SUM 会出错
r = db.query("SELECT SUM(amount) AS s FROM orders")
check("金额列可求和", isinstance(r.rows[0][0], float) and r.rows[0][0] > 0,
      f"总额 {r.rows[0][0]:,.2f}")

r = db.query("SELECT COUNT(*) FROM orders WHERE order_date >= '2026-01-01'")
check("日期字符串可比较", r.rows[0][0] > 0, f"2026 年 {r.rows[0][0]} 单")

# ============================================================ 2. 安全校验
print()
print("=" * 66)
print("2. SQL 安全校验")

dangerous = [
    ("DROP TABLE orders", "删表"),
    ("DELETE FROM orders", "删数据"),
    ("UPDATE orders SET amount=0", "改数据"),
    ("INSERT INTO orders VALUES (1)", "插数据"),
    ("SELECT 1; DROP TABLE orders", "分号注入"),
    ("SELECT * FROM orders -- 注释", "注释藏语句"),
    ("PRAGMA table_info(orders)", "读取元信息"),
    ("ATTACH DATABASE 'x.db' AS x", "挂载外部库"),
]
for sql, desc in dangerous:
    try:
        check_sql(sql)
        check(f"拦截 {desc}", False, "未被拦截")
    except SqlGuardError:
        check(f"拦截 {desc}", True)

safe = [
    "SELECT COUNT(*) FROM orders",
    "WITH t AS (SELECT 1) SELECT * FROM t",
    "SELECT * FROM orders WHERE status='completed'",
    "SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY 2 DESC",
]
for sql in safe:
    try:
        check_sql(sql)
        check(f"放行正常查询", True, sql[:38])
    except SqlGuardError as e:
        check(f"放行正常查询", False, f"{sql[:30]} 被误拦：{e}")

# 字符串里的关键字不该误伤
try:
    check_sql("SELECT * FROM orders WHERE product_name='drop test'")
    check("字符串内的关键字不误判", True)
except SqlGuardError:
    check("字符串内的关键字不误判", False, "被误判")

# ============================================================ 3. 正常路径
print()
print("=" * 66)
print("3. Agent 正常路径")

good_sql = ("SELECT strftime('%Y-%m', order_date) AS 月份, "
            "ROUND(SUM(amount),2) AS 销售额 FROM orders "
            "GROUP BY 1 ORDER BY 1")
llm = MockLLM([
    good_sql,                                   # 生成
    '{"ok": true, "issue": "", "fixed_sql": ""}',  # 自审
    "2025 年 1 月起销售额逐月增长，2026 年 10 月达到峰值。",  # 总结
])
agent = DataAgent(db, llm)
res = agent.ask("每个月的销售额是多少")

check("执行成功", res.ok, res.error)
check("拿到了结果", res.row_count > 0, f"{res.row_count} 行")
check("列名正确", "月份" in res.columns and "销售额" in res.columns, str(res.columns))
check("有总结", bool(res.summary), res.summary[:40])
check("无重试", res.retries == 0)
check("链路完整", [t.phase for t in res.trace] ==
      ["generate", "generate", "review", "guard", "execute", "check", "summarize"],
      str([t.phase for t in res.trace]))

# ============================================================ 4. 出错重试
print()
print("=" * 66)
print("4. SQL 写错 → 重试 → 修正")

bad_sql = "SELECT strftime('%Y-%m', order_date) AS m, SUM(amounts) FROM orders GROUP BY 1"
llm2 = MockLLM([
    bad_sql,                                    # 第一次：列名写错
    '{"ok": true, "issue": "", "fixed_sql": ""}',
    "SELECT SUM(amount) AS total FROM orders",   # 重试：修正
    "全部订单总金额为 X 元。",
])
agent2 = DataAgent(db, llm2)
res2 = agent2.ask("订单总金额是多少")

check("重试后成功", res2.ok, res2.error)
check("记录了重试次数", res2.retries >= 1, str(res2.retries))
check("重试原因可追溯", any(t.phase == "retry" for t in res2.trace))
check("错误信息含列名提示", any("amounts" in t.detail for t in res2.trace),
      str([t.detail[:40] for t in res2.trace if not t.ok]))

# ============================================================ 5. 危险 SQL 被拦
print()
print("=" * 66)
print("5. 模型生成危险 SQL → 拦截 → 重试")

llm3 = MockLLM([
    "DROP TABLE orders",                         # 模型抽风
    '{"ok": true, "issue": "", "fixed_sql": ""}',
    "SELECT COUNT(*) AS 订单数 FROM orders",
    "共有 X 笔订单。",
])
agent3 = DataAgent(db, llm3)
res3 = agent3.ask("有多少订单")

check("危险 SQL 被拦后仍能完成", res3.ok, res3.error)
check("拦截记录存在", any(t.phase == "guard" and not t.ok for t in res3.trace))
check("表未被删除", db.query("SELECT COUNT(*) FROM orders").rows[0][0] > 1000,
      "orders 表完好")

# ============================================================ 6. 自审否决
print()
print("=" * 66)
print("6. 自审发现问题 → 采用修正版")

llm4 = MockLLM([
    "SELECT COUNT(*) AS c FROM orders",                        # 原答案：算了总数
    '{"ok": false, "issue": "问的是退款订单，缺少 status 过滤",'
    ' "fixed_sql": "SELECT COUNT(*) AS c FROM orders WHERE status=\'refunded\'"}',
    "共有 X 笔退款订单。",
])
agent4 = DataAgent(db, llm4)
res4 = agent4.ask("有多少退款订单")

check("完成", res4.ok, res4.error)
check("自审否决被记录", any(t.phase == "review" and not t.ok for t in res4.trace))
check("采用了修正 SQL", "refunded" in res4.sql.lower(), res4.sql)
check("被否决后直接执行修正版（未额外重试）", res4.retries == 0, str(res4.retries))

# ============================================================ 7. 空结果
print()
print("=" * 66)
print("7. 空结果的容错")

llm5 = MockLLM([
    "SELECT * FROM orders WHERE order_date='1999-01-01'",
    '{"ok": true, "issue": "", "fixed_sql": ""}',
    "SELECT * FROM orders WHERE order_date='1999-01-01'",
    "没有符合条件的数据。",
])
agent5 = DataAgent(db, llm5)
res5 = agent5.ask("1999 年有哪些订单")

check("空结果不崩溃", res5.ok or bool(res5.error), res5.error)
check("有明确交代", res5.row_count == 0 or bool(res5.summary))

# ============================================================ 汇总
print()
print("=" * 66)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  失败:", f)
    sys.exit(1)
