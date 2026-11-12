# -*- coding: utf-8 -*-
"""本地 Web 服务：零依赖（只用标准库），浏览器直接打开就能用。

用途：不装 Streamlit 也能在本机看到 Agent 的完整效果。
启动：python server.py   然后浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import DataAgent, Database, LLM, LLMError  # noqa: E402

PORT = int(os.getenv("PORT", "8000"))
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")

# 全局状态：单用户本地使用，够用
DB = Database()
for fn in sorted(os.listdir(DATA_DIR)):
    if fn.lower().endswith(".csv"):
        DB.load_csv_file(os.path.splitext(fn)[0], os.path.join(DATA_DIR, fn))

API_KEY = os.getenv("API_KEY", "").strip()
MODEL = os.getenv("CHAT_MODEL", "glm-4-flash")


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据分析 Agent</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background:#f6f7f9; color:#1a1d23; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 24px 18px 80px; }
  h1 { font-size: 22px; margin: 0 0 6px; letter-spacing:.3px; }
  .sub { color:#6b7280; font-size:13px; margin-bottom:18px; }
  .bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
         background:#fff; border:1px solid #e5e7eb; border-radius:10px;
         padding:12px 14px; margin-bottom:16px; }
  .tag { font-size:12px; color:#374151; background:#eef2ff; border:1px solid #dbe3ff;
         padding:3px 9px; border-radius:999px; }
  .tag.ok { background:#ecfdf5; border-color:#bbf7d0; color:#065f46; }
  .tag.bad { background:#fef2f2; border-color:#fecaca; color:#991b1b; }
  textarea { width:100%; min-height:64px; padding:12px 14px; font-size:14px;
             border:1px solid #d1d5db; border-radius:10px; resize:vertical;
             font-family:inherit; outline:none; }
  textarea:focus { border-color:#1a5fb4; box-shadow:0 0 0 3px rgba(26,95,180,.1); }
  .row { display:flex; gap:10px; margin-top:10px; }
  button { border:none; border-radius:9px; padding:10px 20px; font-size:14px;
           cursor:pointer; font-family:inherit; transition:.15s; }
  .primary { background:#1a5fb4; color:#fff; }
  .primary:hover { background:#154c94; }
  .primary:disabled { background:#9ca3af; cursor:not-allowed; }
  .ghost { background:#fff; border:1px solid #d1d5db; color:#374151; }
  .samples { display:flex; gap:8px; flex-wrap:wrap; margin:12px 0 20px; }
  .chip { background:#fff; border:1px solid #e5e7eb; border-radius:999px;
          padding:6px 13px; font-size:12.5px; color:#374151; cursor:pointer; }
  .chip:hover { border-color:#1a5fb4; color:#1a5fb4; }
  .card { background:#fff; border:1px solid #e5e7eb; border-radius:12px;
          padding:16px 18px; margin-bottom:16px; }
  .q { font-weight:600; font-size:14.5px; margin-bottom:10px; }
  .ans { font-size:15px; line-height:1.7; color:#111827; margin-bottom:12px; }
  table { width:100%; border-collapse:collapse; font-size:13px; margin:8px 0; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid #f0f1f3; }
  th { background:#fafbfc; font-weight:600; color:#4b5563; font-size:12.5px; }
  tr:hover td { background:#fafbfc; }
  details { margin-top:10px; }
  summary { cursor:pointer; font-size:13px; color:#1a5fb4; user-select:none; }
  .step { display:flex; gap:9px; padding:6px 0; border-bottom:1px dashed #eef0f2;
          font-size:12.5px; line-height:1.55; }
  .step:last-child { border-bottom:none; }
  .n { flex:0 0 20px; color:#9ca3af; }
  .ph { flex:0 0 34px; color:#6b7280; }
  .mk { flex:0 0 38px; font-weight:700; font-size:11.5px; }
  .ok { color:#059669; } .bad { color:#dc2626; }
  .dt { flex:1; color:#374151; word-break:break-word; }
  .dt b { color:#111827; }
  pre { background:#0f172a; color:#e2e8f0; padding:12px 14px; border-radius:9px;
        overflow-x:auto; font-size:12.5px; line-height:1.6; }
  .err { background:#fef2f2; border:1px solid #fecaca; color:#991b1b;
         padding:10px 14px; border-radius:9px; font-size:13.5px; }
  .meta { font-size:12px; color:#9ca3af; margin-top:8px; }
  .load { display:inline-block; width:13px; height:13px; border:2px solid #dbe3ff;
          border-top-color:#1a5fb4; border-radius:50%; animation:spin .7s linear infinite;
          vertical-align:-2px; margin-right:6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <h1>数据分析 Agent</h1>
  <div class="sub">上传 CSV，用大白话提问。它会自己写 SQL、自审、校验、执行、检查结果，失败了自己改。</div>

  <div class="bar">
    <span class="tag" id="status">检查中</span>
    <span style="font-size:12.5px;color:#6b7280">数据：</span>
    <span id="tables"></span>
    <input type="file" id="file" accept=".csv" multiple style="display:none">
    <button class="ghost" onclick="document.getElementById('file').click()">上传 CSV</button>
  </div>

  <textarea id="q" placeholder="例如：退款率最高的三个商品"></textarea>
  <div class="row">
    <button class="primary" id="go" onclick="ask()">开始分析</button>
    <button class="ghost" onclick="document.getElementById('q').value=''">清空</button>
  </div>

  <div class="samples">
    <span class="chip" onclick="fill(this)">每个月的销售额趋势</span>
    <span class="chip" onclick="fill(this)">退款率最高的三个商品</span>
    <span class="chip" onclick="fill(this)">华东和华南的销售额对比</span>
    <span class="chip" onclick="fill(this)">哪个渠道客单价最高</span>
    <span class="chip" onclick="fill(this)">办公家具类的毛利率</span>
  </div>

  <div id="out"></div>
</div>

<script>
const PHASE = {generate:"生成", review:"自审", guard:"校验",
               execute:"执行", check:"检查", retry:"重试", summarize:"总结"};

function fill(el){ document.getElementById('q').value = el.textContent; }

function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function boot(){
  const r = await fetch('/api/info'); const d = await r.json();
  const st = document.getElementById('status');
  if (d.has_key) { st.className='tag ok'; st.textContent='模型已就绪 · '+d.model; }
  else { st.className='tag bad'; st.textContent='未配置 Key'; }
  document.getElementById('tables').innerHTML =
    d.tables.map(t => `<span class="tag">${esc(t.name)} · ${t.rows} 行</span>`).join(' ');
}

function render(d){
  if (d.error) return `<div class="card"><div class="err">${esc(d.error)}</div></div>`;
  let h = '<div class="card">';
  h += `<div class="q">${esc(d.question)}</div>`;
  h += `<div class="ans">${esc(d.summary)}</div>`;
  if (d.retries) h += `<div class="meta">本轮重试 ${d.retries} 次</div>`;
  if (d.columns.length) {
    h += '<table><thead><tr>' + d.columns.map(c=>`<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>';
    d.rows.slice(0,20).forEach(r => {
      h += '<tr>' + r.map(v=>`<td>${v===null?'<span style="color:#9ca3af">NULL</span>':esc(v)}</td>`).join('') + '</tr>';
    });
    h += '</tbody></table>';
    if (d.rows.length > 20) h += `<div class="meta">共 ${d.rows.length} 行，显示前 20 行</div>`;
  }
  h += '<details><summary>Agent 决策链路</summary>';
  d.trace.forEach(t => {
    h += `<div class="step"><div class="n">${t.step}</div>`
       + `<div class="ph">${PHASE[t.phase]||t.phase}</div>`
       + `<div class="mk ${t.ok?'ok':'bad'}">${t.ok?'OK':'FAIL'}</div>`
       + `<div class="dt"><b>${esc(t.title)}</b>`
       + (t.detail?`<div style="color:#6b7280;margin-top:2px;white-space:pre-wrap">${esc(t.detail)}</div>`:'')
       + '</div></div>';
  });
  h += '</details>';
  if (d.sql) h += `<details><summary>执行的 SQL</summary><pre>${esc(d.sql)}</pre></details>`;
  h += '</div>';
  return h;
}

async function ask(){
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const btn = document.getElementById('go');
  btn.disabled = true; btn.innerHTML = '<span class="load"></span>分析中';
  const out = document.getElementById('out');
  const ph = document.createElement('div');
  ph.className='card'; ph.innerHTML='<div style="color:#6b7280;font-size:13.5px"><span class="load"></span>Agent 正在生成 SQL 并校验…</div>';
  out.prepend(ph);
  try{
    const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'},
                                       body: JSON.stringify({question:q})});
    const d = await r.json();
    ph.innerHTML = render(d);
  }catch(e){
    ph.innerHTML = `<div class="err">请求失败：${esc(e.message)}</div>`;
  }finally{
    btn.disabled=false; btn.textContent='开始分析';
  }
}

document.getElementById('file').addEventListener('change', async ev => {
  const files = ev.target.files;
  if (!files.length) return;
  for (const f of files){
    const text = await f.text();
    await fetch('/api/upload', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:f.name, content:text})});
  }
  ev.target.value='';
  boot();
});

boot();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass   # 静音，避免刷屏

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/info":
            self._send(200, json.dumps({
                "has_key": bool(API_KEY),
                "model": MODEL,
                "tables": [{"name": t, "rows": len(DB.tables[t])} for t in DB.tables],
            }, ensure_ascii=False))
        else:
            self._send(404, '{"error":"not found"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, '{"error":"请求格式错误"}')
            return

        if self.path == "/api/upload":
            name = payload.get("name", "data.csv")
            table = os.path.splitext(os.path.basename(name))[0].replace(" ", "_")
            try:
                n = DB.load_csv(table, payload.get("content", ""))
                self._send(200, json.dumps({"ok": True, "table": table, "rows": n},
                                           ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return

        if self.path == "/api/ask":
            q = str(payload.get("question", "")).strip()
            if not q:
                self._send(200, json.dumps({"error": "问题为空"}, ensure_ascii=False))
                return
            if not API_KEY:
                self._send(200, json.dumps({"error": "服务端未配置 API_KEY，请用环境变量启动"},
                                           ensure_ascii=False))
                return
            try:
                agent = DataAgent(DB, LLM(key=API_KEY, model=MODEL), max_retry=3)
                res = agent.ask(q)
                self._send(200, json.dumps({
                    "question": res.question,
                    "summary": res.summary,
                    "sql": res.sql,
                    "columns": res.columns,
                    "rows": [list(r) for r in res.rows],
                    "retries": res.retries,
                    "error": res.error,
                    "trace": [
                        {"step": t.step, "phase": t.phase, "title": t.title,
                         "detail": t.detail, "ok": t.ok}
                        for t in res.trace
                    ],
                }, ensure_ascii=False, default=str))
            except LLMError as e:
                self._send(200, json.dumps({"error": str(e)}, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"error": f"{type(e).__name__}: {e}"},
                                           ensure_ascii=False))
            return

        self._send(404, '{"error":"not found"}')


def main():
    print("=" * 60)
    print(" 数据分析 Agent 本地服务")
    print("=" * 60)
    print(f"  地址   http://127.0.0.1:{PORT}")
    print(f"  模型   {MODEL}")
    print(f"  密钥   {'已配置' if API_KEY else '未配置（页面会提示）'}")
    for t in DB.tables:
        print(f"  数据   {t}（{len(DB.tables[t])} 行）")
    print("=" * 60)
    print("  按 Ctrl+C 停止服务")
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
