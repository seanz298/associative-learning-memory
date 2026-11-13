"""模型调用。只用标准库，Colab / Windows / Docker 都不用装依赖。

环境变量：
  ALM_KIND      openai_compatible（默认，DeepSeek / OpenAI 等）或 anthropic
  ALM_BASE_URL  默认 https://api.deepseek.com
  ALM_API_KEY   必填
  ALM_MODEL     必填
  ALM_PRICE_IN / ALM_PRICE_OUT  美元 / 百万 token；不填则成本记为 0
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

ChatFn = Callable[[str, str], Tuple[str, float]]  # (system, user) -> (text, cost_usd)


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def chat(system: str, user: str) -> Tuple[str, float]:
    kind = os.environ.get("ALM_KIND", "openai_compatible")
    key, model = os.environ.get("ALM_API_KEY", ""), os.environ.get("ALM_MODEL", "")
    if not key or not model:
        raise RuntimeError("ALM_API_KEY and ALM_MODEL must be set (see .env.example)")
    if kind == "anthropic":
        base = os.environ.get("ALM_BASE_URL", "https://api.anthropic.com")
        data = _post(base.rstrip("/") + "/v1/messages",
                     {"x-api-key": key, "anthropic-version": "2023-06-01"},
                     {"model": model, "max_tokens": 4096, "system": system,
                      "messages": [{"role": "user", "content": user}]})
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    elif kind == "openai_compatible":
        base = os.environ.get("ALM_BASE_URL", "https://api.deepseek.com")
        data = _post(base.rstrip("/") + "/chat/completions",
                     {"Authorization": f"Bearer {key}"},
                     {"model": model, "messages": [
                         {"role": "system", "content": system},
                         {"role": "user", "content": user}]})
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    else:
        raise ValueError(f"unknown ALM_KIND: {kind}")
    price_in = float(os.environ.get("ALM_PRICE_IN") or 0)
    price_out = float(os.environ.get("ALM_PRICE_OUT") or 0)
    return text, (tin * price_in + tout * price_out) / 1_000_000


def parse_json(text: str) -> Optional[Any]:
    """取回复里最外层的 JSON 对象或数组。解析不了返回 None，不猜。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
