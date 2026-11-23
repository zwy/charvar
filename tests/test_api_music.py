"""音乐的网页入口：/music/* 接口（打桩 + 本地目录注入，不发真网；镜像 test_api_tts）。"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from charvar import adhoc_music, openrouter_music
from charvar.api import build_app


CATALOG = {
    "data": [
        {
            "id": "google/lyria-3-clip-preview",
            "name": "Google: Lyria 3 Clip Preview",
            "supported_parameters": ["seed", "temperature"],
            "description": "30 second duration clips are priced at $0.04 per clip.",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "google/lyria-3-pro-preview",
            "name": "Google: Lyria 3 Pro Preview",
            "supported_parameters": ["seed"],
            "description": "Full-length songs are priced at $0.08 per song.",
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "music-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    # openrouter_music.generate 打桩（adhoc_music 与 api 层都经它）
    audio = b"ID3" + b"\x00" * 32

    def fake_generate(**kw):
        return openrouter_music.MusicResult(
            audio=audio,
            ext="mp3",
            transcript="la la",
            text_content="A clip.",
            usage_cost=0.04,
            seed_sent=kw.get("seed"),
            request_body={},
        )

    monkeypatch.setattr(openrouter_music, "generate", fake_generate)
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_music_models(client):
    resp = client.get("/music/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_model"] == "google/lyria-3-clip-preview"
    ids = [m["id"] for m in data["data"]]
    assert "google/lyria-3-clip-preview" in ids


def test_music_generate_returns_audio_url(client):
    resp = client.post(
        "/music/generate",
        json={"prompt": "接口实测", "seed": "7"},
    )
    assert resp.status_code == 200
    payload = resp.json()  # 同步通路：result 直接就是终态（无 status/轮询字段）
    assert payload["model"] == "google/lyria-3-clip-preview"
    assert payload["seed_sent"] == 7
    assert payload["cost_real"] == 0.04
    assert payload["manifest_path"].endswith("manifest.json")
    assert payload["audio_url"] == f"/music/{payload['run_id']}/audio"
    audio_resp = client.get(payload["audio_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"].startswith("audio/mpeg")


def test_music_generate_empty_prompt_is_400(client):
    resp = client.post("/music/generate", json={"prompt": "   "})
    assert resp.status_code == 400


def test_music_generate_bad_seed_is_400(client):
    resp = client.post("/music/generate", json={"prompt": "x", "seed": "abc"})
    assert resp.status_code == 400


def test_music_recent_list(client):
    client.post("/music/generate", json={"prompt": "列表实测"})
    resp = client.get("/music")
    assert resp.status_code == 200
    records = resp.json()
    assert records and records[0]["prompt"] == "列表实测"
    assert records[0]["has_audio"] is True


def test_music_manifest_endpoint(client):
    created = client.post("/music/generate", json={"prompt": "manifest 实测"}).json()
    resp = client.get(f"/music/{created['run_id']}/manifest")
    assert resp.status_code == 200
    assert resp.json()["request"]["prompt"] == "manifest 实测"


def test_music_routes_order_literal_paths_first(client):
    # 字面路径不能被 /music/{run_id}/… 的通配吞掉
    assert client.get("/music/models").status_code == 200


def test_music_audio_rejects_bad_run_id(client):
    resp = client.get("/music/..%2Fetc/audio")
    assert resp.status_code in (400, 404)


def test_music_audio_unknown_run_404(client):
    resp = client.get("/music/music_20991231-000000_unknown/audio")
    assert resp.status_code == 404


def test_music_delete(client):
    created = client.post("/music/generate", json={"prompt": "删除实测"}).json()
    resp = client.delete(f"/music/{created['run_id']}")
    assert resp.status_code == 200
    assert client.get(f"/music/{created['run_id']}/audio").status_code == 404
    assert client.get("/music").json() == []


def test_music_settings_roundtrip(client):
    # 设置页保存 openrouter_music.model → /settings 回读一致
    resp = client.put("/settings", json={"openrouter_music": {"model": "google/lyria-3-pro-preview"}})
    assert resp.status_code == 200
    assert resp.json()["openrouter_music"]["model"] == "google/lyria-3-pro-preview"
    # 默认链生效：/music/models 的 default_model 跟着换
    assert client.get("/music/models").json()["default_model"] == "google/lyria-3-pro-preview"
