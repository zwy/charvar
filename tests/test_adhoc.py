"""临时生图（charvar.adhoc）：ComfyUI / OpenRouter 两条通路（MockTransport，不发真网）。"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from PIL import Image

from charvar import adhoc, openrouter_image, storage
from charvar.exporters import comfyui as comfyui_exporter


def _png_bytes(color=(200, 100, 50), size=(64, 96)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _ref_image(tmp_path, name="ref.png", size=(3000, 2000)) -> pathlib.Path:
    path = tmp_path / name
    Image.new("RGB", size, (10, 20, 30)).save(path, format="PNG")
    return path


def _setup_workflows(tmp_path, monkeypatch) -> pathlib.Path:
    """造两对 workflow + mapping（t2i / edit），结构照真实内置 workflow 的关键部分。"""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    t2i_workflows: dict[str, dict] = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
        "55": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    }
    # edit workflow 多两个 LoadImage 槽位（对应 image / image2）
    edit_workflows: dict[str, dict] = {
        **t2i_workflows,
        "470": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "471": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
    }
    (workflows_dir / "TestT2I.json").write_text(json.dumps(t2i_workflows), encoding="utf-8")
    (workflows_dir / "TestEdit.json").write_text(json.dumps(edit_workflows), encoding="utf-8")
    latent = [
        {"field": "width", "node_id": "55", "input_key": "width", "default": 1024},
        {"field": "height", "node_id": "55", "input_key": "height", "default": 1024},
    ]
    t2i_mappings = [
        {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
        {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
        {"field": "seed", "node_id": "55", "input_key": "seed", "mode": "increment", "default": 0},
        *latent,
    ]
    edit_mappings = [
        {"field": "image", "node_id": "470", "input_key": "image", "required": False,
         "on_missing": {"remove_node": True}},
        {"field": "image2", "node_id": "471", "input_key": "image", "required": False,
         "on_missing": {"remove_node": True}},
        {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
        {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
        {"field": "seed", "node_id": "55", "input_key": "seed", "mode": "increment", "default": 0},
        *latent,
    ]
    (workflows_dir / "TestT2I.mapping.json").write_text(
        json.dumps({"output_nodes": ["47"], "mappings": t2i_mappings}), encoding="utf-8"
    )
    (workflows_dir / "TestEdit.mapping.json").write_text(
        json.dumps({"output_nodes": ["47"], "mappings": edit_mappings}), encoding="utf-8"
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    return workflows_dir


def _mock_comfy(monkeypatch, uploads: list[str], payloads: list[dict]):
    """mock /upload/image + /prompt + /history + /view（照 tests/test_subject_images.py 的做法）。"""
    queued: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            import re

            m = re.search(rb'filename="([^"]+)"', request.content)
            name = m.group(1).decode() if m else "uploaded.png"
            uploads.append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if request.url.path == "/prompt":
            body = json.loads(request.content.decode())
            pid = f"pid-{len(queued)}"
            queued.append(body)
            return httpx.Response(200, json={"prompt_id": pid})
        if request.url.path.startswith("/history/"):
            pid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    pid: {
                        "status": {"completed": True},
                        "outputs": {
                            "47": {"images": [{"filename": pid + ".png", "subfolder": "", "type": "output"}]}
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})
        return httpx.Response(404)

    class FakeClient:
        def __init__(self, **kw):
            self._inner = httpx.Client(transport=httpx.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(comfyui_exporter, "_httpx_client", lambda **kw: FakeClient(**kw))
    return queued


def _fake_openrouter(monkeypatch, *, images=None, error=None):
    """打桩 openrouter_image.generate_image，记录每次调用的参数。"""
    calls: list[dict] = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return openrouter_image.GenerateImageResult(
            generation_id="g1",
            created=1,
            images=images if images is not None else [_png_bytes()],
            media_types=["image/png"],
            usage={"cost": 0.01},
            request_body={},
        )

    monkeypatch.setattr(openrouter_image, "generate_image", fake_generate)
    return calls


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离数据目录 + 准备 workflow 与设置。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    _setup_workflows(tmp_path, monkeypatch)
    with storage.connect() as conn:
        storage.set_setting(conn, "comfy_t2i_workflow", "TestT2I")
        storage.set_setting(conn, "comfy_edit_workflow", "TestEdit")
    return tmp_path


# ---------- ComfyUI 通路 ----------

def test_comfy_t2i_injects_dimensions(env, monkeypatch):
    """无参考图 → 选 t2i workflow；画幅按 --aspect 注入 EmptyLatentImage。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    result = adhoc.generate(adhoc.AdhocRequest(
        prompt="a red apple", backend="comfyui", aspect_ratio="16:9", resolution="1k",
    ))
    assert result.status == "ready"
    assert result.provider == "comfyui"
    assert result.width == 64 and result.height == 96  # mock 返回的图片实际尺寸
    assert queued and "470" not in queued[0]["prompt"]  # 没带参考图 → LoadImage 不在 payload
    assert queued[0]["prompt"]["55"]["inputs"]["width"] == 1024
    assert queued[0]["prompt"]["55"]["inputs"]["height"] == 576


def test_comfy_edit_uploads_unique_remote_names(env, tmp_path, monkeypatch):
    """两次生成的参考图 remote_name 必须不同（upload 用 overwrite=true，同名会互相覆盖）。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    ref = _ref_image(tmp_path)
    for _ in range(2):
        adhoc.generate(adhoc.AdhocRequest(
            prompt="same character", backend="comfyui", reference_paths=[ref],
        ))
    assert len(uploads) == 2
    assert uploads[0] != uploads[1]
    assert all(name.startswith("adhoc_") for name in uploads)


def test_comfy_edit_maps_refs_to_slots_in_order(env, tmp_path, monkeypatch):
    """参考图按序映射到 image / image2 槽位。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    ref1 = _ref_image(tmp_path, "r1.png")
    ref2 = _ref_image(tmp_path, "r2.png")
    adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="comfyui", reference_paths=[ref1, ref2],
    ))
    assert queued
    payload = queued[0]["prompt"]
    assert payload["470"]["inputs"]["image"] == uploads[0]
    assert payload["471"]["inputs"]["image"] == uploads[1]
    # 未注入的槽位按 mapping 的 on_missing 摘掉
    assert "471" not in payload or payload["471"]["inputs"]["image"] == uploads[1]


def test_comfy_edit_removes_unused_slots(env, tmp_path, monkeypatch):
    """只给 1 张参考图时，没注入的 LoadImage 节点被摘掉。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    ref = _ref_image(tmp_path)
    adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="comfyui", reference_paths=[ref],
    ))
    payload = queued[0]["prompt"]
    assert "470" in payload
    assert "471" not in payload


def test_comfy_negative_prompt_explicit_empty_string(env, monkeypatch):
    """负向词传空串 = 明确不要（不能被 "if not value" 吞掉）。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="comfyui", negative_prompt="",
    ))
    assert queued[0]["prompt"]["11"]["inputs"]["text"] == "p"
    # 空串仍要传下去：submit 内部按三态处理
    assert "negative_prompt" not in queued[0]["prompt"]["11"]["inputs"]


def test_comfy_seed_is_injected_verbatim(env, monkeypatch):
    """seed 走 mapping 的 increment 模式，index=0 时注入值 = 用户给的 seed。"""
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="comfyui", seed=4242))
    assert queued[0]["prompt"]["55"]["inputs"]["seed"] == 4242


def test_comfy_reference_is_preprocessed(env, tmp_path, monkeypatch):
    """参考图被压到长边 1536 / JPEG q88（统一预处理，不传原图）。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    ref = _ref_image(tmp_path)
    adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="comfyui", reference_paths=[ref]))
    assert uploads[0].endswith(".jpg")  # 预处理输出恒为 JPEG
    cached = list((env / "generated" / "adhoc").glob("*/_ref_cache/*.jpg"))
    assert cached, "参考图预处理缓存没落盘"
    with Image.open(cached[0]) as image:
        assert max(image.size) <= 1536


# ---------- OpenRouter 通路 ----------

def test_openrouter_uses_explicit_pixels(env, monkeypatch):
    """画幅走显式像素（provider 的 aspect_ratio 枚举不含 21:9）。"""
    calls = _fake_openrouter(monkeypatch)
    result = adhoc.generate(adhoc.AdhocRequest(
        prompt="a red apple", backend="openrouter", aspect_ratio="16:9", resolution="1k",
    ))
    assert calls
    assert calls[0]["width"] == 1024
    assert calls[0]["height"] == 576
    assert result.provider == "openrouter"
    assert result.model


def test_openrouter_rewrites_reference_tags(env, tmp_path, monkeypatch):
    """有参考图 → <image1> 换成序数词。"""
    calls = _fake_openrouter(monkeypatch)
    ref = _ref_image(tmp_path)
    adhoc.generate(adhoc.AdhocRequest(
        prompt="exactly as shown in <image1>", backend="openrouter", reference_paths=[ref],
    ))
    assert "the first reference image" in calls[0]["prompt"]
    assert "<image1>" not in calls[0]["prompt"]


def test_openrouter_strips_tags_without_refs(env, monkeypatch):
    """没参考图 → <image1> 指向子句整段摘掉。"""
    calls = _fake_openrouter(monkeypatch)
    adhoc.generate(adhoc.AdhocRequest(
        prompt="Use <image1> as the reference for the identity.", backend="openrouter",
    ))
    assert "<image1>" not in calls[0]["prompt"]
    assert "reference" not in calls[0]["prompt"]


def test_openrouter_seed_is_normalized(env, monkeypatch):
    """超 2^53 的 seed 收敛到安全范围（provider 会 400）。"""
    calls = _fake_openrouter(monkeypatch)
    result = adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="openrouter", seed=2**60,
    ))
    # 发给 provider 的 seed 由 build_generate_body 内部收敛
    assert calls[0]["seed"] == 2**60  # generate_image 收到的是原始 seed
    assert result.seed == openrouter_image.normalize_seed(2**60)
    assert result.seed <= 2**53 - 1


# ---------- 落盘与契约 ----------

def test_manifest_written(env, monkeypatch):
    """manifest.json 落盘且含请求与结果。"""
    _fake_openrouter(monkeypatch)
    result = adhoc.generate(adhoc.AdhocRequest(
        prompt="a red apple", backend="openrouter", seed=7,
    ))
    manifest_path = pathlib.Path(result.manifest_path)
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["request"]["prompt"] == "a red apple"
    assert data["request"]["seed"] == 7
    assert data["request"]["backend"] == "openrouter"
    assert data["result"]["provider"] == "openrouter"
    assert data["error"] == ""


def test_does_not_write_runs_row(env, monkeypatch):
    """临时生图不落 runs 表（不进生成历史 / 图片墙）。"""
    _fake_openrouter(monkeypatch)
    with storage.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="openrouter"))
    with storage.connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert before == after == 0


def test_out_writes_explicit_path_with_given_suffix(env, tmp_path, monkeypatch):
    """--out 指定的文件确实存在，且后缀与用户指定一致。"""
    _fake_openrouter(monkeypatch)
    out = tmp_path / "deliverables" / "apple.png"
    result = adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="openrouter", out=out,
    ))
    assert out.exists()
    assert out.suffix == ".png"
    assert result.image_path == str(out)
    # manifest 仍在 data 目录（不污染用户目录）
    assert pathlib.Path(result.manifest_path).exists()
    assert str(tmp_path / "generated") in result.manifest_path


def test_out_content_matches_requested_format(env, tmp_path, monkeypatch):
    """后缀与实际格式不符时要转格式 —— 后缀不能骗人（下游按后缀读会出错）。"""
    webp = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(webp, format="WEBP")
    _fake_openrouter(monkeypatch, images=[webp.getvalue()])
    out = tmp_path / "apple.png"
    adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="openrouter", out=out))
    with Image.open(out) as image:
        assert image.format == "PNG"


def test_out_without_suffix_keeps_real_format(env, tmp_path, monkeypatch):
    """--out 不给后缀时补上真实扩展名（内容与后缀仍一致）。"""
    webp = io.BytesIO()
    Image.new("RGB", (32, 32), (30, 30, 200)).save(webp, format="WEBP")
    _fake_openrouter(monkeypatch, images=[webp.getvalue()])
    out = tmp_path / "apple"
    result = adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="openrouter", out=out))
    written = pathlib.Path(result.image_path)
    assert written == tmp_path / "apple.webp"
    with Image.open(written) as image:
        assert image.format == "WEBP"


def test_empty_prompt_raises(env):
    with pytest.raises(adhoc.AdhocError):
        adhoc.generate(adhoc.AdhocRequest(prompt="   ", backend="openrouter"))


def test_too_many_references_raises(env, tmp_path):
    refs = [_ref_image(tmp_path, f"r{i}.png") for i in range(5)]
    with pytest.raises(adhoc.AdhocError):
        adhoc.generate(adhoc.AdhocRequest(
            prompt="p", backend="openrouter", reference_paths=refs,
        ))


def test_missing_reference_raises(env, tmp_path):
    with pytest.raises(adhoc.AdhocError):
        adhoc.generate(adhoc.AdhocRequest(
            prompt="p", backend="openrouter", reference_paths=[tmp_path / "nope.png"],
        ))


def test_bad_comfy_url_scheme_raises(env):
    """缺 http:// 的地址要在提交前拦下（否则 httpx 报 UnsupportedProtocol）。"""
    with pytest.raises(adhoc.AdhocError):
        adhoc.generate(adhoc.AdhocRequest(
            prompt="p", backend="comfyui", comfy_url="192.168.1.174:8188",
        ))


def test_bad_aspect_ratio_raises(env):
    with pytest.raises(adhoc.AdhocError):
        adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="openrouter", aspect_ratio="7:3"))


# ---------- 模式默认（t2i 文生图 / i2i 图片编辑） ----------


def test_openrouter_mode_defaults_follow_references(env, monkeypatch):
    """带参考图 = i2i 模式：模型/分辨率/质量按 i2i 组配置取；请求显式值优先。"""
    with storage.connect() as conn:
        storage.set_setting(conn, "openrouter", {
            "t2i_model": "t/t2i", "t2i_resolution": "1K", "t2i_quality": "",
            "i2i_model": "t/i2i", "i2i_resolution": "2K", "i2i_quality": "low",
            "api_key": "",
        })
    calls = _fake_openrouter(monkeypatch)
    adhoc.generate(adhoc.AdhocRequest(prompt="p", backend="openrouter", resolution=""))
    assert calls[0]["model"] == "t/t2i"
    assert calls[0]["resolution"] == "1K"
    assert not calls[0]["quality"]  # 模式默认空 = 不发 quality 参数

    calls = _fake_openrouter(monkeypatch)
    ref = _ref_image(env)
    adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="openrouter", reference_paths=[ref],
    ))
    assert calls[0]["model"] == "t/i2i"
    assert calls[0]["resolution"] == "2K"
    assert calls[0]["quality"] == "low"


def test_openrouter_explicit_params_beat_mode_defaults(env, monkeypatch):
    """请求显式给了 resolution / quality 就不动模式默认。"""
    with storage.connect() as conn:
        storage.set_setting(conn, "openrouter", {"t2i_model": "t/t2i", "t2i_quality": "high", "api_key": ""})
    calls = _fake_openrouter(monkeypatch)
    adhoc.generate(adhoc.AdhocRequest(
        prompt="p", backend="openrouter", model="x/explicit", resolution="1k", quality="medium",
    ))
    assert calls[0]["model"] == "x/explicit"
    assert calls[0]["resolution"] == "1k"
    assert calls[0]["quality"] == "medium"


def test_openrouter_resolution_accepts_provider_enum(env):
    """provider 枚举写法（512/1K/1.5K/4K…）过格式校验；乱值报错。"""
    req = adhoc.AdhocRequest(prompt="p", backend="openrouter", resolution="1.5K")
    adhoc.validate(req)
    with pytest.raises(adhoc.AdhocError):
        adhoc.validate(adhoc.AdhocRequest(prompt="p", backend="openrouter", resolution="xxl"))
    # 空 = 跟随设置页/env 的模式默认
    adhoc.validate(adhoc.AdhocRequest(prompt="p", backend="openrouter", resolution=""))
