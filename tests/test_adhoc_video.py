"""Playground 视频（charvar.adhoc_video）：提交/轮询/收片与落盘（打桩 OpenRouter，不发真网）。"""
from __future__ import annotations

import io
import json
import pathlib
import time

import pytest
from PIL import Image

from charvar import adhoc_video, openrouter_video, storage


CATALOG = {
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
        {
            "id": "test/no-seed-model",
            "name": "No Seed Model",
            "supported_durations": [5, 10],
            "supported_resolutions": ["480p"],
            "supported_aspect_ratios": ["16:9"],
            "seed": False,
            "pricing_skus": {"duration_seconds": "0.05"},
        },
    ]
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "video-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    return tmp_path


def _png(path, size=(64, 48)):
    Image.new("RGB", size, (10, 20, 30)).save(path, format="PNG")
    return path


def _mp4_bytes():
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2\x00" + b"\x00" * 32


def _stub_openrouter(monkeypatch, *, poll_payloads=None, final=None, cost=0.12,
                     submit_error=None):
    """打桩 openrouter_video 四件套；记录每次提交的请求体。"""
    calls = {"submit": [], "download": []}
    poll_seq = list(poll_payloads or [])

    def fake_submit(body, *, api_key=None, timeout_s=None):
        calls["submit"].append(dict(body))
        if submit_error is not None:
            raise submit_error
        return openrouter_video.VideoJob("job-1", "pending")

    def fake_wait(job_id, *, poll_interval=5.0, timeout_s=900.0, sleep=time.sleep,
                  timeout_error=None):
        payload = poll_seq.pop(0) if poll_seq else (final or {
            "status": "completed", "unsigned_urls": ["https://x/0"],
            "usage": {"cost": cost},
        })
        return payload

    def fake_poll(job_id, *, api_key=None, timeout_s=None):
        if poll_seq:
            return poll_seq.pop(0)
        return final or {"status": "in_progress", "id": job_id}

    def fake_download(job_id, *, url=None, api_key=None, timeout_s=None):
        calls["download"].append(job_id)
        return _mp4_bytes()

    monkeypatch.setattr(openrouter_video, "submit", fake_submit)
    monkeypatch.setattr(openrouter_video, "wait_for_job", fake_wait)
    monkeypatch.setattr(openrouter_video, "poll", fake_poll)
    monkeypatch.setattr(openrouter_video, "download", fake_download)
    return calls


def _fake_poster(monkeypatch):
    def make(video_path, run_dir):
        target = pathlib.Path(run_dir) / "thumbs" / "000000.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        return target
    monkeypatch.setattr(adhoc_video, "_make_poster", make)


def _req(**kw):
    base = dict(prompt="dance clip", backend="openrouter", seed=7,
                duration=5, resolution="480p", aspect_ratio="9:16")
    base.update(kw)
    return adhoc_video.VideoRequest(**base)


# ---------- 生成主流程 ----------


def test_generate_wait_flow(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    result = adhoc_video.generate(_req())
    assert result.status == "ready"
    assert result.run_id.startswith("video_")
    assert result.job_id == "job-1"
    assert result.cost == pytest.approx(0.12)
    vd = pathlib.Path(result.video_path)
    assert vd.exists() and vd.parent.name == "original"
    assert pathlib.Path(result.poster_path).exists()
    manifest = adhoc_video.read_manifest(result.run_id)
    assert manifest["status"] == "ready"
    assert manifest["job"]["id"] == "job-1"
    assert manifest["job"]["model"] == "heygen/heygen-video-1"
    assert manifest["result"]["duration_ms"] >= 0
    body = calls["submit"][0]
    assert body["duration"] == 5
    assert body["resolution"] == "480p"
    assert body["aspect_ratio"] == "9:16"
    assert body["seed"] == 7


def test_generate_nowait_then_refresh_ready(env, monkeypatch):
    _stub_openrouter(monkeypatch, final={"status": "completed",
                                         "unsigned_urls": ["https://x/0"],
                                         "usage": {"cost": 0.1}})
    _fake_poster(monkeypatch)
    pending = adhoc_video.generate(_req(wait=False))
    assert pending.status == "pending"
    assert pending.manifest_path
    summary = adhoc_video.refresh(pending.run_id)
    assert summary["status"] == "ready"
    assert summary["cost"] == pytest.approx(0.1)
    manifest = adhoc_video.read_manifest(pending.run_id)
    assert manifest["status"] == "ready"
    assert pathlib.Path(manifest["result"]["video_path"]).exists()


def test_refresh_intermediate_then_ready(env, monkeypatch):
    _stub_openrouter(
        monkeypatch,
        poll_payloads=[{"status": "in_progress"}, {"status": "completed",
                                                    "unsigned_urls": [],
                                                    "usage": {"cost": 0.02}}],
    )
    _fake_poster(monkeypatch)
    pending = adhoc_video.generate(_req(wait=False))
    first = adhoc_video.refresh(pending.run_id)
    assert first["status"] == "in_progress"
    done = adhoc_video.refresh(pending.run_id)
    assert done["status"] == "ready"


def test_refresh_marks_failed(env, monkeypatch):
    _stub_openrouter(monkeypatch, final={"status": "failed", "error": "provider blew up"})
    pending = adhoc_video.generate(_req(wait=False))
    summary = adhoc_video.refresh(pending.run_id)
    assert summary["status"] == "failed"
    assert "provider blew up" in summary["error"]
    records = adhoc_video.list_recent()
    assert records[0]["status"] == "failed"
    assert records[0]["has_video"] is False


def test_refresh_is_idempotent_after_ready(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    pending = adhoc_video.generate(_req(wait=False))
    adhoc_video.refresh(pending.run_id)
    poll_count = len(calls["download"])
    adhoc_video.refresh(pending.run_id)
    assert len(calls["download"]) == poll_count  # 终态后不再打 OpenRouter


def test_refresh_poll_error_keeps_state(env, monkeypatch):
    _stub_openrouter(monkeypatch)
    pending = adhoc_video.generate(_req(wait=False))
    monkeypatch.setattr(openrouter_video, "poll",
                        lambda job_id, **kw: (_ for _ in ()).throw(
                            openrouter_video.OpenRouterVideoFetchError("boom")))
    summary = adhoc_video.refresh(pending.run_id)
    assert "boom" in summary["poll_error"]
    assert summary["status"] == "in_progress"


def test_generate_timeout_keeps_in_progress(env, monkeypatch):
    _stub_openrouter(monkeypatch)

    def fake_wait(job_id, **kw):
        raise adhoc_video.AdhocVideoTimeout("超时未完成")

    monkeypatch.setattr(openrouter_video, "wait_for_job", fake_wait)
    with pytest.raises(adhoc_video.AdhocVideoTimeout):
        adhoc_video.generate(_req(wait=True))
    records = adhoc_video.list_recent()
    assert records[0]["status"] == "in_progress"


def test_submit_failure_leaves_failed_manifest(env, monkeypatch):
    _stub_openrouter(monkeypatch, submit_error=openrouter_video.OpenRouterVideoAuthError("401 bad key"))
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req())
    records = adhoc_video.list_recent()
    assert records[0]["status"] == "failed"
    assert "401" in records[0]["error"]


def test_does_not_write_runs_row(env, monkeypatch):
    _stub_openrouter(monkeypatch)
    adhoc_video.generate(_req(wait=False))
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# ---------- 提示词改写与种子语义 ----------


def test_prompt_rewrites_tags_with_first_frame(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    frame = _png(env / "frame.png")
    adhoc_video.generate(_req(first_frame=frame, prompt="the dancer in <image1> dances"))
    body = calls["submit"][0]
    assert "the first reference image" in body["prompt"]
    assert "<image1>" not in body["prompt"]


def test_prompt_strips_tags_without_images(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    adhoc_video.generate(_req(prompt="Use <image1> as the reference. A woman dances."))
    body = calls["submit"][0]
    assert "<image1>" not in body["prompt"]
    assert "Use" not in body["prompt"]


def test_seed_not_sent_for_no_seed_model(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    adhoc_video.generate(_req(model="test/no-seed-model", seed=9, aspect_ratio="16:9"))
    body = calls["submit"][0]
    assert "seed" not in body
    assert body["model"] == "test/no-seed-model"


def test_seed_clamps_to_safe_range(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    adhoc_video.generate(_req(seed=2**64 + 5))
    body = calls["submit"][0]
    assert 0 <= body["seed"] <= 2**53 - 1


def test_first_frame_encoded_as_frame_image(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    frame = _png(env / "frame.png")
    adhoc_video.generate(_req(first_frame=frame))
    body = calls["submit"][0]
    assert body["frame_images"][0]["frame_type"] == "first_frame"
    assert body["frame_images"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ---------- 校验 ----------


def test_empty_prompt_rejected(env):
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.validate(adhoc_video.VideoRequest(prompt="  "))


def test_bad_duration_rejected(env, monkeypatch):
    calls = _stub_openrouter(monkeypatch)
    for bad in (0, -3, True):
        with pytest.raises(adhoc_video.AdhocVideoError):
            adhoc_video.generate(_req(duration=bad))
    assert calls["submit"] == []  # 提交前拦下，没浪费请求


def test_too_many_references_rejected(env, tmp_path):
    refs = [_png(tmp_path / f"r{i}.png") for i in range(5)]
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req(reference_paths=refs))


def test_missing_first_frame_rejected(env, tmp_path):
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req(first_frame=tmp_path / "nope.png"))


# ---------- --out 与暂存参考图 ----------


def test_out_copies_video(env, monkeypatch, tmp_path):
    _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    out = tmp_path / "out" / "dance.mp4"
    result = adhoc_video.generate(_req(out=out))
    assert pathlib.Path(result.video_path) == out
    assert out.exists()


def test_staged_reference_roundtrip_and_cleanup(env, monkeypatch):
    _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    data = io.BytesIO()
    Image.new("RGB", (32, 32)).save(data, format="PNG")
    saved = adhoc_video.save_staged_reference(data.getvalue(), "a.png")
    assert saved.parent.name == "_refs"
    ref = adhoc_video.staged_reference(saved.stem)
    assert ref.exists()
    req = _req(reference_paths=[saved], staged_ref_ids=[saved.stem])
    result = adhoc_video.generate(req)
    assert result.status == "ready"
    assert not saved.exists()  # ready 后清掉
    assert adhoc_video.list_recent()[0]["has_video"] is True


def test_staged_reference_kept_on_failure(env, monkeypatch):
    _stub_openrouter(monkeypatch, submit_error=openrouter_video.OpenRouterVideoFetchError("off"))
    data = io.BytesIO()
    Image.new("RGB", (32, 32)).save(data, format="PNG")
    saved = adhoc_video.save_staged_reference(data.getvalue())
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.generate(_req(reference_paths=[saved], staged_ref_ids=[saved.stem]))
    assert saved.exists()


def test_staged_reference_bad_id(env):
    with pytest.raises(adhoc_video.AdhocVideoError):
        adhoc_video.staged_reference("../evil")


def test_prune_staged_references(env, monkeypatch):
    data = io.BytesIO()
    Image.new("RGB", (32, 32)).save(data, format="PNG")
    saved = adhoc_video.save_staged_reference(data.getvalue())
    old = time.time() - 25 * 3600
    import os
    os.utime(saved, (old, old))
    assert adhoc_video.prune_staged_references() == 1
    assert not saved.exists()


# ---------- run_id 与列表 ----------


def test_illegal_run_id_rejected(env):
    for bad in ("video_../x", "adhoc_1", "video_", "", "video_x; rm"):
        with pytest.raises(adhoc_video.AdhocVideoError):
            adhoc_video.run_dir(bad)


def test_list_recent_fields(env, monkeypatch):
    _stub_openrouter(monkeypatch)
    _fake_poster(monkeypatch)
    result = adhoc_video.generate(_req())
    records = adhoc_video.list_recent()
    assert records[0]["run_id"] == result.run_id
    assert records[0]["backend"] == "openrouter"
    assert records[0]["duration"] == 5
    assert records[0]["resolution"] == "480p"
    assert records[0]["has_video"] is True
