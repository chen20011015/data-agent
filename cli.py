# -*- coding: utf-8 -*-
"""命令行调试入口：在本机直接试真实模型，不依赖 Streamlit。

用法：
    python cli.py --key 你的Key
    python cli.py                      # 读环境变量 API_KEY

进入后直接输入问题，回车提问。
    :sql    查看上一次执行的 SQL
    :trace  重看上一次的完整链路
    :q      退出
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import DataAgent, Database, LLM, LLMError  # noqa: E402

PHASE_LABEL = {
    "generate": "生成", "review": "自审", "guard": "校验",
    "execute": "执行", "check": "检查", "retry": "重试", "summarize": "总结",
}


# ---------------------------------------------------------------- 排版工具

def wcwidth(s: str) -> int:
    """计算显示宽度，中文算 2。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - wcwidth(s))


def cut(s: str, width: int) -> str:
    """按显示宽度截断。"""
    s = str(s)
    out, w = "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width:
            return out + "…"
        out += ch
        w += cw
    return out


def print_table(columns, rows, max_rows=20):
    if not rows:
        print("  （无数据）")
        return
    shown = rows[:max_rows]
    cells = [[cut(c, 22) for c in columns]]
    for r in shown:
        cells.append([cut("NULL" if v is None else v, 22) for v in r])
    widths = [max(wcwidth(row[i]) for row in cells) for i in range(len(columns))]

    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print("  " + line)
    print("  |" + "|".join(" " + pad(cells[0][i], widths[i]) + " " for i in range(len(columns))) + "|")
    print("  " + line)
    for row in cells[1:]:
        print("  |" + "|".join(" " + pad(row[i], widths[i]) + " " for i in range(len(columns))) + "|")
    print("  " + line)
    if len(rows) > max_rows:
        print(f"  … 共 {len(rows)} 行，仅显示前 {max_rows} 行")


def print_trace(trace):
    for t in trace:
        mark = "[OK]" if t.ok else "[!!]"
        phase = PHASE_LABEL.get(t.phase, t.phase)
        print(f"  {t.step:>2}. {pad(phase, 4)} {mark}  {t.title}")
        if t.detail:
            for ln in str(t.detail).splitlines()[:6]:
                print(f"        {cut(ln, 96)}")


# ---------------------------------------------------------------- 主程序

def main():
    ap = argparse.ArgumentParser(description="数据分析 Agent 命令行调试")
    ap.add_argument("--key", default="", help="API Key，留空则读环境变量 API_KEY")
    ap.add_argument("--model", default="", help="模型名，默认 glm-4-flash")
    ap.add_argument("--base", default="", help="接口地址，默认智谱")
    ap.add_argument("--data", default="demo_data", help="数据目录")
    ap.add_argument("--retry", type=int, default=3, help="最大重试次数")
    args = ap.parse_args()

    key = (args.key or os.getenv("API_KEY", "") or os.getenv("CHAT_API_KEY", "")).strip()
    if not key:
        print("未提供 API Key。三种方式任选：")
        print("  1. python cli.py --key 你的真实Key")
        print('  2. PowerShell:  $env:API_KEY="你的真实Key"')
        print("  3. Git Bash:    export API_KEY=你的真实Key")
        print()
        print("Key 在 https://open.bigmodel.cn/ 登录后，左侧 API Keys 新建，复制完整字符串。")
        sys.exit(1)
    if not key.isascii():
        print("Key 里含中文，说明你把「你的真实Key」这几个字原样敲进去了。")
        print("要把整串占位符替换成从网站复制的真实字符串，形如：")
        print("  5cd6f536b43c4f5b898d5c4c82cee7c3.LejOdxx4GZpL5lbO")
        sys.exit(1)
    if len(key) < 20:
        print(f"Key 只有 {len(key)} 位，明显不完整。请回网站重新复制。")
        sys.exit(1)

    # 载入数据
    db = Database()
    data_dir = args.data
    loaded = []
    if os.path.isdir(data_dir):
        for fn in sorted(os.listdir(data_dir)):
            if not fn.lower().endswith(".csv"):
                continue
            table = os.path.splitext(fn)[0].replace(" ", "_")
            n = db.load_csv_file(table, os.path.join(data_dir, fn))
            loaded.append((table, n))
    if not loaded:
        print(f"在 {data_dir} 下没有找到 CSV 文件")
        sys.exit(1)

    llm = LLM(base=args.base or None, key=key, model=args.model or None)
    agent = DataAgent(db, llm, max_retry=args.retry)

    print("=" * 74)
    print(" 数据分析 Agent · 命令行调试")
    print("=" * 74)
    print(f"  模型    {llm.model}")
    print(f"  接口    {llm.base}")
    for t, n in loaded:
        print(f"  数据    {t}（{n} 行）")
    print()
    print("  直接输入问题提问；:sql 看上次 SQL；:trace 重看链路；:q 退出")
    print("=" * 74)

    last = None
    while True:
        try:
            q = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not q:
            continue
        if q in (":q", ":quit", "exit", "quit"):
            print("再见。")
            break
        if q == ":sql":
            print(last.sql if last and last.sql else "  还没有执行过查询")
            continue
        if q == ":trace":
            if last:
                print_trace(last.trace)
            else:
                print("  还没有执行过查询")
            continue

        print()
        try:
            res = agent.ask(q)
        except LLMError as e:
            print(f"  模型调用失败：{e}")
            continue
        except Exception as e:
            print(f"  未预期错误：{type(e).__name__}: {e}")
            continue

        last = res
        print_trace(res.trace)

        print()
        print("-" * 74)
        if res.ok:
            print(f"  结论：{res.summary}")
            if res.retries:
                print(f"  （共重试 {res.retries} 次）")
            print()
            print_table(res.columns, res.rows)
        else:
            print(f"  失败：{res.error}")
            if res.sql:
                print(f"  最后生成的 SQL：{res.sql}")
        print("-" * 74)


if __name__ == "__main__":
    main()
