"""OpenRouter 生图通路单测：请求体组装、提示词改写、参考图下采样、API 落库落盘。

HTTP 层全走 monkeypatch（不打真网）。
"""
from __future__ import annotations

import base64
import io
import json
import pathlib
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import openrouter_image, storage
from charvar.api import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------- build_generate_body ----------


def test_body_uses_size_not_aspect_ratio():
    """画幅走 size 显式像素；不传 aspect_ratio / resolution（冲突会被 provider 400）。"""
    body = openrouter_image.build_generate_body(
        prompt="a red panda", model="m/x", width=704, height=1024
    )
    assert body == {"model": "m/x", "prompt": "a red panda", "size": "704x1024"}
    assert "aspect_ratio" not in body
    assert "resolution" not in body


def test_body_includes_seed_and_references(tmp_path):
    ref = tmp_path / "a.png"
    ref.write_bytes(_png_bytes())
    body = openrouter_image.build_generate_body(
        prompt="p",
        model="m/x",
        width=1024,
        height=576,
        seed=42,
        reference_paths=[ref],
    )
    assert body["seed"] == 42
    assert body["size"] == "1024x576"
    assert len(body["input_references"]) == 1
    entry = body["input_references"][0]
    assert entry["type"] == "image_url"
    assert entry["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_seed_dropped_when_model_declares_params_without_seed():
    """清单声明了参数却没有 seed（如 flux-3-image-klein）→ 不发 seed，别让 provider 400。"""
    caps = openrouter_image.ModelCapabilities(
        model_id="black-forest-labs/flux-3-image-klein",
        declared=frozenset({"resolution", "aspect_ratio", "n", "input_references"}),
    )
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1024, height=576, seed=42, capabilities=caps
    )
    assert "seed" not in body


def test_aspect_ratio_used_when_manifest_has_no_size():
    """清单声明了参数却没列 size（flux-3 只收分辨率档+aspect_ratio）→ 不发 size。"""
    caps = openrouter_image.ModelCapabilities(
        model_id="black-forest-labs/flux-3-image-klein",
        declared=frozenset({"resolution", "aspect_ratio", "n", "input_references"}),
        aspect_ratios=("21:9", "16:9", "2:3", "1:1"),
        resolutions=("768", "1K", "1.5K", "2K", "4K"),
    )
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1344, height=2048, aspect_ratio="2:3",
        resolution="1k", capabilities=caps,
    )
    assert "size" not in body
    assert body["aspect_ratio"] == "2:3"
    assert body["resolution"] == "1K"  # 档位归一进枚举，随 aspect_ratio 一起发


def test_size_falls_back_when_aspect_not_in_enum():
    """声明了参数、没列 size，但调用方的 aspect 不在枚举里 → 仍发 size（靠降级兜底）。"""
    caps = openrouter_image.ModelCapabilities(
        model_id="m/x",
        declared=frozenset({"resolution", "aspect_ratio"}),
        aspect_ratios=("16:9", "1:1"),
    )
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1000, height=1000, aspect_ratio="21:9", capabilities=caps
    )
    assert body["size"] == "1000x1000"
    assert "aspect_ratio" not in body


def test_size_swapped_after_probe_rejection():
    """探测缓存记过 size 被拒（清单没声明时）→ 下次直接走 aspect_ratio，不再浪费一次 400。"""
    caps = openrouter_image.ModelCapabilities(
        model_id="m/x", declared=frozenset(), rejected=frozenset({"size"}),
        aspect_ratios=("1:1", "2:3"),
    )
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1344, height=2048, aspect_ratio="2:3", capabilities=caps
    )
    assert "size" not in body
    assert body["aspect_ratio"] == "2:3"


def test_rejected_parameter_matches_not_pixel_dimensions():
    """provider 句式 "takes a resolution tier … not pixel dimensions. Received size …" 认出 size。"""
    message = (
        "Black Forest Labs FLUX 3 Image takes a resolution tier (768, 1K, 1.5K, 2K, 4K) "
        'plus aspect_ratio, not pixel dimensions. Received size "1344x2048".'
    )
    assert openrouter_image._rejected_parameter(message) == "size"


def test_seed_sent_when_declared():
    """清单声明了 seed → 正常发送并收敛到安全范围。"""
    caps = openrouter_image.ModelCapabilities(
        model_id="m/declares-seed", declared=frozenset({"seed", "n"})
    )
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/declares-seed", width=1024, height=576,
        seed=2**60, capabilities=caps,
    )
    assert body["seed"] == openrouter_image.normalize_seed(2**60)


def test_rejected_parameter_matches_does_not_support():
    """provider 句式 "… FLUX 3 Image does not support: seed." 也要认出参数名（触发降级重试）。"""
    assert (
        openrouter_image._rejected_parameter(
            "Black Forest Labs FLUX 3 Image does not support: seed."
        )
        == "seed"
    )


def test_seed_is_clamped_to_safe_integer_range():
    """charvar 的 subseed 是 64 位 sha256，超出 JS 安全整数会被 provider 400 拒绝。"""
    subseed = 4854601689830238561  # composer 实际产出的量级
    body = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1024, height=1024, seed=subseed
    )
    assert body["seed"] <= 2**53 - 1
    assert body["seed"] >= 0
    # 同一输入仍得同一结果（可复现）
    again = openrouter_image.build_generate_body(
        prompt="p", model="m/x", width=1024, height=1024, seed=subseed
    )
    assert again["seed"] == body["seed"]


def test_body_rejects_empty_prompt_and_bad_size():
    with pytest.raises(openrouter_image.OpenRouterParamError):
        openrouter_image.build_generate_body(prompt="  ", model="m/x", width=1, height=1)
    with pytest.raises(openrouter_image.OpenRouterParamError):
        openrouter_image.build_generate_body(prompt="p", model="", width=1, height=1)
    with pytest.raises(openrouter_image.OpenRouterParamError):
        openrouter_image.build_generate_body(prompt="p", model="m/x", width=0, height=10)


# ---------- 提示词改写 ----------


def test_rewrite_tags_to_ordinals_keeps_grammar():
    """<imageN> → 序数词，句子不留残缺（不能变成 "exactly as shown in ;"）。"""
    text = (
        "Create an image of the person in the character sheet in <image1>. "
        "Keep the identity exactly as shown in <image1>; copy the outfit from <image2>."
    )
    out = openrouter_image.rewrite_reference_tags(text)
    assert "<image1>" not in out
    assert "<image2>" not in out
    assert "in the character sheet." in out
    assert "exactly as shown in the first reference image" in out
    assert "copy the outfit from the second reference image" in out


def test_rewrite_handles_out_of_range_index():
    out = openrouter_image.rewrite_reference_tags("see <image9> now")
    assert "<image9>" not in out
    assert "the reference image" in out


def test_strip_reference_tags_removes_pointing_clauses():
    """无参考图时整段摘掉指向子句，不留残句。

    没有参考图时 "in the character sheet" 本身也失去意义（指的是一张不存在的
    设定图），整段一起去掉。
    """
    out = openrouter_image.strip_reference_tags(
        "Create an image of the person in the character sheet in <image1>. "
        "Use <image1> as the reference for the character's identity. "
        "Create one expressive chat sticker featuring the same single character as <image1>."
    )
    assert "<image" not in out
    assert "character sheet" not in out
    assert "for the character's identity" not in out
    assert "as ." not in out
    assert "Use  as the reference" not in out
    assert out.startswith("Create an image of the person.")


def test_strip_keeps_unrelated_words():
    """别误伤不含标签的普通句子。"""
    text = "Full-body shot of one person, standing upright, facing the camera."
    assert openrouter_image.strip_reference_tags(text) == text


# ---------- 参考图下采样 ----------


def test_encode_reference_downsamples_and_returns_jpeg_data_url(tmp_path):
    ref = tmp_path / "big.png"
    Image.new("RGB", (2048, 1152), (5, 5, 5)).save(ref, format="PNG")
    url = openrouter_image.encode_reference(ref, max_side=1024)
    assert url.startswith("data:image/jpeg;base64,")
    payload = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1024


# ---------- 错误分类 ----------


def _error_from(status: int, message: str) -> openrouter_image.OpenRouterImageError:
    resp = httpx.Response(status, json={"error": {"message": message}})
    return openrouter_image._error_from_response(resp)


def test_error_classification():
    assert isinstance(_error_from(401, "bad key"), openrouter_image.OpenRouterAuthError)
    assert isinstance(_error_from(402, "no credits"), openrouter_image.OpenRouterAuthError)
    assert isinstance(_error_from(429, "slow down"), openrouter_image.OpenRouterRateLimitError)
    assert isinstance(_error_from(404, "no such model"), openrouter_image.OpenRouterNotFoundError)
    assert isinstance(_error_from(500, "boom"), openrouter_image.OpenRouterFetchError)


def test_403_content_policy_is_not_auth_error():
    """上游 provider 用 403 拒绝违规 prompt ≠ key 失效（别误导用户查 key）。"""
    err = _error_from(403, "violation of provider Terms Of Service: content policy")
    assert isinstance(err, openrouter_image.OpenRouterFetchError)
    assert not isinstance(err, openrouter_image.OpenRouterAuthError)


def test_403_without_policy_keyword_is_auth_error():
    err = _error_from(403, "Only management keys can perform this operation")
    assert isinstance(err, openrouter_image.OpenRouterAuthError)


# ---------- 配置解析 ----------


def test_resolve_api_key_falls_back_to_llm_key(monkeypatch):
    monkeypatch.delenv("OP_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-llm")
    assert openrouter_image.resolve_api_key() == "sk-llm"
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-or")
    assert openrouter_image.resolve_api_key() == "sk-or"
    assert openrouter_image.resolve_api_key("sk-explicit") == "sk-explicit"


def test_resolve_model_precedence(monkeypatch):
    monkeypatch.delenv("OP_IMAGE_T2I_MODEL", raising=False)
    monkeypatch.delenv("OP_IMAGE_I2I_MODEL", raising=False)
    monkeypatch.setenv("OP_IMAGE_MODEL", "env/model")
    assert openrouter_image.resolve_model() == "env/model"
    assert openrouter_image.resolve_model("explicit/model") == "explicit/model"
    monkeypatch.delenv("OP_IMAGE_MODEL", raising=False)
    assert openrouter_image.resolve_model() == "meta/muse-image"


def test_resolve_model_per_mode_env(monkeypatch):
    """模式级 env 优先于旧的公共 OP_IMAGE_MODEL；i2i / t2i 各取各的。"""
    monkeypatch.setenv("OP_IMAGE_MODEL", "legacy/model")
    monkeypatch.setenv("OP_IMAGE_T2I_MODEL", "t2i/model")
    monkeypatch.setenv("OP_IMAGE_I2I_MODEL", "i2i/model")
    assert openrouter_image.resolve_model() == "t2i/model"
    assert openrouter_image.resolve_model(mode="i2i") == "i2i/model"
    monkeypatch.delenv("OP_IMAGE_I2I_MODEL", raising=False)
    assert openrouter_image.resolve_model(mode="i2i") == "legacy/model"
    assert openrouter_image.resolve_model("explicit/model") == "explicit/model"


def test_env_mode_value(monkeypatch):
    monkeypatch.setenv("OP_IMAGE_T2I_RESOLUTION", "2K")
    monkeypatch.setenv("OP_IMAGE_I2I_QUALITY", "high")
    assert openrouter_image.env_mode_value("t2i", "resolution") == "2K"
    assert openrouter_image.env_mode_value("i2i", "quality") == "high"
    assert openrouter_image.env_mode_value("t2i", "quality") == ""


def test_default_model_is_muse_image(monkeypatch):
    """默认模型是 meta/muse-image（$0.01/张，比 hy-image 便宜 2.4 倍）。"""
    for name in ("OP_IMAGE_MODEL", "OP_IMAGE_T2I_MODEL", "OP_IMAGE_I2I_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert openrouter_image._DEFAULT_MODEL == "meta/muse-image"


# ---------- generate_image（打桩 HTTP） ----------


def _fake_generate(monkeypatch, handler):
    class FakeClient:
        def __init__(self, *args, **kw):
            self._inner = httpx.Client(transport=httpx.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(openrouter_image, "_http_client", lambda *args, **kw: FakeClient(*args, **kw))


def test_generate_image_decodes_b64(monkeypatch):
    png = _png_bytes()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "created": 1748372400,
                "data": [{"b64_json": base64.b64encode(png).decode(), "media_type": "image/png"}],
                "usage": {"cost": 0.024},
            },
        )

    _fake_generate(monkeypatch, handler)
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    result = openrouter_image.generate_image(prompt="a panda", width=704, height=1024, seed=7)
    assert result.images == [png]
    assert result.media_types == ["image/png"]
    assert result.cost == 0.024
    assert seen["body"]["size"] == "704x1024"
    assert seen["body"]["seed"] == 7


def test_generate_image_requires_api_key(monkeypatch):
    monkeypatch.delenv("OP_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(openrouter_image.OpenRouterParamError):
        openrouter_image.generate_image(prompt="a panda", width=704, height=1024)


def test_generate_image_missing_b64_raises_parse_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"created": 1, "data": [{"media_type": "image/png"}]})

    _fake_generate(monkeypatch, handler)
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    with pytest.raises(openrouter_image.OpenRouterParseError):
        openrouter_image.generate_image(prompt="a panda", width=704, height=1024)


# ---------- API 层 ----------


def _make_run(client) -> str:
    client.post(
        "/subjects",
        json={"id": "demo", "name": "演示", "subject_noun": "woman", "identity_lock": ["silver hair"]},
    )
    gen = client.post(
        "/generate", json={"subject_id": "demo", "preset": "q21_default_30", "seed": 42, "count": 2}
    )
    return gen.json()["run_id"]


def test_openrouter_push_saves_images_and_records(client, monkeypatch, tmp_path):
    """批量推送：产物落 original/ + 缩略图，openrouter_images 表有记录。"""
    monkeypatch.delenv("OP_IMAGE_MODEL", raising=False)
    run_id = _make_run(client)

    def fake_generate(**kwargs):
        return openrouter_image.GenerateImageResult(
            generation_id="g1",
            created=1,
            images=[_png_bytes(32, 48)],
            media_types=["image/png"],
            usage={"cost": 0.024},
            request_body={},
        )

    monkeypatch.setattr(openrouter_image, "generate_image", fake_generate)
    resp = client.post(
        f"/runs/{run_id}/openrouter",
        json={"aspect_ratio": "2:3", "resolution": "1k"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 2
    assert data["failed"] == 0
    assert data["model"] == "meta/muse-image"

    generated = tmp_path / "generated" / run_id / "original"
    assert (generated / "000000.png").exists()
    assert (generated / "000001.png").exists()
    thumbs = tmp_path / "generated" / run_id / "thumbs"
    assert (thumbs / "000000.webp").exists()

    # 图片墙能看见（复用 ComfyUI 的资产视图）
    images = client.get(f"/runs/{run_id}/images").json()
    assert images["completed"] == 2
    assert images["items"][0]["status"] == "done"


def test_openrouter_push_records_failure_without_aborting_batch(client, monkeypatch):
    """单条失败不中断整批。"""
    run_id = _make_run(client)
    seen_prompts: list[str] = []

    def fake_generate(**kwargs):
        seen_prompts.append(kwargs["prompt"])
        if len(seen_prompts) == 1:
            raise openrouter_image.OpenRouterFetchError("上游炸了")
        return openrouter_image.GenerateImageResult(
            generation_id="g1",
            created=1,
            images=[_png_bytes()],
            media_types=["image/png"],
            usage={"cost": 0.02},
        )

    monkeypatch.setattr(openrouter_image, "generate_image", fake_generate)
    data = client.post(f"/runs/{run_id}/openrouter", json={"resolution": "1k"}).json()
    assert data["failed"] == 1
    assert data["succeeded"] == 1
    failed = [e for e in data["items"] if not e["ok"]]
    assert "上游炸了" in failed[0]["error"]


def test_failed_item_is_stored_with_error_status(client, monkeypatch, tmp_path):
    """失败条目落库必须是 status=error —— 曾经默认成 "done"，行里却带着 error。"""
    run_id = _make_run(client)
    calls: list[dict] = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise openrouter_image.OpenRouterFetchError("上游炸了")
        return openrouter_image.GenerateImageResult(
            generation_id="g1",
            created=1,
            images=[_png_bytes()],
            media_types=["image/png"],
            usage={"cost": 0.02},
        )

    monkeypatch.setattr(openrouter_image, "generate_image", fake_generate)
    client.post(f"/runs/{run_id}/openrouter", json={"resolution": "1k"})

    with sqlite3.connect(tmp_path / "test.db") as conn:
        rows = {
            int(row[0]): (str(row[1]), str(row[2]))
            for row in conn.execute(
                "SELECT item_index, status, error FROM openrouter_images WHERE run_id = ?",
                (run_id,),
            )
        }
    assert len(rows) == 2
    statuses = sorted(status for status, _ in rows.values())
    assert statuses == ["done", "error"]
    failed = [err for status, err in rows.values() if status == "error"]
    assert failed and "上游炸了" in failed[0]


def test_storage_derives_status_from_ok_when_missing(tmp_path):
    """entries 没带 status 时按 ok 推导，失败条目不能默认成 done。"""
    conn = storage.connect(tmp_path / "t.db")
    storage.save_openrouter_images(
        conn,
        "run1",
        [
            {"index": 0, "variant_id": "v0", "ok": True},
            {"index": 1, "variant_id": "v1", "ok": False, "error": "boom"},
        ],
    )
    rows = {int(r[0]): (str(r[1]), str(r[2])) for r in conn.execute(
        "SELECT item_index, status, error FROM openrouter_images WHERE run_id = 'run1'"
    )}
    assert rows[0] == ("done", "")
    assert rows[1] == ("error", "boom")


def test_openrouter_push_snapshots_provider_for_regenerate(client, monkeypatch):
    """入队快照记 provider=openrouter，单条「重新生成」按原 provider 分发。"""
    run_id = _make_run(client)
    calls: list[dict] = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return openrouter_image.GenerateImageResult(
            generation_id="g1",
            created=1,
            images=[_png_bytes()],
            media_types=["image/png"],
            usage={"cost": 0.024},
        )

    monkeypatch.setattr(openrouter_image, "generate_image", fake_generate)
    client.post(f"/runs/{run_id}/openrouter", json={"resolution": "1k"})

    resp = client.post(
        f"/runs/{run_id}/regenerate", json={"item_index": 0, "seed": 999}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "openrouter"
    # 重新生成走的是 OpenRouter（seed 换新值）
    assert calls[-1]["seed"] == 999


def test_openrouter_missing_reference_image_returns_400(client, monkeypatch):
    run_id = _make_run(client)
    resp = client.post(
        f"/runs/{run_id}/openrouter", json={"reference_kinds": ["character_sheet"]}
    )
    assert resp.status_code == 400
    assert "参考图不存在" in resp.json()["detail"]


def test_settings_expose_openrouter_masked(client, monkeypatch):
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-or-test")
    monkeypatch.delenv("OP_IMAGE_MODEL", raising=False)
    data = client.get("/settings").json()
    assert data["openrouter"]["api_key"] == ""
    assert data["openrouter_configured"] is True
    assert data["openrouter_default_t2i_model"] == "meta/muse-image"
    assert data["openrouter_default_i2i_model"] == "meta/muse-image"


def test_settings_save_openrouter_model(client):
    resp = client.put(
        "/settings", json={"openrouter": {"model": "qwen/qwen-image-2.1-turbo", "api_key": ""}}
    )
    assert resp.status_code == 200
    assert resp.json()["openrouter"]["model"] == "qwen/qwen-image-2.1-turbo"


def test_comfyui_queue_snapshot_records_comfyui_provider(client, monkeypatch):
    """ComfyUI 入队快照记 provider=comfyui：单条「重新生成」据此分发回 ComfyUI。"""
    from charvar.exporters import comfyui as comfyui_exporter

    def fake_submit(result, workflow, mapping, **kw):
        return [
            {
                "index": item.index,
                "variant_id": item.variant_id,
                "filename_prefix": f"p_{item.index}",
                "prompt_id": f"pid-{item.index}",
                "ok": True,
            }
            for item in result.items
        ]

    monkeypatch.setattr(comfyui_exporter, "submit", fake_submit)
    monkeypatch.setattr(
        comfyui_exporter, "load_workflow_pair", lambda name, *a, **k: ({"11": {}}, {"mappings": []})
    )
    run_id = _make_run(client)
    resp = client.post(
        f"/runs/{run_id}/comfyui",
        json={"aspect_ratio": "2:3", "resolution": "1k", "wait": False},
    )
    assert resp.status_code == 200
    # 重新生成必须回到 ComfyUI（不能误走 OpenRouter）
    called: list[str] = []
    monkeypatch.setattr(
        comfyui_exporter, "submit", lambda *a, **k: (called.append("comfyui"), fake_submit(*a, **k))[1]
    )
    monkeypatch.setattr(
        openrouter_image, "generate_image", lambda **kw: pytest.fail("不该走 OpenRouter")
    )
    out = client.post(f"/runs/{run_id}/regenerate", json={"item_index": 0, "seed": 5})
    assert out.status_code == 200
    assert out.json()["ok"] is True
    assert called == ["comfyui"]



# ---------- 模型能力（清单驱动） ----------


_MODEL_LIST = {
    "data": [
        {
            "id": "meta/muse-image",
            "name": "Meta: Muse Image",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": {},
        },
        {
            "id": "openai/gpt-image-2",
            "name": "OpenAI: GPT Image 2",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": {
                "aspect_ratio": {"type": "enum", "values": ["1:1", "2:3", "21:9"]},
                "quality": {"type": "enum", "values": ["auto", "low", "medium", "high"]},
                "input_references": {"type": "range", "min": 0, "max": 16},
            },
        },
        {
            "id": "tencent/hy-image-v3.5-preview",
            "name": "Tencent: Hunyuan Image",
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": {
                "aspect_ratio": {"type": "enum", "values": ["1:1", "2:3", "16:9"]},
                "resolution": {"type": "enum", "values": ["1K", "1.5K", "2K", "4K"]},
                "seed": {"type": "boolean"},
                "size": {"type": "boolean"},
            },
        },
        {
            "id": "recraft/recraft-v4.1-flash",
            "name": "Recraft: V4.1 Flash",
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": {"aspect_ratio": {"type": "enum", "values": ["1:1"]}},
        },
    ]
}


@pytest.fixture()
def fake_model_list(monkeypatch, tmp_path):
    """把模型清单固定住（不打真网），并隔离能力缓存。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        openrouter_image, "list_models", lambda **kw: dict(_MODEL_LIST)
    )
    return tmp_path


def test_capabilities_from_model_list(fake_model_list):
    """能力按 api-response.json 的 supported_parameters 推导。"""
    caps = openrouter_image.model_capabilities("openai/gpt-image-2")
    assert "quality" in caps.declared
    assert caps.qualities == ("auto", "low", "medium", "high")
    assert caps.aspect_ratios == ("1:1", "2:3", "21:9")
    assert caps.supports_refs is True


def test_capabilities_detect_text_only_model(fake_model_list):
    """清单里没有 image 输入的模型不能带参考图。"""
    caps = openrouter_image.model_capabilities("recraft/recraft-v4.1-flash")
    assert caps.supports_refs is False


def test_capabilities_for_model_with_empty_declaration(fake_model_list):
    """muse-image 清单声明为空：没有档位，也没有被误判成不支持参考图。"""
    caps = openrouter_image.model_capabilities("meta/muse-image")
    assert caps.declared == frozenset()
    assert caps.resolutions == ()
    assert caps.qualities == ()
    assert caps.supports_refs is True


def test_body_omits_resolution_when_model_does_not_declare_it(fake_model_list):
    """清单没声明 resolution → 不发（发了也不生效，还误导用户）。"""
    body = openrouter_image.build_generate_body(
        prompt="p", model="meta/muse-image", width=1024, height=1024, resolution="4k"
    )
    assert "resolution" not in body


def test_body_sends_resolution_when_model_declares_it(fake_model_list):
    caps = openrouter_image.model_capabilities("tencent/hy-image-v3.5-preview")
    body = openrouter_image.build_generate_body(
        prompt="p", model="tencent/hy-image-v3.5-preview", width=1024, height=1024,
        resolution="2k", capabilities=caps,
    )
    assert body["resolution"] == "2K"


def test_body_accepts_provider_format_resolution(fake_model_list):
    """provider 枚举原样（512/1.5K/4K…，大小写不敏感）也收：面板/env 的模式默认是这种写法。"""
    caps = openrouter_image.model_capabilities("tencent/hy-image-v3.5-preview")
    for raw, expected in (("1.5K", "1.5K"), ("1.5k", "1.5K"), ("512", None), ("8K", None)):
        body = openrouter_image.build_generate_body(
            prompt="p", model="tencent/hy-image-v3.5-preview", width=1024, height=1024,
            resolution=raw, capabilities=caps,
        )
        if expected is None:
            assert "resolution" not in body
        else:
            assert body["resolution"] == expected


def test_quality_enum_match_is_case_insensitive(fake_model_list):
    """quality 档大小写不敏感，命中后回传清单里的原始写法。"""
    caps = openrouter_image.model_capabilities("openai/gpt-image-2")
    body = openrouter_image.build_generate_body(
        prompt="p", model="openai/gpt-image-2", width=1024, height=1024,
        quality="HIGH", capabilities=caps,
    )
    assert body["quality"] == "high"


def test_provider_quality_auto_means_skip(fake_model_list):
    """"auto" 一律不发送（provider 自己的默认就是 auto，再发一遍没意义）。"""
    caps = openrouter_image.model_capabilities("openai/gpt-image-2")
    body = openrouter_image.build_generate_body(
        prompt="p", model="openai/gpt-image-2", width=1024, height=1024,
        quality="auto", capabilities=caps,
    )
    assert "quality" not in body


# ---------- 模式默认合并后的像素档位规划 ----------


def test_pixel_tier_uses_provider_value_when_in_enum(fake_model_list):
    """枚举命中 → 直接用归一后的 provider 值算像素（1K=1024 长边）。"""
    tier = openrouter_image.pixel_resolution_tier("tencent/hy-image-v3.5-preview", "4K")
    assert tier == "4K"


def test_pixel_tier_falls_back_for_invalid_value(fake_model_list):
    """无效枚举值不许撑大画幅：退回 fallback_tier。"""
    tier = openrouter_image.pixel_resolution_tier(
        "tencent/hy-image-v3.5-preview", "8K", fallback_tier="1k"
    )
    assert tier == "1k"


def test_pixel_tier_keeps_charvar_tier_when_not_declared(fake_model_list):
    """模型没声明 resolution（muse）→ 档位保留原样只算像素。"""
    assert openrouter_image.pixel_resolution_tier("meta/muse-image", "2k") == "2k"


def test_body_omits_quality_when_value_not_in_enum(fake_model_list):
    caps = openrouter_image.model_capabilities("openai/gpt-image-2")
    body = openrouter_image.build_generate_body(
        prompt="p", model="openai/gpt-image-2", width=1024, height=1024,
        quality="ultra", capabilities=caps,
    )
    assert "quality" not in body


def test_body_drops_references_for_text_only_model(fake_model_list, tmp_path):
    """只支持文生图的模型：不发 input_references。"""
    ref = tmp_path / "a.png"
    ref.write_bytes(_png_bytes())
    caps = openrouter_image.model_capabilities("recraft/recraft-v4.1-flash")
    body = openrouter_image.build_generate_body(
        prompt="p", model="recraft/recraft-v4.1-flash", width=1024, height=1024,
        reference_paths=[ref], capabilities=caps,
    )
    assert "input_references" not in body


# ---------- 自动降级 ----------


def test_rejected_parameter_parsing():
    assert openrouter_image._rejected_parameter("unknown parameter `quality`") == "quality"
    assert (
        openrouter_image._rejected_parameter(
            'No provider for x supports the requested parameter(s): quality "high".'
        )
        == "quality"
    )
    assert openrouter_image._rejected_parameter("Image size '1024x448' is not supported") == "size"
    assert openrouter_image._rejected_parameter("something else entirely") is None


def test_generate_degrades_when_provider_rejects_parameter(fake_model_list, monkeypatch):
    """provider 拒了 quality → 摘掉重试一次；被拒的参数记进能力缓存。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        if "quality" in body:
            return httpx.Response(
                400, json={"error": {"message": "unknown parameter `quality`"}}
            )
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(_png_bytes()).decode(), "media_type": "image/png"}],
                "usage": {"cost": 0.01},
            },
        )

    _fake_generate(monkeypatch, handler)
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    result = openrouter_image.generate_image(
        prompt="p", width=1024, height=1024, model="openai/gpt-image-2", quality="high"
    )
    assert len(seen) == 2
    assert "quality" in seen[0]
    assert "quality" not in seen[1]
    # 实际生效的请求体是降级后的那份
    assert "quality" not in result.request_body
    # 被拒的参数记进能力缓存
    cache = json.loads(openrouter_image.capabilities_cache_path().read_text())
    assert "quality" in cache["openai/gpt-image-2"]["rejected"]


def test_generate_falls_back_to_aspect_ratio_when_size_rejected(fake_model_list, monkeypatch):
    """size 被拒 → 退到 aspect_ratio（画幅从像素精确退化为比例枚举）。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        if "size" in body:
            return httpx.Response(
                400, json={"error": {"message": "Image size '1024x448' is not supported"}}
            )
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(_png_bytes()).decode(), "media_type": "image/png"}],
                "usage": {"cost": 0.01},
            },
        )

    _fake_generate(monkeypatch, handler)
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    result = openrouter_image.generate_image(
        prompt="p", width=1024, height=448, model="openai/gpt-image-2", aspect_ratio="21:9"
    )
    assert seen[-1].get("aspect_ratio") == "21:9"
    assert "size" not in seen[-1]
    assert result.request_body.get("aspect_ratio") == "21:9"


def test_generate_does_not_retry_on_auth_error(fake_model_list, monkeypatch):
    """鉴权失败不该重试（重试只会浪费额度）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    _fake_generate(monkeypatch, handler)
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    with pytest.raises(openrouter_image.OpenRouterAuthError):
        openrouter_image.generate_image(
            prompt="p", width=1024, height=1024, model="openai/gpt-image-2"
        )
    assert calls["n"] == 1


def test_concurrent_capability_reads_hit_network_once(monkeypatch, tmp_path):
    """冷缓存时并发读能力：只该拉一次清单（4 路并发不能打 4 次网）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_MODEL_LIST)

    _fake_generate(monkeypatch, handler)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: openrouter_image.model_capabilities("meta/muse-image"), range(4)))
    assert calls["n"] == 1


def test_concurrent_cache_writes_do_not_collide(fake_model_list, monkeypatch, tmp_path):
    """并发写缓存：临时文件名必须唯一，否则一个线程会删掉另一个的临时文件。"""
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "cache.json"

    def write(_):
        openrouter_image._write_cache_atomic(target, {"k": _})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(8)))
    assert json.loads(target.read_text())["k"] in range(8)


def test_concurrent_probe_records_are_not_lost(fake_model_list, monkeypatch):
    """并发探测记录：读-改-写要加锁，否则互相覆盖会丢条目。"""
    from concurrent.futures import ThreadPoolExecutor

    def probe(i):
        openrouter_image.record_probe("meta/muse-image", rejected=f"p{i}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(probe, range(8)))
    cache = json.loads(openrouter_image.capabilities_cache_path().read_text())
    assert sorted(cache["meta/muse-image"]["rejected"]) == [f"p{i}" for i in range(8)]
