"""OpenRouter 视频通路单测：请求体组装、枚举校验、提交/轮询/下载、降级重试。

HTTP 层全走 MockTransport（不发真网）；模型目录用本地缓存文件注入。
"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from PIL import Image

from charvar import openrouter_video, storage


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
            "pricing_skus": {
                "duration_seconds_480p": "0.02",
                "duration_seconds_768p": "0.036",
                "duration_seconds_2k": "0.1",
                "reference_duration_seconds_480p": "0.07",
                "reference_input_images": "0.04",
            },
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


def _entry(env):
    return openrouter_video.video_model_entry("heygen/heygen-video-1")


# ---------- build_body ----------


def test_body_base_fields(env):
    body = openrouter_video.build_body(
        prompt="a dancer", model="heygen/heygen-video-1",
        duration=5, resolution="480p", aspect_ratio="9:16", seed=7,
    )
    assert body["model"] == "heygen/heygen-video-1"
    assert body["prompt"] == "a dancer"
    assert body["duration"] == 5
    assert body["resolution"] == "480p"
    assert body["aspect_ratio"] == "9:16"
    assert body["seed"] == 7


def test_body_rejects_out_of_enum(env):
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(prompt="p", model="heygen/heygen-video-1", duration=4)
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(prompt="p", model="heygen/heygen-video-1", resolution="1080p")
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(prompt="p", model="heygen/heygen-video-1", aspect_ratio="2:3")


def test_seed_skipped_when_model_declares_no_seed(env):
    body = openrouter_video.build_body(prompt="p", model="test/no-seed-model", seed=9)
    assert "seed" not in body


def test_first_frame_becomes_frame_images(env):
    ref = env / "frame.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(ref, format="PNG")
    body = openrouter_video.build_body(
        prompt="p", model="heygen/heygen-video-1", first_frame_path=ref,
    )
    frames = body["frame_images"]
    assert frames[0]["frame_type"] == "first_frame"
    assert frames[0]["type"] == "image_url"
    assert frames[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_first_frame_rejected_when_unsupported(env, monkeypatch):
    entry = dict(_entry(env))
    entry["supported_frame_images"] = ["last_frame"]
    monkeypatch.setattr(openrouter_video, "video_model_entry", lambda mid: entry)
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(prompt="p", model="heygen/heygen-video-1",
                                    first_frame_path=env / "missing.png")


def test_input_references_structure(env):
    refs = []
    for i in range(2):
        p = env / f"ref{i}.png"
        Image.new("RGB", (32, 32)).save(p, format="PNG")
        refs.append(p)
    body = openrouter_video.build_body(
        prompt="p", model="heygen/heygen-video-1", reference_paths=refs,
    )
    assert len(body["input_references"]) == 2
    assert body["input_references"][0]["type"] == "image_url"


def test_input_references_capped_at_four(env):
    refs = []
    for i in range(5):
        p = env / f"r{i}.png"
        Image.new("RGB", (32, 32)).save(p, format="PNG")
        refs.append(p)
    body = openrouter_video.build_body(prompt="p", model="heygen/heygen-video-1", reference_paths=refs)
    assert len(body["input_references"]) == 4


def test_body_rejects_empty_prompt(env):
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(prompt="  ", model="heygen/heygen-video-1")


# ---------- 模型目录 / 解析链 ----------


def test_resolve_model_precedence(env, monkeypatch):
    assert openrouter_video.resolve_model("m/a") == "m/a"
    monkeypatch.setenv("OP_VIDEO_MODEL", "m/env")
    assert openrouter_video.resolve_model(None) == "m/env"
    monkeypatch.delenv("OP_VIDEO_MODEL")
    assert openrouter_video.resolve_model(None) == "heygen/heygen-video-1"


def test_list_models_reads_cache(env):
    data = openrouter_video.list_models()
    ids = [m.get("id") for m in data.get("data") or []]
    assert "heygen/heygen-video-1" in ids


def test_model_choices(env):
    choices = openrouter_video.model_choices()
    assert {"id": "heygen/heygen-video-1", "name": "HeyGen Video"} in [dict(c) for c in choices]


# ---------- estimate_cost ----------


def test_estimate_cost_t2v(env):
    cost = openrouter_video.estimate_cost(
        _entry(env), seconds=10, resolution="480p",
    )
    assert cost == round(0.02 * 10, 4)


def test_estimate_cost_with_references(env):
    cost = openrouter_video.estimate_cost(
        _entry(env), seconds=10, resolution="480p", n_refs=1,
    )
    assert cost == round(0.07 * 10 + 0.04 * 1, 4)


def test_estimate_cost_fallback_sku(env):
    entry = {"pricing_skus": {"duration_seconds": "0.05"}}
    assert openrouter_video.estimate_cost(entry, seconds=10) == 0.5
    assert openrouter_video.estimate_cost({}, seconds=10) is None


# ---------- sniff ----------


def test_sniff_video_ext():
    assert openrouter_video.sniff_video_ext(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00") == "mp4"
    assert openrouter_video.sniff_video_ext(b"\x1a\x45\xdf\xa3" + b"\x00" * 8) == "webm"
    assert openrouter_video.sniff_video_ext(b"\x00\x00\x00\x18ftypqt  \x00\x00\x02\x00") == "mov"
    assert openrouter_video.sniff_video_ext(b"not a video") == "bin"


# ---------- submit / poll / download（MockTransport） ----------


class _FakeClientFactory:
    def __init__(self, handler):
        self._transport = httpx.MockTransport(handler)

    def __call__(self, timeout=300.0, **kw):
        outer = self

        class FakeClient:
            def __init__(self):
                self._inner = httpx.Client(transport=outer._transport)

            def __enter__(self):
                return self._inner

            def __exit__(self, *args):
                self._inner.close()

        return FakeClient()


def _install(env, handler):
    factory = _FakeClientFactory(handler)
    openrouter_video._http_client = factory  # type: ignore[assignment]


def test_submit_returns_job(env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(202, json={"id": "job-1", "status": "pending",
                                         "polling_url": "https://openrouter.ai/api/v1/videos/job-1"})

    _install(env, handler)
    job = openrouter_video.submit({"model": "heygen/heygen-video-1", "prompt": "dance"})
    assert job.job_id == "job-1"
    assert job.status == "pending"
    assert seen["body"]["prompt"] == "dance"


def test_submit_degrades_after_param_rejection(env):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        attempts.append(body)
        if "seed" in body:
            return httpx.Response(400, json={"error": {"message": "unknown parameter `seed`"}})
        return httpx.Response(202, json={"id": "job-2", "status": "pending"})

    _install(env, handler)
    job = openrouter_video.submit({"model": "heygen/heygen-video-1", "prompt": "dance", "seed": 1})
    assert job.job_id == "job-2"
    assert len(attempts) == 2
    assert "seed" not in attempts[1]


def test_submit_auth_error_not_degraded(env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    _install(env, handler)
    with pytest.raises(openrouter_video.OpenRouterVideoAuthError):
        openrouter_video.submit({"model": "m/x", "prompt": "dance"})


def test_submit_requires_api_key(env, monkeypatch):
    monkeypatch.delenv("OP_IMAGE_API_KEY")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.submit({"model": "m/x", "prompt": "dance"})


def test_poll_and_download(env):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos/job-3"):
            return httpx.Response(200, json={"id": "job-3", "status": "completed",
                                             "unsigned_urls": ["https://x/y"]})
        if request.url.path.endswith("/videos/job-3/content"):
            assert request.headers.get("Authorization") == "Bearer sk-test"
            return httpx.Response(200, content=b"\x00\x00\x00\x20ftypisom\x00" + b"\x00" * 16)
        return httpx.Response(404)

    _install(env, handler)
    payload = openrouter_video.poll("job-3")
    assert payload["status"] == "completed"
    data = openrouter_video.download("job-3")
    assert openrouter_video.sniff_video_ext(data) == "mp4"


def test_wait_for_job_polls_until_done(env):
    statuses = iter(["pending", "in_progress", "completed"])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "job-4", "status": next(statuses)})

    _install(env, handler)
    sleeps: list[float] = []
    payload = openrouter_video.wait_for_job("job-4", sleep=sleeps.append)
    assert payload["status"] == "completed"
    assert len(calls) == 3
    assert sleeps == [5.0, 5.0]


def test_wait_for_job_timeout_keeps_status(env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job-5", "status": "in_progress"})

    _install(env, handler)
    with pytest.raises(TimeoutError):
        openrouter_video.wait_for_job("job-5", timeout_s=0.0, sleep=lambda s: None)


def test_error_classification(env):
    def deny(status, message):
        return lambda request: httpx.Response(status, json={"error": {"message": message}})

    _install(env, deny(429, "slow down"))
    with pytest.raises(openrouter_video.OpenRouterVideoRateLimitError):
        openrouter_video.poll("job-x")
    _install(env, deny(404, "no such job"))
    with pytest.raises(openrouter_video.OpenRouterVideoNotFoundError):
        openrouter_video.poll("job-x")
    _install(env, deny(403, "content policy violation"))
    with pytest.raises(openrouter_video.OpenRouterVideoFetchError):
        openrouter_video.poll("job-x")


# ---------- estimate_cost：grok 系美分键名 ----------


def test_estimate_cost_grok_cents_keys(env):
    entry = {"pricing_skus": {"cents_per_video_output_second_480p": "2",
                              "cents_per_video_output_second_1080p": "14",
                              "cents_per_image_input": "1"}}
    assert openrouter_video.estimate_cost(entry, seconds=5, resolution="480p") == 0.1
    assert openrouter_video.estimate_cost(entry, seconds=5, resolution="1080p") == 0.7
    # 带输入图：每秒价不变 + 每张 $0.01
    assert openrouter_video.estimate_cost(
        entry, seconds=5, resolution="480p", n_refs=1) == 0.11


def test_estimate_cost_grok_falls_back_from_ref_rate(env):
    entry = {"pricing_skus": {"duration_seconds_480p": "0.02",
                              "reference_input_images": "0.04"}}
    # heygen 系参考图档缺失 → 回退普档每秒价（估算只做展示）
    assert openrouter_video.estimate_cost(
        entry, seconds=5, resolution="480p", n_refs=2) == 0.18


def test_submit_degrades_on_not_supported_for_this_model(env):
    """grok 的拒参话式：input_references 后接 not-supported-for-this-model。"""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        attempts.append(body)
        if "input_references" in body:
            return httpx.Response(
                400, json={"error": {"message": "`input_references` is not supported for this model."}}
            )
        return httpx.Response(202, json={"id": "job-6", "status": "pending"})

    _install(env, handler)
    job = openrouter_video.submit(
        {"model": "x-ai/grok-imagine-video-1.5-lite", "prompt": "dance",
         "input_references": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,x"}}]}
    )
    assert job.job_id == "job-6"
    assert len(attempts) == 2
    assert "input_references" not in attempts[1]


# ---------- ⑤⑥：尾帧 / 参考视频（input_references video_url） ----------


def _ftyp_bytes(brand=b"isom"):
    return b"\x00\x00\x00\x20ftyp" + brand + b"\x00\x00\x02\x00isomiso2\x00" + b"\x00" * 16


def test_video_reference_becomes_video_url_part(env):
    p = env / "clip.mp4"
    p.write_bytes(_ftyp_bytes())
    body = openrouter_video.build_body(
        prompt="p", model="heygen/heygen-video-1", reference_videos=[p],
    )
    parts = body["input_references"]
    assert parts[0]["type"] == "video_url"
    assert parts[0]["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_video_reference_rejects_unknown_container(env):
    p = env / "clip.txt"
    p.write_bytes(b"not a video at all")
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(
            prompt="p", model="heygen/heygen-video-1", reference_videos=[p],
        )


def test_image_and_video_refs_mixed(env):
    img = env / "a.png"
    Image.new("RGB", (16, 16)).save(img, format="PNG")
    vid = env / "b.mp4"
    vid.write_bytes(_ftyp_bytes())
    body = openrouter_video.build_body(
        prompt="p", model="heygen/heygen-video-1",
        reference_paths=[img], reference_videos=[vid],
    )
    types = [p["type"] for p in body["input_references"]]
    assert types == ["image_url", "video_url"]
    # 参考视频按原始容器发（不进图片预处理）
    assert body["input_references"][1]["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_combined_references_capped_at_four(env):
    imgs = []
    for i in range(3):
        p = env / f"m{i}.png"
        Image.new("RGB", (16, 16)).save(p, format="PNG")
        imgs.append(p)
    vids = []
    for i in range(2):
        p = env / f"v{i}.mp4"
        p.write_bytes(_ftyp_bytes())
        vids.append(p)
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(
            prompt="p", model="heygen/heygen-video-1",
            reference_paths=imgs, reference_videos=vids,
        )


def test_last_frame_supported_and_rejected(env, monkeypatch):
    entry = dict(_entry(env))
    entry["supported_frame_images"] = ["first_frame", "last_frame"]
    monkeypatch.setattr(openrouter_video, "video_model_entry", lambda mid: entry)
    last = env / "last.png"
    Image.new("RGB", (16, 16)).save(last, format="PNG")
    body = openrouter_video.build_body(
        prompt="p", model="heygen/heygen-video-1", last_frame_path=last,
    )
    assert body["frame_images"][0]["frame_type"] == "last_frame"

    entry["supported_frame_images"] = ["first_frame"]
    monkeypatch.setattr(openrouter_video, "video_model_entry", lambda mid: entry)
    with pytest.raises(openrouter_video.OpenRouterVideoParamError):
        openrouter_video.build_body(
            prompt="p", model="heygen/heygen-video-1", last_frame_path=last,
        )


def test_sniff_video_ref_formats(tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(_ftyp_bytes())
    mov = tmp_path / "b.mov"
    mov.write_bytes(_ftyp_bytes(b"qt  "))
    webm = tmp_path / "c.webm"
    webm.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 20)
    assert openrouter_video.sniff_video_ref(mp4) == "video/mp4"
    assert openrouter_video.sniff_video_ref(mov) == "video/mov"
    assert openrouter_video.sniff_video_ref(webm) == "video/webm"


# ---------- pricing_hints：列表用的每秒价提示 ----------

def test_pricing_hints_heygen_dollars_per_second():
    entry = {"pricing_skus": {"duration_seconds_480p": "0.02", "duration_seconds_2k": "0.1"},
             "supported_resolutions": ["480p", "2K"]}
    assert openrouter_video.pricing_hints(entry) == ["480p $0.0200/s", "2K $0.1000/s"]


def test_pricing_hints_grok_cents_per_second():
    entry = {"pricing_skus": {"cents_per_video_output_second_480p": "2"},
             "supported_resolutions": ["480p"]}
    assert openrouter_video.pricing_hints(entry) == ["480p $0.0200/s"]


def test_pricing_hints_skips_tier_without_own_sku():
    """没有自己 SKU 的档位宁可不出价 —— 兜底会把别的档位的价标错。"""
    entry = {"pricing_skus": {"duration_seconds_480p": "0.02"},
             "supported_resolutions": ["480p", "1080p"]}
    assert openrouter_video.pricing_hints(entry) == ["480p $0.0200/s"]


def test_pricing_hints_empty_when_no_pricing():
    assert openrouter_video.pricing_hints({}) == []
    assert openrouter_video.pricing_hints({"pricing_skus": {}}) == []
