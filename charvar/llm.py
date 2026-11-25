"""LLM 客户端（OpenAI 规范 chat completions，兼容任意 provider）。

移植自 spellforge/styles/llm.py（与 riban-ranking 同骨架），改动：
- requests → httpx，trust_env=False 忽略 shell 环境代理；出口代理走
  :func:`resolve_proxy` 链（显式 config/参数 > env ``LLM_PROXY`` > 系统代理 > 直连，
  与音乐通路同款）——OpenRouter 按请求出口 IP 判区，直连出口的推理 POST 被 400
  ``Request path cannot be relayed``（2026-11-25 实测）；系统代理兜底只对
  openrouter.ai 基址生效，防自建/内网 LLM 被塞进 QuickQ 出口；
- 配置优先级：显式 override 参数（调用方传 DB 设置）> 环境变量 / .env
  （.env 已由 charvar.paths 在 import 时载入 os.environ）。
环境变量：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_PROXY。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
_TIMEOUT = 300.0  # 30 条全批改写的输出很长（>1.5 万 token），放宽到 5 分钟

_CONFIG_KEYS = ("base_url", "api_key", "model", "proxy")


class LLMError(RuntimeError):
    pass


class LLMParseError(LLMError):
    """返回文本解析 JSON 失败；.text 保留原始返回，供调用方做截断抢救。"""

    def __init__(self, message: str, text: str = ""):
        super().__init__(message)
        self.text = text


def _http_client(**kw: Any) -> httpx.Client:
    # trust_env=False：忽略 shell 的 ALL_PROXY 等环境代理（与 comfyui_exporter 同策略）
    return httpx.Client(trust_env=False, **kw)


def resolve_proxy(base_url: str, proxy: str | None = None) -> str | None:
    """LLM 通路的出口代理：显式 > env ``LLM_PROXY`` > 系统代理 > 直连。

    系统代理兜底只在 openrouter.ai 基址下生效（OpenRouter 按出口 IP 判区）；其他
    provider（可能是自建/内网服务）不送进系统代理，保持直连。
    """
    for candidate in (proxy, os.environ.get("LLM_PROXY")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    host = (urllib.parse.urlparse(str(base_url or "")).hostname or "").lower()
    if host not in ("openrouter.ai", "www.openrouter.ai"):
        return None
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - 读系统代理失败按直连走
        return None
    value = str(proxies.get("https") or proxies.get("http") or "").strip()
    return value or None


def get_llm_config(override: dict | None = None) -> dict:
    """解析当前生效的 LLM 配置。

    override：调用方传入的 {base_url, api_key, model}（通常来自 DB 设置，
    见 settings_service.llm_overrides），非空键覆盖环境变量。
    """
    cfg = {
        "base_url": (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "api_key": os.environ.get("LLM_API_KEY") or "",
        "model": os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
    }
    for key in _CONFIG_KEYS:
        value = (override or {}).get(key)
        if value and str(value).strip():
            cfg[key] = str(value).strip()
    return cfg


def chat(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int | None = None,
    json_mode: bool = True,
    retries: int = 3,
    config: dict | None = None,
    schema: dict | None = None,
    schema_name: str = "response",
    strict: bool = True,
) -> str:
    """调用一次 chat completion，返回 assistant 文本。

    json_mode=True 时请求 JSON 输出并关闭 reasoning 回显；带指数退避重试。
    schema 给定时请求 json_schema（structured outputs）；供应商不支持（400/422）
    时自动降级 json_object，降级不消耗重试次数。
    max_tokens=None 时不传该字段，输出长度交由模型自身上限决定（类似 -1）。
    """
    cfg = get_llm_config(config)
    api_key = cfg["api_key"]
    if not api_key:
        raise LLMError(
            "缺少 API Key：请在设置页配置 LLM，或设置环境变量 LLM_API_KEY / .env 文件"
        )
    url = cfg["base_url"] + "/chat/completions"

    body: dict = {
        "model": model or cfg["model"],
        "messages": messages,
        "reasoning": {"exclude": True},
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    def _response_format(use_schema: bool) -> dict | None:
        if use_schema and schema is not None:
            return {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": strict, "schema": schema},
            }
        return {"type": "json_object"} if json_mode else None

    schema_on = schema is not None
    last_err: Exception | None = None
    attempt = 0
    while attempt < retries:
        payload = {**body}
        rf = _response_format(schema_on)
        if rf is not None:
            payload["response_format"] = rf
        attempt += 1
        try:
            proxy = resolve_proxy(cfg["base_url"], cfg.get("proxy"))
            with _http_client(timeout=httpx.Timeout(_TIMEOUT, write=60.0), proxy=proxy) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=_TIMEOUT,
                )
            if resp.status_code in (400, 422) and schema_on:
                # 供应商不支持 structured outputs → 降级 json_object 重试（不消耗次数）
                schema_on = False
                attempt -= 1
                continue
            if resp.status_code == 429:
                time.sleep(2**attempt * 3)
                last_err = LLMError("rate limited")
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"].get("content")
            if not content:
                raise LLMError(f"empty content: {json.dumps(data.get('usage', {}), ensure_ascii=False)}")
            return content
        except (httpx.HTTPError, KeyError, LLMError) as e:  # noqa: PERF203
            last_err = e
            if attempt < retries:
                time.sleep(2**attempt * 2)
    raise LLMError(f"LLM call failed after {retries} attempts: {last_err}")


def chat_json(messages: list[dict], **kw: Any) -> dict:
    """调用并解析 JSON 输出；解析失败时尝试从文本中截取第一个 JSON 对象。

    仍失败时抛 LLMParseError（.text 保留原始返回全文，供调用方抢救截断输出）。
    """
    text = chat(messages, json_mode=True, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start >= 0:
            try:
                return json.loads(text[start : text.rfind("}") + 1])
            except json.JSONDecodeError:
                pass
        raise LLMParseError(f"invalid JSON response: {text[:200]}", text=text) from None
