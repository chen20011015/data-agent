# -*- coding: utf-8 -*-
"""LLM 客户端：只依赖标准库 urllib，走 OpenAI 兼容协议。

支持智谱 GLM / DeepSeek / OpenAI，换厂商只改 base 与 model。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"


class LLMError(RuntimeError):
    """模型调用失败，带可读原因。"""


class LLM:
    def __init__(self, base: str | None = None, key: str | None = None,
                 model: str | None = None, timeout: int = 90):
        self.base = (base or os.getenv("API_BASE") or DEFAULT_BASE).rstrip("/")
        self.key = key or os.getenv("API_KEY") or os.getenv("CHAT_API_KEY") or ""
        self.model = model or os.getenv("CHAT_MODEL") or DEFAULT_MODEL
        self.timeout = timeout

    # ---------------------------------------------------------------- 底层

    def _check_key(self) -> None:
        """Key 会被放进 HTTP 头，只能是 ASCII，且不能有空白。"""
        k = (self.key or "").strip()
        if not k:
            raise LLMError("未配置 API Key：请设置环境变量 API_KEY，或用 --key 传入")
        if not k.isascii():
            raise LLMError(
                "API Key 里含有中文或其他非 ASCII 字符。\n"
                "  多半是把文档里的示例「你的Key」原样填了进去——那三个字要换成真实字符串。\n"
                "  真实 Key 形如：5cd6f536b43c4f5b898d5c4c82cee7c3.LejOdxx4GZpL5lbO"
            )
        if any(c.isspace() for c in k):
            raise LLMError("API Key 里含有空格或换行，请重新复制完整字符串")
        if len(k) < 20:
            raise LLMError(f"API Key 长度不对（当前 {len(k)} 位），请确认复制完整")

    def chat(self, messages: list[dict], temperature: float = 0.1,
             max_tokens: int = 2000) -> str:
        self._check_key()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
            except Exception:
                detail = str(e)
            if e.code in (401, 403):
                raise LLMError(f"Key 无效或无权限（{e.code}）：{detail}")
            if e.code == 429:
                raise LLMError(f"限流或余额不足（429）：{detail}")
            raise LLMError(f"接口报错 {e.code}：{detail}")
        except urllib.error.URLError as e:
            raise LLMError(f"网络不通：{e.reason}")
        except TimeoutError:
            raise LLMError("请求超时")

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError(f"返回格式异常：{str(body)[:200]}")

    # ---------------------------------------------------------------- 提取

    @staticmethod
    def extract_sql(text: str) -> str:
        """从模型输出里抽出 SQL。模型常带 ```sql 围栏或解释文字。"""
        if not text:
            return ""
        m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
        sql = m.group(1) if m else text
        # 去掉常见前缀
        sql = re.sub(r"^\s*(sql\s*[:：])", "", sql, flags=re.I)
        return sql.strip().strip("`").strip()
