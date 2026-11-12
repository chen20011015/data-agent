# -*- coding: utf-8 -*-
"""生成演示数据：电商订单表 + 商品表。

真实感要点：
1. 有季节性（双11、618 订单量大）
2. 有退货（status 含 refunded）
3. 有零成交（数量为 0 的异常单）
4. 地区分布不均
5. 商品有成本价，可以算毛利
"""

import csv
import os
import random
from datetime import date, timedelta

random.seed(20261112)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "demo_data")
os.makedirs(OUT, exist_ok=True)

PRODUCTS = [
    ("P001", "无线蓝牙耳机", "数码配件", 89.0, 199.0),
    ("P002", "机械键盘 87 键", "数码配件", 156.0, 329.0),
    ("P003", "人体工学鼠标", "数码配件", 68.0, 149.0),
    ("P004", "桌面显示器支架", "办公用品", 112.0, 259.0),
    ("P005", "USB-C 扩展坞", "数码配件", 143.0, 299.0),
    ("P006", "升降办公桌", "办公家具", 680.0, 1499.0),
    ("P007", "护腰办公椅", "办公家具", 520.0, 1199.0),
    ("P008", "桌面收纳架", "办公用品", 35.0, 89.0),
    ("P009", "降噪头戴耳机", "数码配件", 320.0, 699.0),
    ("P010", "便携显示器 15.6", "显示设备", 760.0, 1580.0),
    ("P011", "笔记本散热底座", "数码配件", 42.0, 99.0),
    ("P012", "台灯护眼版", "办公用品", 88.0, 189.0),
]

REGIONS = [
    ("华东", 0.38), ("华南", 0.22), ("华北", 0.16),
    ("华中", 0.10), ("西南", 0.08), ("东北", 0.06),
]

STATUS_WEIGHTS = [("completed", 0.86), ("refunded", 0.09), ("cancelled", 0.05)]


def weighted(items):
    names = [x[0] for x in items]
    weights = [x[1] for x in items]
    return random.choices(names, weights=weights, k=1)[0]


def season_factor(d: date) -> float:
    """双11、618、年货节订单量放大。"""
    if d.month == 11 and d.day <= 15:
        return 2.8
    if d.month == 6 and 1 <= d.day <= 20:
        return 2.2
    if d.month == 1 and d.day <= 20:
        return 1.6
    if d.month == 12 and d.day >= 20:
        return 1.4
    return 1.0


# ---------------- 商品表 ----------------
with open(os.path.join(OUT, "products.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["product_id", "product_name", "category", "cost_price", "list_price"])
    for pid, name, cat, cost, price in PRODUCTS:
        w.writerow([pid, name, cat, cost, price])

# ---------------- 订单表 ----------------
start = date(2025, 1, 1)
end = date(2026, 10, 31)
total_days = (end - start).days

rows = []
order_no = 100000

day = start
while day <= end:
    base_count = random.randint(2, 5)
    count = int(base_count * season_factor(day))
    for _ in range(count):
        pid, name, cat, cost, price = random.choice(PRODUCTS)
        qty = random.choices([1, 1, 1, 2, 2, 3, 5], k=1)[0]

        # 5% 的异常单：数量为 0（下错单）
        if random.random() < 0.05:
            qty = 0

        # 单价有促销折扣
        discount = random.choice([1.0, 1.0, 1.0, 0.9, 0.85, 0.8])
        unit_price = round(price * discount, 2)
        amount = round(unit_price * qty, 2)

        status = weighted(STATUS_WEIGHTS)
        region = weighted(REGIONS)

        order_no += 1
        rows.append([
            f"ORD{order_no}",
            day.isoformat(),
            pid,
            name,
            cat,
            qty,
            unit_price,
            amount,
            round(cost * qty, 2),   # 成本，用于算毛利
            status,
            region,
            random.choice(["APP", "小程序", "PC", "直播间"]),
        ])
    day += timedelta(days=1)

with open(os.path.join(OUT, "orders.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow([
        "order_id", "order_date", "product_id", "product_name", "category",
        "quantity", "unit_price", "amount", "cost", "status", "region", "channel",
    ])
    w.writerows(rows)

print(f"products.csv: {len(PRODUCTS)} 行")
print(f"orders.csv:   {len(rows)} 行")
print(f"日期范围:     {start} ~ {end}")
print(f"输出目录:     {OUT}")
