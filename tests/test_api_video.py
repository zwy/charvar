"""Playground 视频的网页入口：/videos/* 接口（打桩 + MockTransport，不发真网）。"""
from __future__ import annotations

import io
import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import adhoc_video, openrouter_video, storage
from charvar.api import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    db_path = pathlib.Path(tmp_path) / "test.db"
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)


def _inject_catalog(tmp_path, monkeypatch):
    """给 /videos/models 与提交前校验注入一份本地目录（不打真网）。"""
    catalog = {
        "data": [
            {
                "id": "heygen/heygen-video-1",
                "name": "HeyGen Video",
                "supported_durations": [5, 6, 10],
                "supported_resolutions": ["480p", "768p", "2K"],
                "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
                "supported_frame_images": ["first_frame"],
                "seed": True,
                "pricing_skus": {"duration_seconds_480p": "0.02",
                                 "reference_duration_seconds_480p": "0.07",
                                 "reference_input_images": "0.04"},
            },
        ]
    }
    cache = pathlib.Path(tmp_path) / "openrouter" / "video-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(catalog), encoding="utf-8")


def _stub_modules(monkeypatch, *, final=None, submit_error=None, cost=0.12):
    poll_seq = []

    def fake_submit(body, *, api_key=None, timeout_s=None):
        if submit_error is not None:
            raise submit_error
        return openrouter_video.VideoJob("job-9", "pending")

    def fake_wait(job_id, **kw):
        raise AssertionError("web 通路不该阻塞轮询")

    def fake_poll(job_id, *, api_key=None, timeout_s=None):
        if poll_seq:
            return poll_seq.pop(0)
        return final or {"status": "completed", "unsigned_urls": ["https://x/0"],
                         "usage": {"cost": cost}}

    def fake_download(job_id, *, url=None, api_key=None, timeout_s=None):
        return b"\x00\x00\x00\x20ftypisom\x00" + b"\x00" * 16

    def fake_poster(video_path, run_dir):
        target = pathlib.Path(run_dir) / "thumbs" / "000000.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
        return target

    monkeypatch.setattr(openrouter_video, "submit", fake_submit)
    monkeypatch.setattr(openrouter_video, "wait_for_job", fake_wait)
    monkeypatch.setattr(openrouter_video, "poll", fake_poll)
    monkeypatch.setattr(openrouter_video, "download", fake_download)
    monkeypatch.setattr(adhoc_video, "_make_poster", fake_poster)
    return fake_poll


def _png_bytes(size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 8, 7)).save(buf, format="PNG")
    return buf.getvalue()


# ---------- 暂存参考图 ----------


def test_upload_and_delete_reference(client):
    resp = client.post("/videos/references", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert resp.status_code == 200, resp.text
    ref_id = resp.json()["id"]
    assert resp.json()["name"] == "a.png"
    got = client.get(f"/videos/reference/{ref_id}")
    assert got.status_code == 200
    assert client.delete(f"/videos/references/{ref_id}").json() == {"ok": True}
    assert client.get(f"/videos/reference/{ref_id}").status_code == 404


def test_illegal_reference_id_rejected(client):
    assert client.get("/videos/reference/../x").status_code in (404, 400)


# ---------- 模型目录 ----------


def test_video_models_endpoint_uses_cache(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    resp = client.get("/videos/models")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["default_model"] == "heygen/heygen-video-1"
    entry = payload["data"][0]
    assert entry["id"] == "heygen/heygen-video-1"
    assert entry["supported_durations"] == [5, 6, 10]
    assert entry["seed"] is True
    assert "pricing_skus" in entry


# ---------- 提交与轮询 ----------


def test_generate_creates_pending_then_status_ready(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    _stub_modules(monkeypatch)
    resp = client.post("/videos/generate", json={"prompt": "a woman dances"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["run_id"].startswith("video_")
    assert payload["job_id"] == "job-9"
    assert payload["status_url"] == f"/videos/{payload['run_id']}/status"

    status = client.get(payload["status_url"])
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "ready"
    assert body["video_url"] == f"/videos/{payload['run_id']}/video"
    assert body["cost"] == pytest.approx(0.12)

    video = client.get(f"/videos/{payload['run_id']}/video")
    assert video.status_code == 200
    assert video.content.startswith(b"\x00\x00\x00\x20ftyp")
    poster = client.get(f"/videos/{payload['run_id']}/poster")
    assert poster.status_code == 200
    assert poster.headers["content-type"].startswith("image/jpeg")
    manifest = client.get(f"/videos/{payload['run_id']}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["status"] == "ready"

    listed = client.get("/videos")
    assert listed.status_code == 200
    records = listed.json()
    assert records[0]["run_id"] == payload["run_id"]
    assert records[0]["has_video"] is True


def test_generate_with_first_frame_and_references(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    seen = {}

    def fake_submit(body, *, api_key=None, timeout_s=None):
        seen.update(body)
        return openrouter_video.VideoJob("job-10", "pending")

    def fake_poll(job_id, **kw):
        return {"status": "completed", "unsigned_urls": [], "usage": {"cost": 0.4}}
    monkeypatch.setattr(openrouter_video, "submit", fake_submit)
    monkeypatch.setattr(openrouter_video, "wait_for_job", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(openrouter_video, "poll", fake_poll)
    monkeypatch.setattr(openrouter_video, "download", lambda job_id, **kw: b"\x00\x00\x00\x20ftyp" + b"\x00" * 12)
    monkeypatch.setattr(adhoc_video, "_make_poster", lambda p, d: None)

    uploaded = client.post("/videos/references", files={"file": ("f.png", _png_bytes(), "image/png")}).json()["id"]
    resp = client.post("/videos/generate", json={
        "prompt": "the dancer in <image1> performs a spin",
        "duration": 6, "resolution": "480p", "aspect_ratio": "9:16", "seed": 3,
        "first_frame": {"staged_id": uploaded},
        "references": [{"staged_id": uploaded}],
    })
    assert resp.status_code == 200, resp.text
    # API 层按请求原样发：首帧进 frame_images、参考图进 input_references
    # （两者同给时官方语义 frame_images 优先；web UI 在 i2v 页签不并送参考图）
    assert seen["frame_images"][0]["frame_type"] == "first_frame"
    assert len(seen["input_references"]) == 1
    assert seen["duration"] == 6
    assert seen["seed"] == 3


def test_generate_bad_enum_rejected(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    _stub_modules(monkeypatch)
    resp = client.post("/videos/generate", json={"prompt": "p", "duration": 4})
    assert resp.status_code == 400
    assert "时长" in resp.json()["detail"]


def test_generate_submit_error_maps_400(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    _stub_modules(monkeypatch,
                  submit_error=openrouter_video.OpenRouterVideoAuthError("HTTP 401：bad key"))
    resp = client.post("/videos/generate", json={"prompt": "p"})
    assert resp.status_code == 400
    assert "401" in resp.json()["detail"]


def test_generate_missing_api_key(client, tmp_path, monkeypatch):
    _inject_catalog(tmp_path, monkeypatch)
    monkeypatch.delenv("OP_IMAGE_API_KEY")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    resp = client.post("/videos/generate", json={"prompt": "p"})
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_generate_with_subject_reference(client, tmp_path, monkeypatch):
    """主体档案参考图来源：kind 必须是已知槽位，主体必须存在。"""
    _inject_catalog(tmp_path, monkeypatch)
    _stub_modules(monkeypatch)
    resp = client.post("/videos/generate", json={
        "prompt": "p", "first_frame": {"subject_id": "no-such", "kind": "front"},
    })
    assert resp.status_code == 404  # _require_subject 404

    resp = client.post("/videos/generate", json={
        "prompt": "p", "references": [{"subject_id": None, "kind": None}],
    })
    assert resp.status_code == 400  # 缺来源


def test_status_404_and_illegal_run_id(client):
    assert client.get("/videos/video_nope/status").status_code == 404
    assert client.get("/videos/evil_/status").status_code in (400, 404)
