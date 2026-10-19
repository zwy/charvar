"""FastAPI 冒烟：预设/主体 CRUD/生成/历史/导出。"""
from __future__ import annotations

import json
import pathlib

import httpx

import pytest
from fastapi.testclient import TestClient

from charvar.api import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_presets_and_detail(client):
    data = client.get("/presets").json()
    names = {p["name"] for p in data}
    assert {"q21_default_30", "q21_legacy_30", "matrix_basic"} <= names
    detail = client.get("/presets/q21_default_30").json()
    assert detail["count"] == 30
    assert len(detail["variants"]) == 30


def test_subject_crud_and_generate_flow(client):
    profile = {
        "id": "demo_witch",
        "name": "演示",
        "subject_noun": "woman",
        "trigger_word": "sw1tch",
        "style_lock": ["the color palette"],
        "identity_lock": ["long silver-white hair"],
    }
    assert client.post("/subjects", json=profile).status_code == 201
    assert client.post("/subjects", json=profile).status_code == 409
    got = client.get("/subjects/demo_witch").json()
    assert got["subject_noun"] == "woman"
    assert client.get("/subjects/missing").status_code == 404

    req = {"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    gen = client.post("/generate", json=req)
    assert gen.status_code == 200
    result = gen.json()
    assert result["run_id"]
    assert len(result["items"]) == 30

    run_id = result["run_id"]
    assert client.get(f"/runs/{run_id}").json()["seed"] == 42
    assert len(client.get("/runs").json()) == 1
    exported = client.get(f"/runs/{run_id}/export", params={"format": "txt"}).text
    assert exported.splitlines()[0] == result["items"][0]["prompt"]
    zip_bytes = client.get(f"/runs/{run_id}/export", params={"format": "captions_zip"}).content
    assert zip_bytes[:2] == b"PK"
    assert client.delete(f"/runs/{run_id}").status_code == 200
    assert client.get(f"/runs/{run_id}").status_code == 404

    put = client.put("/subjects/demo_witch", json={"subject_noun": "girl", "name": "演示 2"})
    assert put.status_code == 200
    assert client.get("/subjects/demo_witch").json()["subject_noun"] == "girl"
    assert client.delete("/subjects/demo_witch").status_code == 200
    assert client.get("/subjects").json() == []


def test_generate_with_inline_subject_and_fixed_parts(client):
    body = {
        "subject": {"id": "temp", "name": "临时", "subject_noun": "person"},
        "preset": "matrix_basic",
        "seed": 7,
        "randomize_background": False,
        "randomize_lighting": False,
        "fixed_background": "red",
    }
    result = client.post("/generate", json=body).json()
    assert len(result["items"]) == 30
    assert result["items"][0]["background"] == "red"
    assert result["items"][0]["lighting"] is None
    assert "against a red background" in result["items"][0]["prompt"]
    assert "The lighting is" not in result["items"][0]["prompt"]


def _mock_comfy(monkeypatch, workflows_dir: pathlib.Path):
    """把 exporter._httpx_client 替换为 mock server（/upload/image + /prompt + /history）。"""
    from charvar.exporters import comfyui as comfyui_exporter

    queued: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            import re

            m = re.search(rb'filename="([^"]+)"', request.content)
            name = m.group(1).decode() if m else "uploaded.png"
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
                            "47": {
                                "images": [
                                    {"filename": pid + ".png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
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


def test_comfy_queue_with_mocked_server(tmp_path, monkeypatch, client):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "TestWF.json").write_text(
        json.dumps(
            {
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
                "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
            }
        ),
        encoding="utf-8",
    )
    (workflows_dir / "TestWF.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                    {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    queued = _mock_comfy(monkeypatch, workflows_dir)

    listing = client.get("/comfy/workflows").json()
    assert listing["workflows_dir"] == str(workflows_dir)
    assert listing["workflows"] == [{"name": "TestWF", "has_workflow": True, "has_mapping": True}]

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    run_id = client.post(
        "/generate", json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    ).json()["run_id"]

    queue_resp = client.post(
        f"/runs/{run_id}/comfyui", json={"workload": "TestWF", "wait": False}
    ).json()
    assert queue_resp["succeeded"] == 30
    assert queue_resp["failed"] == 0
    assert [s["prompt_id"] for s in queue_resp["submissions"][:2]] == ["pid-0", "pid-1"]
    injected = queued[0]["prompt"]
    assert injected["11"]["inputs"]["text"].startswith("Create an image of the person")
    assert injected["47"]["inputs"]["filename_prefix"].endswith("_000_full_front")

    records = client.get(f"/runs/{run_id}/comfyui").json()
    assert len(records) == 30
    assert records[0]["prompt_id"] == "pid-0"


def test_comfy_wait_records_output_files(tmp_path, monkeypatch, client):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "WaitWF.json").write_text(
        json.dumps(
            {
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
                "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
            }
        ),
        encoding="utf-8",
    )
    (workflows_dir / "WaitWF.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                    {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    _mock_comfy(monkeypatch, workflows_dir)

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    run_id = client.post(
        "/generate",
        json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42, "count": 2},
    ).json()["run_id"]

    resp = client.post(
        f"/runs/{run_id}/comfyui", json={"workload": "WaitWF", "wait": True}
    ).json()
    assert resp["succeeded"] == 2
    files = resp["submissions"][0]["files"]
    assert files == [{"filename": "pid-0.png", "subfolder": "", "type": "output"}]
    record = client.get(f"/runs/{run_id}/comfyui").json()[0]
    assert record["output_files"] == [{"filename": "pid-0.png", "subfolder": "", "type": "output"}]


def test_comfy_auto_workload(tmp_path, monkeypatch, client):
    """不传 workload 时按参考图自动选：有参考图=edit，没参考图=t2i。"""
    import io

    from PIL import Image

    def _pair(name: str, with_image: bool = False) -> None:
        nodes = {
            "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
            "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
        }
        mappings = [
            {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
            {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
        ]
        if with_image:
            nodes["470"] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}}
            mappings.insert(0, {"field": "image", "node_id": "470", "input_key": "image", "required": False})
        (workflows_dir / f"{name}.json").write_text(json.dumps(nodes), encoding="utf-8")
        (workflows_dir / f"{name}.mapping.json").write_text(
            json.dumps({"output_nodes": ["47"], "mappings": mappings}),
            encoding="utf-8",
        )

    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    _pair("T2I")
    _pair("Edit", with_image=True)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    _mock_comfy(monkeypatch, workflows_dir)

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (10, 20, 30)).save(buf, format="PNG")
    client.post(
        "/subjects/demo_witch/images/front/upload",
        files={"file": ("front.png", buf.getvalue(), "image/png")},
    )
    assert client.put(
        "/settings", json={"comfy_t2i_workflow": "T2I", "comfy_edit_workflow": "Edit"}
    ).status_code == 200
    run_id = client.post(
        "/generate",
        json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42, "count": 2},
    ).json()["run_id"]

    no_refs = client.post(f"/runs/{run_id}/comfyui", json={"wait": False}).json()
    assert no_refs["workload"] == "T2I"

    with_refs = client.post(
        f"/runs/{run_id}/comfyui", json={"wait": False, "reference_kinds": ["front"]}
    ).json()
    assert with_refs["workload"] == "Edit"
    # 注：设置页清空 workflow 名时 get_settings 回退内置默认名（成对文件缺失 → 404 找不到），
    # 所以「未指定 workflow」分支只出现在显式 workload 为空且默认名也解析失败的情形，不在此展开。


def test_video_preset_endpoint_and_render(tmp_path, monkeypatch, client):
    """GET /video-presets 列出预设；POST /runs/{id}/video 用真 ffmpeg 渲染小样。"""
    import io
    import shutil as _shutil

    from PIL import Image

    if _shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg")

    # 视频/提示词预设都指到 tmp：拷贝真预设保证 /generate 可用，另加一个小预设
    presets_root = tmp_path / "presets"
    presets_root.mkdir()
    src_presets = pathlib.Path(__file__).resolve().parent.parent / "presets"
    for src in src_presets.glob("*.yaml"):
        _shutil.copy(src, presets_root / src.name)
    _shutil.copytree(src_presets / "wildcards", presets_root / "wildcards")
    (presets_root / "video").mkdir()
    (presets_root / "video" / "tiny.yaml").write_text(
        "\n".join(
            [
                "name: tiny",
                'type: video',
                "aspect: '9:16'",
                "resolution: 720p",
                "fps: 8",
                "seconds_per_image: 0.25",
                "include_contact_sheet: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(presets_root))

    names = {p["name"]: p for p in client.get("/video-presets").json()}
    assert "tiny" in names
    assert names["tiny"]["size"] == [720, 1280]

    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    run_id = client.post(
        "/generate", json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    ).json()["run_id"]

    # 手工放 3 张小图（模拟 ComfyUI 已同步）
    gen_dir = tmp_path / "generated" / run_id
    for index in range(3):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (30, 60, 90)).save(buf, format="PNG")
        (gen_dir / "original").mkdir(parents=True, exist_ok=True)
        (gen_dir / "original" / f"{index:06d}.png").write_bytes(buf.getvalue())

    resp = client.post("/runs/demo_witch-x/video", json={"preset": "tiny"})
    assert resp.status_code == 404  # run 不存在

    resp = client.post(f"/runs/{run_id}/video", json={"preset": "nope"}).json()
    assert resp.status_code == 400 if hasattr(resp, "status_code") else client.post(
        f"/runs/{run_id}/video", json={"preset": "nope"}
    ).status_code == 400

    done = client.post(f"/runs/{run_id}/video", json={"preset": "tiny"}).json()
    assert done["size"] == [720, 1280]
    assert done["images_used"] == 3
    assert 0 < done["duration"] < 5
    listing = client.get(f"/runs/{run_id}/images").json()
    assert [v["name"] for v in listing["videos"]] == ["tiny.mp4"]
    video_resp = client.get(f"/runs/{run_id}/assets/videos/tiny.mp4")
    assert video_resp.status_code == 200
    assert b"ftyp" in video_resp.content[:64]
    again = client.post(f"/runs/{run_id}/video", json={"preset": "tiny"})
    assert again.status_code == 200  # 同名重跑覆盖


# ---------- 带字表情包（schema 2 sticker）API 链路 ----------

def _sticker_workflows_dir(base: pathlib.Path) -> None:
    nodes = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": "old negative"}},
        "55": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
        "470": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    base.mkdir(parents=True, exist_ok=True)
    (base / "StickerWF.json").write_text(json.dumps(nodes), encoding="utf-8")
    (base / "StickerWF.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                    {"field": "negative_prompt", "node_id": "12", "input_key": "text"},
                    {"field": "width", "node_id": "55", "input_key": "width", "default": 1024},
                    {"field": "height", "node_id": "55", "input_key": "height", "default": 1024},
                    {"field": "image", "node_id": "470", "input_key": "image", "required": False},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_sticker_api_end_to_end(tmp_path, monkeypatch, client):
    """classic sticker：meta 快照 → 详情/列表根字段 → 入队 auto=1:1、空负向清内置默认；
    负向冲突可读报错；显式自定义负向照常注入；手动比例生效；TXT/JSONL 导出保留中文与快照。"""
    workflows = tmp_path / "sticker_workflows"
    _sticker_workflows_dir(workflows)
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows))
    queued = _mock_comfy(monkeypatch, workflows)
    client.post("/subjects", json={"id": "demo_cat", "name": "Demo Cat"})
    resp = client.post(
        "/generate", json={"subject_id": "demo_cat", "preset": "chat_replies_v1_12", "seed": 42}
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert len(result["items"]) == 12
    item = result["items"][0]
    assert item["meta"]["display_text"]["content"] == "收到！"
    assert item["meta"]["text_policy"] == "required_label"
    assert result["warnings"] == []

    detail = client.get("/presets/chat_replies_v1_12").json()
    assert detail["schema_version"] == 2 and detail["purpose"] == "sticker"
    assert detail["preferred_aspect_ratio"] == "1:1"
    assert detail["lettering"]["language"] == "zh-Hans"
    assert detail["variants"][0]["display_text"]["content"] == "收到！"
    listing = client.get("/presets").json()
    entry = next(p for p in listing if p["name"] == "chat_replies_v1_12")
    assert entry["purpose"] == "sticker" and entry["text_policy"] == "required_label"

    run_id = result["run_id"]
    payload = client.post(f"/runs/{run_id}/comfyui", json={"workload": "StickerWF"}).json()
    assert payload["succeeded"] == 12
    first = queued[0]["prompt"]
    assert first["55"]["inputs"]["width"] == 1024 and first["55"]["inputs"]["height"] == 1024
    assert first["12"]["inputs"]["text"] == ""  # 空负向注入清掉 workflow 内置默认 "old negative"
    assert "收到！" in first["11"]["inputs"]["text"]

    # 手动比例 3:4 → 768×1024（显式优先于快照）
    run2 = client.post(
        "/generate", json={"subject_id": "demo_cat", "preset": "chat_replies_v1_12", "seed": 43}
    ).json()["run_id"]
    client.post(f"/runs/{run2}/comfyui", json={"workload": "StickerWF", "aspect_ratio": "3:4"})
    assert queued[12]["prompt"]["55"]["inputs"]["width"] == 768

    # 负向冲突 → 400 可读报错；显式自定义（无禁字 token）→ 注入原值
    run3 = client.post(
        "/generate", json={"subject_id": "demo_cat", "preset": "chat_replies_v1_12", "seed": 44}
    ).json()["run_id"]
    bad = client.post(f"/runs/{run3}/comfyui", json={"workload": "StickerWF", "negative_prompt": "text, blurry"})
    assert bad.status_code == 400
    assert "text" in bad.json()["detail"]
    ok = client.post(f"/runs/{run3}/comfyui", json={"workload": "StickerWF", "negative_prompt": "blurry"})
    assert ok.status_code == 200
    assert queued[25]["prompt"]["12"]["inputs"]["text"] == "blurry"

    # TXT 逐行含中文；JSONL 含 display_text 快照
    export = client.get(f"/runs/{run_id}/export?format=txt").text
    assert "收到！" in export
    export_item = json.loads(client.get(f"/runs/{run_id}/export?format=jsonl").text.splitlines()[0])
    assert export_item["meta"]["display_text"]["placement"] == "beside the raised hand or forepaw, above shoulder height"


def test_sticker_generate_subject_conflict_blocks(tmp_path, monkeypatch, client):
    """主体档案含明确禁字条款 → 400；模板疑似条款不阻断但随结果返回（warnings）。"""
    client.post("/subjects", json={"id": "ban_cat", "name": "Ban"})
    client.put("/subjects/ban_cat", json={"id": "ban_cat", "name": "Ban", "identity_lock": ["no text, clean look"]})
    resp = client.post("/generate", json={"subject_id": "ban_cat", "preset": "chat_replies_v1_12", "seed": 1})
    assert resp.status_code == 400
    assert "no text" in resp.json()["detail"]

    client.post("/subjects", json={"id": "warn_cat", "name": "Warn"})
    client.put("/subjects/warn_cat/template", json={"template_md": "## 场景：无文字条款（仅局部）"})
    resp = client.post("/generate", json={"subject_id": "warn_cat", "preset": "chat_replies_v1_12", "seed": 1})
    assert resp.status_code == 200
    assert resp.json()["warnings"] and "无文字" in resp.json()["warnings"][0]
