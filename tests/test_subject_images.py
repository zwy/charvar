"""主体档案图片：槽位/模板/上传/生成（MockTransport 全链路，不发真网）。"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar.api import build_app
from charvar import llm, paths, storage


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _png_bytes(color=(200, 100, 50), size=(64, 96)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _setup_workflows(tmp_path, monkeypatch, with_latent: bool = False) -> pathlib.Path:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    workflows: dict[str, dict] = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "470": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    if with_latent:
        workflows["55"] = {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}}
    (workflows_dir / "TestT2I.json").write_text(json.dumps(workflows), encoding="utf-8")
    t2i_mappings = [
        {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
        {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
    ]
    edit_mappings = [
        {"field": "image", "node_id": "470", "input_key": "image", "required": False},
        {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
        {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
    ]
    if with_latent:
        # 注入 target：EmptyLatentImage 的 width/height（真实 edit workflow 的出图尺寸入口）
        latent_mappings = [
            {"field": "width", "node_id": "55", "input_key": "width", "default": 1024},
            {"field": "height", "node_id": "55", "input_key": "height", "default": 1024},
        ]
        t2i_mappings += latent_mappings
        edit_mappings += latent_mappings
    (workflows_dir / "TestT2I.mapping.json").write_text(json.dumps({"output_nodes": ["47"], "mappings": t2i_mappings}), encoding="utf-8")
    (workflows_dir / "TestEdit.json").write_text(json.dumps(workflows), encoding="utf-8")
    (workflows_dir / "TestEdit.mapping.json").write_text(json.dumps({"output_nodes": ["47"], "mappings": edit_mappings}), encoding="utf-8")
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    client_settings = {"comfy_t2i_workflow": "TestT2I", "comfy_edit_workflow": "TestEdit"}
    return client_settings


def _mock_comfy(monkeypatch, uploads: list[str], payloads: list[dict], state: dict | None = None):
    """mock /upload/image + /prompt + /history + /view；state["complete"]=False 时 history 不完成。"""
    queued: list = []
    state = state if state is not None else {"complete": True}

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
            if not state.get("complete", True):
                return httpx.Response(200, json={pid: {"status": {}}})
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

    from charvar.exporters import comfyui as comfyui_exporter

    monkeypatch.setattr(comfyui_exporter, "_httpx_client", lambda **kw: FakeClient(**kw))
    return queued


def test_slots_and_template_roundtrip(client):
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    data = client.get("/subjects/demo_witch/images").json()
    assert data["kinds"] == ["character_sheet", "headshot", "front", "side", "behind"]
    assert len(data["slots"]) == 5
    assert all(slot["status"] == "empty" and not slot["is_ready"] for slot in data["slots"])
    assert data["slots"][0]["label"] == "人物半身像＋三视图"

    assert client.get("/subjects/demo_witch/template").status_code == 404
    put = client.put("/subjects/demo_witch/template", json={"template_md": "# 模板\n正文"})
    assert put.status_code == 200
    assert client.get("/subjects/demo_witch/template").text == "# 模板\n正文"
    assert client.get("/subjects/missing/images").status_code == 404
    # 未知 kind
    assert client.get("/subjects/demo_witch/images/other/file").status_code == 400


def test_upload_and_delete(client, tmp_path):
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    resp = client.post(
        "/subjects/demo_witch/images/front/upload",
        files={"file": ("front.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    slot = resp.json()
    assert slot["status"] == "ready" and slot["source"] == "uploaded" and slot["is_ready"]
    original = paths.subjects_dir() / "demo_witch" / "front.png"
    assert original.exists()
    assert (paths.subjects_dir() / "demo_witch" / "thumbs" / "front.webp").exists()
    assert client.get("/subjects/demo_witch/images/front/file").status_code == 200
    assert client.get("/subjects/demo_witch/images/front/thumb").status_code == 200

    # 非图片内容被拒
    bad = client.post(
        "/subjects/demo_witch/images/headshot/upload",
        files={"file": ("a.png", b"not-an-image", "image/png")},
    )
    assert bad.status_code == 400

    delete = client.delete("/subjects/demo_witch/images/front").json()
    assert delete["removed"] is True
    assert not original.exists()
    assert client.get("/subjects/demo_witch/images/front/file").status_code == 404


def test_generate_requires_ready_sheet(client, tmp_path, monkeypatch):
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    _mock_comfy(monkeypatch, [], [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    resp = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "edit headshot"},
    )
    assert resp.status_code == 409
    assert "character_sheet" in resp.json()["detail"]


def test_generate_edit_uses_uploaded_sheet(client, tmp_path, monkeypatch):
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    up = client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    assert up.json()["is_ready"]

    resp = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "chest-up portrait of the same character", "seed": 3},
    )
    assert resp.status_code == 200, resp.text
    slot = resp.json()
    assert slot["status"] == "ready" and slot["source"] == "generated"
    assert uploads == ["demo_witch_character_sheet.png"]
    injected = queued[0]["prompt"]
    assert injected["470"]["inputs"]["image"] == "demo_witch_character_sheet.png"
    assert injected["11"]["inputs"]["text"] == "chest-up portrait of the same character"
    assert injected["47"]["inputs"]["filename_prefix"] == "demo_witch_000_headshot"
    assert (paths.subjects_dir() / "demo_witch" / "headshot.png").exists()
    assert (paths.subjects_dir() / "demo_witch" / "thumbs" / "headshot.webp").exists()
    slots = {s["kind"]: s for s in client.get("/subjects/demo_witch/images").json()["slots"]}
    assert slots["headshot"]["is_ready"] and slots["character_sheet"]["is_ready"]

    # 重新生成：同 remote_name 覆盖上传
    resp2 = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "v2", "seed": 4},
    )
    assert resp2.status_code == 200
    assert uploads == ["demo_witch_character_sheet.png", "demo_witch_character_sheet.png"]


def test_generate_sheet_uses_t2i_and_settings_size(client, tmp_path, monkeypatch):
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    queued = _mock_comfy(monkeypatch, [], [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    resp = client.post(
        "/subjects/demo_witch/images/character_sheet/generate",
        json={"prompt": "character sheet, three views", "seed": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    # t2i：image 节点保持 workflow 内置默认（未注入参考图）
    payload = queued[0]["prompt"]
    assert payload["470"]["inputs"]["image"] == "ref.png"
    assert payload["47"]["inputs"]["filename_prefix"] == "demo_witch_000_character_sheet"


def test_generate_timeout_then_sync(client, tmp_path, monkeypatch):
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    monkeypatch.setenv("COMFYUI_TIMEOUT", "0.5")
    from charvar.exporters import comfyui as comfyui_exporter

    monkeypatch.setattr(comfyui_exporter.time, "sleep", lambda s: None)
    uploads: list[str] = []
    state = {"complete": False}
    _mock_comfy(monkeypatch, uploads, [], state)
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    resp = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "p", "seed": 9},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["prompt_id"] == "pid-0"

    state["complete"] = True
    synced = client.post("/subjects/demo_witch/images/headshot/sync").json()
    assert synced["status"] == "ready"
    assert (paths.subjects_dir() / "demo_witch" / "headshot.png").exists()


def test_generate_batch(client, tmp_path, monkeypatch):
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    storage.upsert_subject_image(conn, {"subject_id": "demo_witch", "kind": "headshot", "prompt": "p-head"})
    storage.upsert_subject_image(conn, {"subject_id": "demo_witch", "kind": "front", "prompt": "p-front"})
    # 缺 prompt 的 kind 触发 400
    resp = client.post("/subjects/demo_witch/images/generate-batch", json={"kinds": ["headshot", "side"]})
    assert resp.status_code == 400
    resp = client.post("/subjects/demo_witch/images/generate-batch", json={"kinds": ["headshot", "front"]})
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert {s["kind"]: s["status"] for s in slots} == {"headshot": "ready", "front": "ready"}
    assert uploads == ["demo_witch_character_sheet.png"]  # 批量只上传一次参考图
    # character_sheet 不属于批量（edit）类型
    bad = client.post("/subjects/demo_witch/images/generate-batch", json={"kinds": ["character_sheet"]})
    assert bad.status_code == 400


def test_write_replaces_other_suffix_files(client, tmp_path, monkeypatch):
    """换后缀写入时旧文件必须清理：_original_file 按后缀序取图，残留会展示旧图。"""
    settings = _setup_workflows(tmp_path, monkeypatch)
    client.put("/settings", json=settings)
    _mock_comfy(monkeypatch, [], [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})

    def _jpeg_bytes() -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (64, 96), (10, 20, 30)).save(buf, format="JPEG")
        return buf.getvalue()

    # 上传路径：png → jpg
    client.post("/subjects/demo_witch/images/front/upload", files={"file": ("front.png", _png_bytes(), "image/png")})
    resp = client.post("/subjects/demo_witch/images/front/upload", files={"file": ("front.jpg", _jpeg_bytes(), "image/jpeg")})
    assert resp.status_code == 200
    assert (paths.subjects_dir() / "demo_witch" / "front.jpg").exists()
    assert not (paths.subjects_dir() / "demo_witch" / "front.png").exists()
    assert client.get("/subjects/demo_witch/images/front/file").status_code == 200

    # 生成路径：先上传 jpg 占位，生成（png 输出）后旧 jpg 被清理
    client.post("/subjects/demo_witch/images/character_sheet/upload", files={"file": ("sheet.png", _png_bytes(), "image/png")})
    client.post("/subjects/demo_witch/images/headshot/upload", files={"file": ("headshot.jpg", _jpeg_bytes(), "image/jpeg")})
    resp = client.post("/subjects/demo_witch/images/headshot/generate", json={"prompt": "p", "seed": 1})
    assert resp.status_code == 200, resp.text
    assert (paths.subjects_dir() / "demo_witch" / "headshot.png").exists()
    assert not (paths.subjects_dir() / "demo_witch" / "headshot.jpg").exists()
    assert client.get("/subjects/demo_witch/images/headshot/file").status_code == 200


def test_subject_delete_cascades(client, tmp_path):
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    client.post(
        "/subjects/demo_witch/images/front/upload",
        files={"file": ("front.png", _png_bytes(), "image/png")},
    )
    client.put("/subjects/demo_witch/template", json={"template_md": "# t"})
    assert client.delete("/subjects/demo_witch").status_code == 200
    assert not (paths.subjects_dir() / "demo_witch").exists()
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    assert storage.list_subject_images(conn, "demo_witch") == []
    assert storage.get_subject_template(conn, "demo_witch") is None

def test_suggest_prompt_persists_to_db(client, tmp_path, monkeypatch):
    """AI 起草结果必须落库（否则生成任一图后其他草稿会被前端态冲掉）。"""
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    monkeypatch.setattr(llm, "chat_json", lambda messages, **kwargs: {"prompt": "Drafted prompt only."})
    resp = client.post("/subjects/demo_witch/images/front/suggest-prompt")
    assert resp.status_code == 200
    assert resp.json()["prompt"] == "Drafted prompt only."
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    row = storage.get_subject_image(conn, "demo_witch", "front")
    assert row["prompt"] == "Drafted prompt only."
    assert row["status"] == "empty"  # 只写 prompt，状态机不动


def test_prompt_put_upsert_and_preserve(client, tmp_path):
    """手工编辑提示词落库：无行时建 empty 行；ready 行只改 prompt 不动文件/状态。"""
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    put1 = client.put("/subjects/demo_witch/images/front/prompt", json={"prompt": "  hand written  "})
    assert put1.status_code == 200
    assert put1.json()["prompt"] == "hand written"
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    row = storage.get_subject_image(conn, "demo_witch", "front")
    assert row["prompt"] == "hand written" and row["status"] == "empty"

    client.post(
        "/subjects/demo_witch/images/front/upload",
        files={"file": ("front.png", _png_bytes(), "image/png")},
    )
    put2 = client.put("/subjects/demo_witch/images/front/prompt", json={"prompt": "edited after upload"})
    row = storage.get_subject_image(conn, "demo_witch", "front")
    assert row["prompt"] == "edited after upload"
    assert row["status"] == "ready" and row["file_path"]  # 文件与 ready 状态原样保留

    assert client.put("/subjects/demo_witch/images/other/prompt", json={"prompt": "x"}).status_code == 400
    assert client.put("/subjects/missing/images/front/prompt", json={"prompt": "x"}).status_code == 404


def test_generate_edit_injects_slot_size(client, tmp_path, monkeypatch):
    """回归：edit 槽位生成必须注入「主体图默认尺寸」里的画幅——
    edit workflow 的出图 latent 是 EmptyLatentImage（不随参考图尺寸），
    不注入就落到 workflow 内置 1024×1024（1:1）。请求显式画幅优先于设置。"""
    settings = _setup_workflows(tmp_path, monkeypatch, with_latent=True)
    client.put("/settings", json=settings)
    queued = _mock_comfy(monkeypatch, [], [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )

    resp = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "chest-up portrait of the same character", "seed": 3},
    )
    assert resp.status_code == 200, resp.text
    payload = queued[0]["prompt"]
    # headshot 默认 3:4/1k → 768×1024（长边 1024，短边取 64 倍数）
    assert payload["55"]["inputs"]["width"] == 768
    assert payload["55"]["inputs"]["height"] == 1024

    # 请求显式画幅优先于设置
    resp = client.post(
        "/subjects/demo_witch/images/headshot/generate",
        json={"prompt": "p", "seed": 4, "aspect_ratio": "1:1"},
    )
    assert resp.status_code == 200
    payload = queued[1]["prompt"]
    assert payload["55"]["inputs"]["width"] == 1024
    assert payload["55"]["inputs"]["height"] == 1024


def test_generate_batch_injects_per_kind_size(client, tmp_path, monkeypatch):
    """批量生成按各槽位自己的画幅注入 width/height（3:4 headshot → 768×1024，
    2:3 front → 704×1024），参考图只上传一次，种子跨槽位递增。"""
    settings = _setup_workflows(tmp_path, monkeypatch, with_latent=True)
    client.put("/settings", json=settings)
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads, [])
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    storage.upsert_subject_image(conn, {"subject_id": "demo_witch", "kind": "headshot", "prompt": "p-head"})
    storage.upsert_subject_image(conn, {"subject_id": "demo_witch", "kind": "front", "prompt": "p-front"})

    resp = client.post("/subjects/demo_witch/images/generate-batch", json={"kinds": ["headshot", "front"], "seed": 5})
    assert resp.status_code == 200, resp.text
    assert uploads == ["demo_witch_character_sheet.png"]
    head, front = queued[0]["prompt"], queued[1]["prompt"]
    assert head["55"]["inputs"]["width"] == 768 and head["55"]["inputs"]["height"] == 1024
    assert front["55"]["inputs"]["width"] == 704 and front["55"]["inputs"]["height"] == 1024
    # 逐槽位提交 + index 递增 → 前缀序号与旧的单次提交一致
    assert head["47"]["inputs"]["filename_prefix"] == "demo_witch_000_headshot"
    assert front["47"]["inputs"]["filename_prefix"] == "demo_witch_001_front"
