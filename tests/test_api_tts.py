"""Playground 语音的网页入口：/tts/* 接口（打桩 + 本地目录注入，不发真网）。"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from charvar import adhoc_tts, openrouter_tts
from charvar.api import build_app


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
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "speech-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    # openrouter_tts.generate 打桩（adhoc_tts 与 api 层都经它）
    audio = openrouter_tts.wrap_pcm_as_wav(bytes(range(256)), 44100, 1)

    def fake_generate(**kw):
        return openrouter_tts.SpeechResult(
            audio=audio,
            ext="wav",
            content_type="audio/pcm;rate=44100;channels=1",
            sample_rate=44100,
            channels=1,
            generation_id="gen-api",
            format_sent="pcm",
            request_body={"input": "<redacted>", "voice": "nadine"},
        )

    monkeypatch.setattr(openrouter_tts, "generate", fake_generate)
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_tts_models(client):
    resp = client.get("/tts/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_model"] == "heygen/heygen-voice-1-turbo:free"
    ids = [m["id"] for m in data["data"]]
    assert "heygen/heygen-voice-1-turbo:free" in ids
    voices = [m["supported_voices"] for m in data["data"] if m["id"] == "heygen/heygen-voice-1-turbo:free"]
    assert voices == [["nadine", "sabrina"]]


def test_tts_generate_returns_audio_url(client):
    resp = client.post(
        "/tts/generate",
        json={"text": "接口实测", "voice": "sabrina"},
    )
    assert resp.status_code == 200
    payload = resp.json()  # 同步通路：result 直接就是终态（无 status/轮询字段）
    assert payload["model"] == "heygen/heygen-voice-1-turbo:free"
    assert payload["voice"] == "nadine"
    assert payload["manifest_path"].endswith("manifest.json")
    assert payload["audio_url"] == f"/tts/{payload['run_id']}/audio"
    # 音频可直接取回
    audio_resp = client.get(payload["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"].startswith("audio/wav")


def test_tts_generate_empty_text_is_400(client):
    resp = client.post("/tts/generate", json={"text": "   "})
    assert resp.status_code == 400


def test_tts_generate_over_cap_is_400(client):
    resp = client.post("/tts/generate", json={"text": "好" * 5001})
    assert resp.status_code == 400
    assert "5000" in resp.json()["detail"]


def test_tts_recent_list(client):
    client.post("/tts/generate", json={"text": "列表实测"})
    resp = client.get("/tts")
    assert resp.status_code == 200
    records = resp.json()
    assert records and records[0]["text"] == "列表实测"
    assert records[0]["has_audio"] is True


def test_tts_manifest_endpoint(client):
    created = client.post("/tts/generate", json={"text": "manifest 实测"}).json()
    resp = client.get(f"/tts/{created['run_id']}/manifest")
    assert resp.status_code == 200
    assert resp.json()["request"]["text"] == "manifest 实测"


def test_tts_routes_order_literal_paths_first(client):
    # 字面路径不能被 /tts/{run_id}/… 的通配吞掉
    assert client.get("/tts/models").status_code == 200


def test_tts_audio_rejects_bad_run_id(client):
    resp = client.get("/tts/..%2Fetc/audio")
    assert resp.status_code in (400, 404)


def test_tts_audio_unknown_run_404(client):
    resp = client.get("/tts/tts_20991231-000000_unknown/audio")
    assert resp.status_code == 404


def test_tts_settings_roundtrip(client):
    # 设置页保存 openrouter_tts.model → /settings 回读一致
    resp = client.put("/settings", json={"openrouter_tts": {"model": "minimax/speech-2.8-turbo"}})
    assert resp.status_code == 200
    assert resp.json()["openrouter_tts"]["model"] == "minimax/speech-2.8-turbo"
    # 默认链生效：/tts/models 的 default_model 跟着换
    assert client.get("/tts/models").json()["default_model"] == "minimax/speech-2.8-turbo"