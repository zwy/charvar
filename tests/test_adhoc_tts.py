"""Playground 语音通路单测：run_id 拦截、validate 上限、同步生成落盘、失败留痕、列表。

openrouter_tts 层全打桩（不发自网请求）；模型目录注入本地缓存。
"""
from __future__ import annotations

import io
import json
import pathlib
import wave
from pathlib import Path

import pytest

from charvar import adhoc_tts, openrouter_tts


CATALOG = {
    "data": [
        {
            "id": "heygen/heygen-voice-1-turbo:free",
            "name": "HeyGen Voice Turbo (free)",
            "supported_voices": ["nadine", "sabrina"],
            "pricing": {"prompt": "0"},
        },
        {
            "id": "minimax/speech-2.8-turbo",
            "name": "MiniMax Speech 2.8 Turbo",
            "supported_voices": ["mm_voice"],
            "pricing": {"prompt": "0.00006"},
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


def _pcm_result(env, *, ext="wav", sample_rate=44100, voice="nadine"):
    pcm = bytes(range(256)) * 4
    audio = openrouter_tts.wrap_pcm_as_wav(pcm, sample_rate, 1) if ext == "wav" else b"ID3" + b"\x00" * 16
    return openrouter_tts.SpeechResult(
        audio=audio,
        ext=ext,
        content_type=f"audio/pcm;rate={sample_rate};channels=1" if ext == "wav" else "audio/mpeg",
        sample_rate=sample_rate if ext == "wav" else None,
        channels=1 if ext == "wav" else None,
        generation_id="gen-test",
        format_sent="pcm" if ext == "wav" else "mp3",
        request_body={"model": "heygen/heygen-voice-1-turbo:free", "input": "<redacted>", "voice": voice},
    )


# ---------- run_id 与目录 ----------


def test_run_dir_rejects_bad_ids(env):
    for bad in ("video_x", "../etc", "tts_..evil", "", "tts_"):
        with pytest.raises(adhoc_tts.AdhocTtsError):
            adhoc_tts.run_dir(bad)
    good = adhoc_tts.run_dir("tts_20261028-101010_abcd1234")
    assert good == adhoc_tts.tts_root() / "tts_20261028-101010_abcd1234"


def test_audio_file_missing_raises(env):
    adhoc_tts.run_dir("tts_20261028-101010_abcd1234").mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        adhoc_tts.audio_file("tts_20261028-101010_abcd1234")


# ---------- validate ----------


def test_validate_rejects_empty_text(env):
    with pytest.raises(adhoc_tts.AdhocTtsError):
        adhoc_tts.validate(adhoc_tts.TtsRequest(text="   "))
    with pytest.raises(adhoc_tts.AdhocTtsError):
        adhoc_tts.validate(adhoc_tts.TtsRequest(text=""))


def test_validate_heygen_char_cap(env):
    long_text = "好" * 5001
    with pytest.raises(adhoc_tts.AdhocTtsError, match="5000"):
        adhoc_tts.validate(adhoc_tts.TtsRequest(text=long_text),
                           model="heygen/heygen-voice-1-turbo:free")
    # 正好 5000 放行
    adhoc_tts.validate(adhoc_tts.TtsRequest(text="好" * 5000),
                       model="heygen/heygen-voice-1-turbo:free")


def test_validate_no_cap_for_other_models(env):
    # 非 HeyGen 系：上限未知，交给 provider（超 5000 不在提交前拦）
    adhoc_tts.validate(adhoc_tts.TtsRequest(text="字" * 6000),
                       model="minimax/speech-2.8-turbo")


# ---------- generate（同步全流程） ----------


def test_generate_happy_path(env, monkeypatch):
    monkeypatch.setattr(openrouter_tts, "generate", lambda **kw: _pcm_result(env))
    result = adhoc_tts.generate(adhoc_tts.TtsRequest(text="你好", voice=""))
    assert result.provider == "openrouter"
    assert result.model == "heygen/heygen-voice-1-turbo:free"
    assert result.voice == "nadine"  # client 兜底（打桩的 request_body 里记了）
    assert result.response_format == "pcm"
    audio = Path(result.audio_path)
    assert audio.exists() and audio.suffix == ".wav"
    # 1024 字节 PCM = 512 帧 @44100 → ~0.0116s
    assert result.duration_s == pytest.approx(512 / 44100, abs=0.01)
    assert result.size_bytes == len(audio.read_bytes())
    assert result.cost == 0.0  # 免费档按目录价估算
    assert result.run_id.startswith("tts_")

    manifest = adhoc_tts.read_manifest(result.run_id)
    assert manifest["status"] == "ready"
    assert manifest["request"]["text"] == "你好"
    assert manifest["result"]["audio_path"] == result.audio_path


def test_generate_mp3_ext(env, monkeypatch):
    monkeypatch.setattr(openrouter_tts, "generate", lambda **kw: _pcm_result(env, ext="mp3"))
    result = adhoc_tts.generate(adhoc_tts.TtsRequest(text="hello"))
    assert Path(result.audio_path).suffix == ".mp3"
    assert Path(result.audio_path).read_bytes().startswith(b"ID3")


def test_generate_failure_leaves_manifest(env, monkeypatch):
    def boom(**kw):
        raise openrouter_tts.OpenRouterTtsAuthError("鉴权失败", http_status=401)

    monkeypatch.setattr(openrouter_tts, "generate", boom)
    with pytest.raises(adhoc_tts.AdhocTtsError):
        adhoc_tts.generate(adhoc_tts.TtsRequest(text="你好"))
    # 失败的 run 也留了 manifest（历史列表能看见失败原因）
    records = adhoc_tts.list_recent()
    failed = [r for r in records if r["status"] == "failed"]
    assert failed and "鉴权失败" in json.dumps(failed[0]["error"], ensure_ascii=False)
    assert failed[0]["has_audio"] is False


def test_generate_out_copy(env, monkeypatch):
    monkeypatch.setattr(openrouter_tts, "generate", lambda **kw: _pcm_result(env))
    out = env / "exported" / "clip.wav"
    result = adhoc_tts.generate(
        adhoc_tts.TtsRequest(text="你好", out=out),
    )
    assert out.exists()
    assert result.audio_path == str(out)


def test_generate_rejects_empty_text(env):
    with pytest.raises(adhoc_tts.AdhocTtsError):
        adhoc_tts.generate(adhoc_tts.TtsRequest(text="  "))


# ---------- list_recent ----------


def test_list_recent_order_and_fields(env, monkeypatch):
    def fake_generate(**kw):
        voice = kw.get("voice") or "nadine"
        return _pcm_result(env, voice=voice)

    monkeypatch.setattr(openrouter_tts, "generate", fake_generate)
    first = adhoc_tts.generate(adhoc_tts.TtsRequest(text="第一条"))
    second = adhoc_tts.generate(adhoc_tts.TtsRequest(text="第二条", voice="sabrina"))
    records = adhoc_tts.list_recent()
    # 同一秒内两次的时间戳可能相同（排序退到随机后缀）；两条都要在且字段完整
    runs = {r["run_id"]: r for r in records}
    assert first.run_id in runs and second.run_id in runs
    for run_id, text in ((first.run_id, "第一条"), (second.run_id, "第二条")):
        rec = runs[run_id]
        assert rec["text"] == text
        assert rec["has_audio"] is True
        assert rec["status"] == "ready"
        assert rec["duration_s"] is not None
        assert rec["cost"] == 0.0
    assert runs[second.run_id]["voice"] == "sabrina"
    # created_at 倒序（run_id 前缀同秒时退到 uuid 后缀，不强断言先后）


def test_wav_duration_read_from_header(env):
    pcm = bytes(range(256)) * 2  # 512 字节 = 256 帧 @24000
    audio = openrouter_tts.wrap_pcm_as_wav(pcm, 24000, 1)
    tmp = env / "probe.wav"
    tmp.write_bytes(audio)
    assert adhoc_tts._audio_duration(tmp, "wav") == round(256 / 24000, 3)


def test_mp3_duration_fallback_none(env):
    tmp = env / "broken.mp3"
    tmp.write_bytes(b"not really mp3")
    assert adhoc_tts._audio_duration(tmp, "mp3") is None