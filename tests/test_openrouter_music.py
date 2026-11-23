"""OpenRouter 音乐通路单测：请求体组装、seed 透传策略、SSE 解析、实收费用、异常分类。

HTTP 层全走 MockTransport（不发真网）；模型目录用本地缓存文件注入。
"""
from __future__ import annotations

import base64
import json
import pathlib

import httpx
import pytest

from charvar import openrouter_music


_REAL_HTTP_CLIENT = openrouter_music._http_client


CATALOG = {
    "data": [
        {
            "id": "google/lyria-3-pro-preview",
            "name": "Google: Lyria 3 Pro Preview",
            "supported_parameters": ["max_tokens", "response_format", "seed", "temperature", "top_p"],
            "description": "Full-length songs are priced at $0.08 per song. Lyria 3 generates high-quality music.",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "google/lyria-3-clip-preview",
            "name": "Google: Lyria 3 Clip Preview",
            "supported_parameters": ["seed", "temperature"],
            "description": "30 second duration clips are priced at $0.04 per clip. Lyria 3 music.",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "test/no-price",
            "name": "No Price",
            "supported_parameters": [],
            "description": "no price info here",
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "music-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    return tmp_path


def _install(env, handler):
    transport = httpx.MockTransport(handler)

    def factory(timeout: float, proxy: str | None = None) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=httpx.Timeout(timeout, write=60.0))

    openrouter_music._http_client = factory  # type: ignore[assignment]


@pytest.fixture()
def restore_factory(env):
    yield
    # 用例间还原工厂,防 _install 的覆写串到下一个用例(模块级单例)
    openrouter_music._http_client = _REAL_HTTP_CLIENT  # type: ignore[assignment]


# ---------- build_body ----------

@pytest.mark.usefixtures("env")
def test_body_base_fields():
    body = openrouter_music.build_body(prompt="funk groove", model="google/lyria-3-clip-preview")
    assert body["model"] == "google/lyria-3-clip-preview"
    assert body["messages"] == [{"role": "user", "content": "funk groove"}]
    assert body["modalities"] == ["text", "audio"]
    assert body["stream"] is True
    assert "seed" not in body
    assert "audio" not in body


@pytest.mark.usefixtures("env")
def test_body_seed_sent_only_when_declared():
    # clip 声明 seed → 发
    body = openrouter_music.build_body(prompt="x", model="google/lyria-3-clip-preview", seed=42)
    assert body["seed"] == 42
    # 目录没收录的模型不拦也不发(交 provider 裁决)
    body = openrouter_music.build_body(prompt="x", model="unknown/model", seed=42)
    assert "seed" not in body


@pytest.mark.usefixtures("env")
def test_body_audio_config_only_when_explicit():
    body = openrouter_music.build_body(
        prompt="x", model="test/no-price", voice="alloy", audio_format="pcm16",
    )
    assert body["audio"] == {"voice": "alloy", "format": "pcm16"}
    # 只要有一个就组 audio(voice-only)
    body = openrouter_music.build_body(prompt="x", model="test/no-price", voice="alloy")
    assert body["audio"] == {"voice": "alloy"}


@pytest.mark.usefixtures("env")
def test_body_rejects_empty_prompt():
    with pytest.raises(openrouter_music.OpenRouterMusicParamError):
        openrouter_music.build_body(prompt="   ", model="google/lyria-3-clip-preview")


@pytest.mark.usefixtures("env")
def test_body_rejects_no_model():
    with pytest.raises(openrouter_music.OpenRouterMusicParamError):
        openrouter_music.build_body(prompt="x", model="  ")


# ---------- 模型目录 / 解析链 ----------


def test_resolve_proxy_chain(monkeypatch):
    # 显式 > OP_MUSIC_PROXY > 系统代理 > 直连(None)
    assert openrouter_music.resolve_proxy("http://x:1") == "http://x:1"
    monkeypatch.setenv("OP_MUSIC_PROXY", "http://x:2")
    assert openrouter_music.resolve_proxy(None) == "http://x:2"
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://127.0.0.1:3"})
    monkeypatch.delenv("OP_MUSIC_PROXY")
    assert openrouter_music.resolve_proxy(None) == "http://127.0.0.1:3"
    # 系统代理没开 → 直连（此时 Lyria 报地区 403，目录不受影响）
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})
    assert openrouter_music.resolve_proxy(None) is None
    # socks 走不了（httpx 原生不带 socks 扩展时）:优先取 https/http 条目
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"socks": "socks5://127.0.0.1:4"})
    assert openrouter_music.resolve_proxy(None) is None


def test_resolve_model_precedence(env, monkeypatch):
    assert openrouter_music.resolve_model("m/a") == "m/a"
    monkeypatch.setenv("OP_MUSIC_MODEL", "m/env")
    assert openrouter_music.resolve_model(None) == "m/env"
    monkeypatch.delenv("OP_MUSIC_MODEL")
    assert openrouter_music.resolve_model(None) == "google/lyria-3-clip-preview"


def test_list_models_reads_cache(env):
    data = openrouter_music.list_models()
    ids = [m.get("id") for m in data.get("data") or []]
    assert "google/lyria-3-clip-preview" in ids


@pytest.mark.usefixtures("env")
def test_model_choices_sorted():
    choices = openrouter_music.model_choices()
    ids = [c["id"] for c in choices]
    assert ids == sorted(ids)


# ---------- estimate_cost ----------


@pytest.mark.usefixtures("env")
def test_estimate_cost_from_description():
    assert openrouter_music.estimate_cost("google/lyria-3-clip-preview") == 0.04
    assert openrouter_music.estimate_cost("google/lyria-3-pro-preview") == 0.08


@pytest.mark.usefixtures("env")
def test_estimate_cost_unknown_none():
    assert openrouter_music.estimate_cost("test/no-price") is None


# ---------- SSE 解析 ----------


def _chunk(usage=None, **delta):
    payload = {"choices": [{"delta": delta}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


@pytest.mark.usefixtures("env")
def test_stream_accumulator_finalizes():
    acc = openrouter_music._StreamAccumulator()
    acc.feed(_chunk(**{"content": "A short funk piece."}))
    acc.feed(_chunk(audio={"data": base64.b64encode(b"ID3").decode()}))
    acc.feed(_chunk(audio={"data": base64.b64encode(b"\x00" * 61).decode(),
                           "transcript": "la la"}))
    acc.feed(_chunk(usage={"cost": 0.04}))
    audio, transcript, text, cost = acc.finalize()
    assert audio[:3] == b"ID3"
    assert len(audio) == 64
    assert transcript == "la la"
    assert text == "A short funk piece."
    assert cost == 0.04


@pytest.mark.usefixtures("env")
def test_stream_no_audio_raises():
    acc = openrouter_music._StreamAccumulator()
    acc.feed(_chunk(**{"content": "no audio here"}))
    with pytest.raises(openrouter_music.OpenRouterMusicParseError):
        acc.finalize()


@pytest.mark.usefixtures("env")
def test_stream_raw_pcm_unsupported():
    acc = openrouter_music._StreamAccumulator()
    acc.feed(_chunk(audio={"data": base64.b64encode(b"\x00" * 64).decode()}))
    with pytest.raises(openrouter_music.OpenRouterMusicParseError):
        acc.finalize()


def test_generate_collects_sse(env):
    body = {"prompt": "x", "model": "google/lyria-3-clip-preview"}

    def sse_stream(request: httpx.Request):
        # 断言请求体结构
        sent = json.loads(request.content.decode())
        assert sent["modalities"] == ["text", "audio"]
        assert sent["stream"] is True
        lines = [
            'data: ' + json.dumps(_chunk(**{"content": "Instrumental."})),
            'data: ' + json.dumps(_chunk(audio={"data": base64.b64encode(b"ID3").decode()})),
            'data: ' + json.dumps(_chunk(audio={"data": base64.b64encode(b"\x00" * 61).decode()})),
            'data: ' + json.dumps(_chunk(usage={"cost": 0.04})),
            "data: [DONE]",
        ]
        content = ("\n".join(lines) + "\n").encode()

        def iter_body():
            yield content

        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    def handler(request: httpx.Request):  # noqa: ARG001
        return sse_stream(request)

    _install(env, handler)
    result = openrouter_music.generate(
        prompt="a funk groove", model="google/lyria-3-clip-preview", seed=123,
    )
    assert result.ext == "mp3"
    assert result.text_content == "Instrumental."
    assert result.usage_cost == 0.04
    assert result.seed_sent == 123


@pytest.mark.usefixtures("env")
def test_generate_requires_api_key(env, monkeypatch):
    monkeypatch.delenv("OP_API_KEY", raising=False)
    monkeypatch.delenv("OP_IMAGE_API_KEY")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(openrouter_music.OpenRouterMusicParamError):
        openrouter_music.generate(prompt="x", model="google/lyria-3-clip-preview")


def test_error_classification(env):
    def deny(status, message):
        return lambda request: httpx.Response(status, json={"error": {"message": message}})

    _install(env, deny(401, "bad key"))
    with pytest.raises(openrouter_music.OpenRouterMusicAuthError):
        openrouter_music.generate(prompt="x", model="google/lyria-3-clip-preview")
    _install(env, deny(429, "slow down"))
    with pytest.raises(openrouter_music.OpenRouterMusicRateLimitError):
        openrouter_music.generate(prompt="x", model="google/lyria-3-clip-preview")
    _install(env, deny(403, "content policy violation"))
    with pytest.raises(openrouter_music.OpenRouterMusicFetchError):
        openrouter_music.generate(prompt="x", model="google/lyria-3-clip-preview")
