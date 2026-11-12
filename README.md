# 数据分析 Agent

上传 CSV，用大白话提问，Agent 自己写 SQL、自己审查、自己检查结果、失败了自己改。

**这不是 Text-to-SQL 脚本。** 普通脚本是"问题 → 生成 SQL → 执行 → 返回"，模型写错了就直接报错给你。这个项目在生成和执行之间加了四道关卡，任何一道不通过都会打回重来。

---

## 为什么叫 Agent 而不是 Text-to-SQL

|      | Text-to-SQL 脚本 | 本项目                    |
| ---- | -------------- | ---------------------- |
| 生成   | 一次             | 最多 3 次，每次带上次的错误信息      |
| 自审   | 无              | 模型审查自己写的 SQL 是否符合问题意图  |
| 安全   | 无              | 硬闸门拦截 DROP/DELETE/注释注入 |
| 结果检查 | 无              | 空结果、整列 null、数值异常都会触发重试 |
| 过程   | 不可见            | 每一步的思考、SQL、错误、重试原因全部可见 |

链路上任何一步失败，都会带着"为什么失败"回到生成环节，而不是把错误抛给用户。

---

## 执行链路

```
用户问题
   │
   ├─ 1. 生成 SQL         带上表结构、枚举值、样例行
   │
   ├─ 2. 自审（LLM）      字段对吗？过滤条件对吗？聚合方式对吗？
   │       └─ 不通过 ──→ 直接采用模型给的修正版
   │
   ├─ 3. 安全校验（硬闸门）
   │       ├─ 必须单条 SELECT / WITH
   │       ├─ 黑名单：DROP DELETE UPDATE INSERT ALTER ATTACH PRAGMA ...
   │       ├─ 禁止注释（-- 与 /* */ 都可能藏语句）
   │       ├─ 字符串字面量先剥离，避免 'drop' 这种值被误判
   │       └─ 不通过 ──→ 打回重写
   │
   ├─ 4. 执行（SQLite 只读）
   │       └─ 语法/列名错误 ──→ 带着报错原文打回重写
   │
   ├─ 5. 结果检查
   │       ├─ 0 行 → 提示检查筛选条件与枚举值（连续两次才判定为真实无数据）
   │       ├─ 整列 null → 疑似 JOIN 或列名错误
   │       └─ 数值列全为 0 → 疑似计算逻辑错误
   │
   └─ 6. 生成结论（带真实数字，不允许编造）
```

---

## 自测（不需要 API Key）

```bash
python make_demo_data.py     # 生成演示数据：2632 条订单 + 12 个商品
python test_agent.py         # 40 项断言，全部用 Mock 模型，零成本
```

覆盖范围：

- 数据加载与类型推断（金额能求和、日期能比较）
- 安全校验（8 种攻击 SQL 全拦、4 种正常查询放行、字符串内关键字不误伤）
- 正常路径（链路完整、无多余重试）
- 写错列名 → 重试 → 修正
- 模型生成 `DROP TABLE` → 拦截 → 重试 → 表完好无损
- 自审否决 → 采用修正版 SQL
- 空结果不崩溃

---

## 本地运行

```bash
pip install -r requirements.txt

# 配置 Key（智谱 GLM-4-Flash 免费）
export API_KEY="你的key"          # macOS / Linux
$env:API_KEY="你的key"            # Windows PowerShell

streamlit run app.py
```

打开 <http://localhost:8501，上传> `demo_data/orders.csv` 和 `products.csv`。

也可以直接拖任意 CSV 进去，文件名就是表名。

## 部署到 Streamlit Cloud

1. 推到 GitHub
2. [share.streamlit.io](https://share.streamlit.io) → New app → 选仓库 → Main file 填 `app.py`
3. App settings → Secrets：

```toml
API_KEY = "你的key"
```

---

## 目录结构

```
data-agent/
├── agent/
│   ├── db.py        # SQLite 封装：CSV 载入、类型推断、schema 描述
│   ├── guard.py     # SQL 安全校验（硬闸门，不依赖模型自觉）
│   ├── llm.py       # OpenAI 兼容客户端（纯标准库 urllib）
│   └── core.py      # Agent 循环：生成 → 自审 → 校验 → 执行 → 检查 → 重试
├── demo_data/       # 演示数据
├── make_demo_data.py
├── test_agent.py    # 40 项自测，无需 Key
├── app.py           # Streamlit 前端
└── requirements.txt
```

**核心逻辑零第三方依赖**——只用 `sqlite3` + `urllib` + `json`。前端才需要 Streamlit。

---

## 已知限制

- **表规模**：内存 SQLite，适合万级以内。再大要换 DuckDB 或接数据仓库
- **schema 超长**：表特别多时会超出上下文，目前没有做 schema 检索筛选
- **多轮追问**：当前每轮独立，还不会带上历史 SQL 做增量修改
- **比率类问题**：分母为 0 的情况靠 Prompt 提醒模型用 NULLIF，没有强校验

## 后续计划

- [ ] 多轮追问：带上上一轮 SQL 做增量修改
- [ ] 图表自动化：按问题类型选择折线/柱状/占比图，而不是统一柱状
- [ ] schema 检索：表多时先用向量检索筛出相关表再喂给模型
- [ ] 评测集：50 条问题-SQL 对，统计首次生成正确率与重试收敛率

---

## License

MIT
