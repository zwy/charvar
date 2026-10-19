"""LLM 客户端单测：MockTransport 打桩 _http_client，不发真网。"""
from __future__ import annotations

import json

import httpx
import pytest

from charvar import llm


def _fake_llm(monkeypatch, handler):
    class FakeClient:
        def __init__(self, **kw):
            self._inner = httpx.Client(transport=httpx.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(llm, "_http_client", lambda **kw: FakeClient(**kw))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def _ok_content(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {}})


def test_chat_success(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return _ok_content("hello")

    _fake_llm(monkeypatch, handler)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hello"
    body = seen[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning"] == {"exclude": True}


def test_chat_retry_on_429(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return _ok_content("ok")

    _fake_llm(monkeypatch, handler)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert calls["n"] == 2


def test_chat_json_salvage_parse(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_content('好的，结果如下：{"a": 1, "b": ["x"]} 以上。')

    _fake_llm(monkeypatch, handler)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert llm.chat_json([{"role": "user", "content": "hi"}]) == {"a": 1, "b": ["x"]}


def test_chat_empty_content_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}], "usage": {"total_tokens": 1}})

    _fake_llm(monkeypatch, handler)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    with pytest.raises(llm.LLMError, match="LLM call failed"):
        llm.chat([{"role": "user", "content": "hi"}], retries=2)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="缺少 API Key"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_config_precedence(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://env.example/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    cfg = llm.get_llm_config()
    assert cfg["base_url"] == "http://env.example/v1"
    assert cfg["model"] == "env-model"
    overridden = llm.get_llm_config({"base_url": "http://db.example/v1", "model": "db-model", "api_key": "sk-db"})
    assert overridden["base_url"] == "http://db.example/v1"
    assert overridden["model"] == "db-model"
    assert overridden["api_key"] == "sk-db"
    # 空串 override 不覆盖 env
    partial = llm.get_llm_config({"model": ""})
    assert partial["model"] == "env-model"
