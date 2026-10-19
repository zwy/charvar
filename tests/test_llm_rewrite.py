"""LLM 生成优化模式：mode=llm + 参考图入队（mock LLM + mock ComfyUI）。"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar.api import build_app
from charvar import paths
from charvar.preset_loader import load


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_llm(monkeypatch, *, variants_override=None):
    """mock LLM：返回 q21 预设全部 30 个 id 的改写结果（或指定子集）。"""
    from charvar import llm

    preset = load("q21_default_30")
    ids = [v.id for v in preset.variants]
    chosen = variants_override if variants_override is not None else ids

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {"variants": [{"id": vid, "prompt": f"Rewritten {vid}"} for vid in chosen]},
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {}})

    class FakeClient:
        def __init__(self, **kw):
            self._inner = httpx.Client(transport=httpx.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(llm, "_http_client", lambda **kw: FakeClient(**kw))


def _mock_comfy(monkeypatch, uploads: list[str]):
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


def _ready_subject(client):
    """建主体 + 上传 character_sheet + 写模板。"""
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    up = client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    assert up.json()["is_ready"]
    client.put("/subjects/demo_witch/template", json={"template_md": "# 通用模板"})


def test_generate_llm_mode_full(client, monkeypatch):
    _mock_llm(monkeypatch)
    _ready_subject(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "mode": "llm",
            "reference_kinds": ["character_sheet"],
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert len(result["items"]) == 30
    assert result["items"][0]["variant_id"] == "full_front"
    assert result["items"][0]["prompt"] == "Rewritten full_front"
    assert result["items"][0]["meta"]["mode"] == "llm"
    assert result["items"][0]["meta"]["reference_kinds"] == ["character_sheet"]
    # 经典 master 模板不再出现
    assert "Create an image of" not in result["items"][0]["prompt"]
    run_id = result["run_id"]
    assert client.get(f"/runs/{run_id}").json()["seed"] == 42

    # 同 seed 背景/光照采样确定性
    resp2 = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "mode": "llm",
            "reference_kinds": ["character_sheet"],
        },
    ).json()
    assert [(i["background"], i["lighting"]) for i in result["items"]] == [
        (i["background"], i["lighting"]) for i in resp2["items"]
    ]
    # LLM 模式的 run 不影响经典模式
    classic = client.post(
        "/generate", json={"subject_id": "demo_witch", "preset": "q21_default_30", "seed": 42}
    ).json()
    assert "Create an image of" in classic["items"][0]["prompt"]


def test_generate_llm_missing_variant(client, monkeypatch):
    _mock_llm(monkeypatch, variants_override=["full_front"])
    _ready_subject(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 1,
            "mode": "llm",
            "reference_kinds": ["character_sheet"],
        },
    )
    assert resp.status_code == 502
    assert "缺少变体" in resp.json()["detail"]


def test_generate_llm_validation(client, monkeypatch):
    _mock_llm(monkeypatch)
    # 无 subject_id
    assert client.post("/generate", json={"mode": "llm", "reference_kinds": ["character_sheet"]}).status_code == 400
    client.post("/subjects", json={"id": "bare", "name": "裸"})
    # 参考图未就绪
    resp = client.post(
        "/generate",
        json={"subject_id": "bare", "mode": "llm", "reference_kinds": ["character_sheet"]},
    )
    assert resp.status_code == 400
    # 超过 4 个参考图
    resp = client.post(
        "/generate",
        json={
            "subject_id": "bare",
            "mode": "llm",
            "reference_kinds": ["character_sheet", "headshot", "front", "side", "behind"],
        },
    )
    assert resp.status_code == 400
    # 缺通用模板
    up = client.post(
        "/subjects/bare/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    assert up.status_code == 200
    resp = client.post(
        "/generate",
        json={"subject_id": "bare", "mode": "llm", "reference_kinds": ["character_sheet"]},
    )
    assert resp.status_code == 400
    assert "模板" in resp.json()["detail"]


def test_queue_with_reference_kinds(client, tmp_path, monkeypatch):
    _mock_llm(monkeypatch)
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    nodes = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "470": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    (workflows_dir / "TestEdit.json").write_text(json.dumps(nodes), encoding="utf-8")
    (workflows_dir / "TestEdit.mapping.json").write_text(
        json.dumps(
            {
                "output_nodes": ["47"],
                "mappings": [
                    {"field": "image", "node_id": "470", "input_key": "image", "required": False},
                    {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads)
    _ready_subject(client)

    run_id = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "mode": "llm",
            "reference_kinds": ["character_sheet"],
        },
    ).json()["run_id"]

    resp = client.post(
        f"/runs/{run_id}/comfyui",
        json={"workload": "TestEdit", "reference_kinds": ["character_sheet"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["succeeded"] == 30
    assert uploads == ["demo_witch_character_sheet.png"]  # 30 条共享一次上传
    for payload in queued:
        assert payload["prompt"]["470"]["inputs"]["image"] == "demo_witch_character_sheet.png"
        assert payload["prompt"]["11"]["inputs"]["text"].startswith("Rewritten ")


def test_queue_multi_reference_mapping_1_to_4(client, tmp_path, monkeypatch):
    """1–4 参考图槽位：选 2 个 → 只上传/注入这 2 个；未选的槽位节点和引用从 payload 摘除。"""
    _mock_llm(monkeypatch)
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    nodes = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "514": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "515": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "516": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "517": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "459:474": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "prompt": ["459:480", 0],
                "images.image_1": ["514", 0],
                "images.image_2": ["515", 0],
                "images.image_3": ["516", 0],
                "images.image_4": ["517", 0],
            },
        },
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    (workflows_dir / "Edit4.json").write_text(json.dumps(nodes), encoding="utf-8")
    mappings = [{"field": "prompt", "node_id": "459:474", "input_key": "prompt", "required": True}]
    for i, (field, node_id) in enumerate(
        [("image", "514"), ("image2", "515"), ("image3", "516"), ("image4", "517")]
    ):
        mappings.append(
            {
                "field": field,
                "node_id": node_id,
                "input_key": "image",
                "required": False,
                "on_missing": {"remove_node": True, "unlink": [{"node_id": "459:474", "input_key": f"images.image_{i + 1}"}]},
            }
        )
    (workflows_dir / "Edit4.mapping.json").write_text(
        json.dumps({"output_nodes": ["47"], "mappings": mappings}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(workflows_dir))
    uploads: list[str] = []
    queued = _mock_comfy(monkeypatch, uploads)
    _ready_subject(client)
    client.post(
        "/subjects/demo_witch/images/headshot/upload",
        files={"file": ("headshot.png", _png_bytes(), "image/png")},
    )

    run_id = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "q21_default_30",
            "seed": 42,
            "mode": "llm",
            "reference_kinds": ["character_sheet", "headshot"],
        },
    ).json()["run_id"]

    resp = client.post(
        f"/runs/{run_id}/comfyui",
        json={"workload": "Edit4", "reference_kinds": ["character_sheet", "headshot"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["succeeded"] == 30
    # 只上传选中的 2 张，顺序与参考说明一致
    assert uploads == ["demo_witch_character_sheet.png", "demo_witch_headshot.png"]
    for payload in queued:
        wf = payload["prompt"]
        assert wf["514"]["inputs"]["image"] == "demo_witch_character_sheet.png"
        assert wf["515"]["inputs"]["image"] == "demo_witch_headshot.png"
        assert wf["459:474"]["inputs"]["images.image_1"] == ["514", 0]
        assert wf["459:474"]["inputs"]["images.image_2"] == ["515", 0]
        # 未选中的槽位整块摘除，不带默认图
        assert "516" not in wf and "517" not in wf
        assert "images.image_3" not in wf["459:474"]["inputs"]
        assert "images.image_4" not in wf["459:474"]["inputs"]


def _intents(ids: list[str]) -> list[dict]:
    return [
        {
            "id": vid,
            "text": "variant text",
            "framing": "full",
            "angle": "front",
            "expression": None,
            "background": "bg",
            "lighting": "light",
            "framing_guard": None,
        }
        for vid in ids
    ]


def test_salvage_variants_from_truncated_text():
    from charvar import llm_compose

    text = '{"variants":[{"id":"full_front","prompt":"P one"},{"id":"full_l45","prompt":"P tw'
    assert llm_compose._salvage_variants(text) == {"variants": [{"id": "full_front", "prompt": "P one"}]}
    # prose 包裹也能从 variants 数组起点捞
    wrapped = '好的： {"variants":[{"id":"a","prompt":"x"},{"id":"b","prompt":"y"}]…被截断'
    assert llm_compose._salvage_variants(wrapped) == {"variants": [{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}]}
    assert llm_compose._salvage_variants("完全没有 JSON") == {"variants": []}


def test_rewrite_missing_ids_retry(monkeypatch):
    """缺失条目触发缩批补调一次；schema 传入且 max_tokens 不设上限。"""
    import charvar.llm_prompts as llm_prompts
    from charvar import llm, llm_compose
    from charvar.models import SubjectProfile

    calls: list[dict] = []

    def fake_chat_json(messages, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return {"variants": [{"id": "a", "prompt": "P1"}, {"id": "b", "prompt": "P2"}]}
        return {"variants": [{"id": "c", "prompt": "P3"}]}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    out = llm_compose.rewrite_prompts(
        SubjectProfile(id="s", name="n", subject_noun="person"), "", _intents(["a", "b", "c"]), []
    )
    assert out == {"a": "P1", "b": "P2", "c": "P3"}
    assert len(calls) == 2
    assert calls[0].get("schema") is llm_prompts.REWRITE_SCHEMA
    assert calls[0].get("max_tokens") is None


def test_rewrite_salvage_truncated_then_retry(monkeypatch):
    """截断 → 抢救已完整条目 → 用缺失 id 补调补齐。"""
    from charvar import llm, llm_compose
    from charvar.models import SubjectProfile

    texts = [
        '{"variants":[{"id":"a","prompt":"P1"},{"id":"b","prompt":"P2',  # 截断
        '{"variants":[{"id":"b","prompt":"P2b"},{"id":"c","prompt":"P3"}]}',
    ]
    calls: list[int] = []

    def fake_chat_json(messages, **kw):
        calls.append(1)
        text = texts[len(calls) - 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise llm.LLMParseError("invalid JSON response", text=text)

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    out = llm_compose.rewrite_prompts(
        SubjectProfile(id="s", name="n", subject_noun="person"), "", _intents(["a", "b", "c"]), []
    )
    assert out == {"a": "P1", "b": "P2b", "c": "P3"}
    assert len(calls) == 2


def test_rewrite_still_missing_after_retry_raises(monkeypatch):
    from charvar import llm, llm_compose
    from charvar.models import SubjectProfile

    def fake_chat_json(messages, **kw):
        return {"variants": [{"id": "a", "prompt": "P1"}]}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    with pytest.raises(llm.LLMError, match="缺少变体"):
        llm_compose.rewrite_prompts(
            SubjectProfile(id="s", name="n", subject_noun="person"), "", _intents(["a", "b"]), []
        )
