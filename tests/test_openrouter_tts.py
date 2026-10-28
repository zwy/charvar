"""OpenRouter 语音通路单测：请求体组装、voice 枚举校验、格式协商/降级、PCM 封 WAV、异常分类。

HTTP 层全走 MockTransport（不发真网）；模型目录用本地缓存文件注入。
"""
from __future__ import annotations

import io
import json
import pathlib
import wave

import httpx
import pytest

from charvar import openrouter_tts


CATALOG = {
    "data": [
        {
            "id": "heygen/heygen-voice-1-turbo:free",
            "name": "HeyGen Voice Turbo (free)",
            "supported_voices": ["nadine", "tabitha", "sabrina", "vincent"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "heygen/heygen-voice-1",
            "name": "HeyGen Voice",
            "supported_voices": ["nadine", "sabrina"],
            "pricing": {"prompt": "0.000015"},
        },
        {
            "id": "minimax/speech-2.8-turbo",
            "name": "MiniMax Speech 2.8 Turbo",
            "supported_voices": ["mm_voice"],
            "pricing": {"prompt": "0.00006"},
        },
        {
            "id": "test/no-voices",
            "name": "No Voices",
            "pricing": {"prompt": "0.001"},
        },
    ]
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "speech-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    return tmp_path


def _entry(env, model_id="heygen/heygen-voice-1-turbo:free"):
    return openrouter_tts.speech_model_entry(model_id)


def _install(env, handler):
    transport = httpx.MockTransport(handler)

    def factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=httpx.Timeout(timeout, write=60.0))

    openrouter_tts._http_client = factory  # type: ignore[assignment]


# ---------- build_body ----------


def test_body_base_fields(env):
    body = openrouter_tts.build_body(
        text="hello there", model="heygen/heygen-voice-1-turbo:free",
        voice="sabrina", response_format="pcm",
    )
    assert body["model"] == "heygen/heygen-voice-1-turbo:free"
    assert body["input"] == "hello there"
    assert body["voice"] == "sabrina"
    assert body["response_format"] == "pcm"


def test_body_voice_rejected_out_of_enum(env):
    with pytest.raises(openrouter_tts.OpenRouterTtsParamError):
        openrouter_tts.build_body(
            text="hi", model="heygen/heygen-voice-1-turbo:free", voice="bogus",
        )


def test_body_voice_passes_when_catalog_has_no_enum(env):
    # 目录没收录的模型：不拦，交 provider 裁决（Unknown voice 的 404 原样带出）
    body = openrouter_tts.build_body(text="hi", model="unknown/model", voice="whatever")
    assert body["voice"] == "whatever"


def test_body_voice_omitted_when_empty(env):
    body = openrouter_tts.build_body(text="hi", model="heygen/heygen-voice-1", voice="")
    assert "voice" not in body


def test_body_rejects_unknown_format(env):
    with pytest.raises(openrouter_tts.OpenRouterTtsParamError):
        openrouter_tts.build_body(text="hi", model="m/x", response_format="wav")


def test_body_rejects_empty_text(env):
    with pytest.raises(openrouter_tts.OpenRouterTtsParamError):
        openrouter_tts.build_body(text="   ", model="heygen/heygen-voice-1")


# ---------- 模型目录 / 解析链 ----------


def test_resolve_model_precedence(env, monkeypatch):
    assert openrouter_tts.resolve_model("m/a") == "m/a"
    monkeypatch.setenv("OP_TTS_MODEL", "m/env")
    assert openrouter_tts.resolve_model(None) == "m/env"
    monkeypatch.delenv("OP_TTS_MODEL")
    assert openrouter_tts.resolve_model(None) == "heygen/heygen-voice-1-turbo:free"


def test_list_models_reads_cache(env):
    data = openrouter_tts.list_models()
    ids = [m.get("id") for m in data.get("data") or []]
    assert "heygen/heygen-voice-1" in ids


def test_model_choices(env):
    choices = openrouter_tts.model_choices()
    assert {"id": "test/no-voices", "name": "No Voices"} in [dict(c) for c in choices]


def test_supported_voices(env):
    assert openrouter_tts.supported_voices(_entry(env)) == ("nadine", "tabitha", "sabrina", "vincent")
    assert openrouter_tts.supported_voices(_entry(env, "test/no-voices")) == ()


# ---------- estimate_cost ----------


def test_estimate_cost_paid(env):
    # minimax $0.00006/字符 × 100 字符
    cost = openrouter_tts.estimate_cost("minimax/speech-2.8-turbo", 100)
    assert cost == round(0.00006 * 100, 6)


def test_estimate_cost_free_zero(env):
    assert openrouter_tts.estimate_cost("heygen/heygen-voice-1-turbo:free", 5000) == 0.0


def test_estimate_cost_unknown_model_none(env):
    assert openrouter_tts.estimate_cost("unknown/model", 10) is None


# ---------- 格式协商 ----------


def test_static_format_map(env):
    assert openrouter_tts.static_format("heygen/heygen-voice-1") == "pcm"
    assert openrouter_tts.static_format("minimax/speech-2.8-turbo") == "mp3"
    assert openrouter_tts.static_format("google/gemini-3.8-flash-tts") == "pcm"
    assert openrouter_tts.static_format("test/no-voices") is None


def test_suggested_format_from_error_wording(env):
    assert openrouter_tts._suggested_format(
        'HeyGen Voice only supports response_format "pcm".'
    ) == "pcm"
    assert openrouter_tts._suggested_format(
        'MiniMax TTS only supports response_format="mp3" for streaming. Got "pcm".'
    ) == "mp3"
    assert openrouter_tts._suggested_format("unrelated failure") is None


def test_initial_format_precedence(env, monkeypatch):
    # 探测缓存 > 静态表：往缓存里记 mp3，heygen 也该走 mp3
    openrouter_tts._record_format("heygen/heygen-voice-1-turbo:free", "mp3")
    assert openrouter_tts._initial_format("heygen/heygen-voice-1-turbo:free", None) == "mp3"
    assert openrouter_tts._initial_format("heygen/heygen-voice-1-turbo:free", "pcm") == "pcm"
    # 缓存没有的模型走静态表
    assert openrouter_tts._initial_format("heygen/heygen-voice-1", None) == "pcm"


# ---------- 合成（同步 POST → 音频字节） ----------


def test_generate_defaults_voice_from_enum(env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, content=b"ID3" + b"\x00" * 64,
            headers={"content-type": "audio/mpeg", "x-generation-id": "gen-1"},
        )

    _install(env, handler)
    # voice 不给：实测 voice 必填（heygen/minimax 不带就 400），client 兜底枚举第一项
    result = openrouter_tts.generate(text="hello", model="heygen/heygen-voice-1-turbo:free")
    assert seen["body"]["voice"] == "nadine"
    assert result.ext == "mp3"
    assert result.generation_id == "gen-1"


def test_generate_explicit_voice_wins(env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, content=b"ID3" + b"\x00" * 8, headers={"content-type": "audio/mpeg"})

    _install(env, handler)
    openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free", voice="sabrina")
    assert seen["body"]["voice"] == "sabrina"


def test_generate_voice_omitted_when_no_enum(env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, content=b"ID3" + b"\x00" * 8, headers={"content-type": "audio/mpeg"})

    _install(env, handler)
    openrouter_tts.generate(text="hi", model="test/no-voices")
    assert "voice" not in seen["body"]


def test_generate_pcm_wraps_to_wav(env):
    pcm = bytes(range(256)) * 4  # 1024 字节 = 512 帧

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=pcm,
            headers={"content-type": "audio/pcm;rate=44100;channels=1"},
        )

    _install(env, handler)
    result = openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")
    assert result.ext == "wav"
    assert result.sample_rate == 44100
    assert result.channels == 1
    with wave.open(io.BytesIO(result.audio), "rb") as handle:
        assert handle.getnframes() == 512
        assert handle.getframerate() == 44100
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2


def test_generate_format_degrades_on_rejection_and_records(env):
    seen_formats = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen_formats.append(body.get("response_format"))
        if body.get("response_format") == "mp3":
            return httpx.Response(
                400, json={"error": {"message": 'HeyGen Voice only supports response_format "pcm".'}}
            )
        return httpx.Response(200, content=b"ID3" + b"\x00" * 8, headers={"content-type": "audio/mpeg"})

    _install(env, handler)
    result = openrouter_tts.generate(
        text="hi", model="heygen/heygen-voice-1-turbo:free", response_format="mp3",
    )
    assert seen_formats == ["mp3", "pcm"]  # 显式 mp3 被拒 → 按话术换 pcm 重试
    assert result.format_requested == "mp3"
    assert result.format_sent == "pcm"
    # 探测结果落档：下次同模型直接发对（缓存 > 静态表）
    assert openrouter_tts._read_format_cache()["heygen/heygen-voice-1-turbo:free"]["response_format"] == "pcm"
    assert openrouter_tts._initial_format("heygen/heygen-voice-1-turbo:free", None) == "pcm"


def test_generate_unknown_text_error_not_degraded(env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"message": 'Unknown voice "bogus". See supported_voices.'}}
        )

    _install(env, handler)
    with pytest.raises(openrouter_tts.OpenRouterTtsNotFoundError):
        openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")


def test_generate_requires_api_key(env, monkeypatch):
    monkeypatch.delenv("OP_IMAGE_API_KEY")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(openrouter_tts.OpenRouterTtsParamError):
        openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")


def test_error_classification(env):
    def deny(status, message):
        return lambda request: httpx.Response(status, json={"error": {"message": message}})

    _install(env, deny(401, "bad key"))
    with pytest.raises(openrouter_tts.OpenRouterTtsAuthError):
        openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")
    _install(env, deny(429, "slow down"))
    with pytest.raises(openrouter_tts.OpenRouterTtsRateLimitError):
        openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")
    _install(env, deny(403, "content policy violation"))
    with pytest.raises(openrouter_tts.OpenRouterTtsFetchError):
        openrouter_tts.generate(text="hi", model="heygen/heygen-voice-1-turbo:free")


# ---------- 嗅探 / 封装 ----------


def test_sniff_audio_ext(env):
    assert openrouter_tts.sniff_audio_ext(b"RIFF" + b"\x00" * 4 + b"WAVE") == "wav"
    assert openrouter_tts.sniff_audio_ext(b"ID3\x03" + b"\x00" * 8) == "mp3"
    assert openrouter_tts.sniff_audio_ext(b"\xff\xfb\x90\x00" + b"\x00" * 8) == "mp3"
    assert openrouter_tts.sniff_audio_ext(b"OggS" + b"\x00" * 8) == "ogg"
    assert openrouter_tts.sniff_audio_ext(b"fLaC" + b"\x00" * 8) == "flac"
    assert openrouter_tts.sniff_audio_ext(b"\x00\x00\x00\x00") == "bin"


def test_parse_audio_params(env):
    assert openrouter_tts._parse_audio_params("audio/pcm;rate=44100;channels=1") == (44100, 1)
    assert openrouter_tts._parse_audio_params("audio/mpeg") == (None, None)


def test_wrap_pcm_roundtrip(env):
    data = b"\x01\x02" * 100  # 200 字节 = 100 样本 = 50 双声道帧
    wrapped = openrouter_tts.wrap_pcm_as_wav(data, 24000, 2)
    with wave.open(io.BytesIO(wrapped), "rb") as handle:
        assert handle.getnframes() == 50
        assert handle.getframerate() == 24000
        assert handle.getnchannels() == 2