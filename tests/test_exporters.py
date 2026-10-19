"""§10.7：ComfyUI 导出注入正确、filename_prefix 唯一；TXT/JSONL/captions 齐全。"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from charvar.exporters import comfyui, jsonl, txt
from charvar.exporters.captions import caption_filename, write_zip_bytes
from charvar.models import GenerateRequest
from charvar.preset_loader import load


def _workflow() -> dict:
    return {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "70": {"class_type": "Seed (rgthree)", "inputs": {"seed": -1}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }


def _mapping(seed_mode: str = "increment") -> dict:
    return {
        "output_nodes": ["47"],
        "mappings": [
            {"field": "prompt", "node_id": "11", "input_key": "text", "required": True},
            {"field": "negative_prompt", "node_id": "12", "input_key": "text", "default": "blurry, ugly"},
            {"field": "seed", "node_id": "70", "input_key": "seed", "default": -1, "mode": seed_mode},
            {"field": "filename_prefix", "node_id": "47", "input_key": "filename_prefix"},
        ],
    }


def _result(silver_witch, count=None):
    preset = load("q21_default_30")
    from charvar.composer import generate

    req = GenerateRequest(seed=42)
    if count:
        req = GenerateRequest(seed=42, count=count)
    return generate(silver_witch, preset, req)


def test_txt_one_prompt_per_line(silver_witch):
    result = _result(silver_witch)
    text = txt.render(result)
    lines = text.splitlines()
    assert len(lines) == 30
    assert lines[0] == result.items[0].prompt
    assert lines[-1] == result.items[-1].prompt


def test_jsonl_valid_items(silver_witch):
    result = _result(silver_witch)
    lines = jsonl.render(result).splitlines()
    assert len(lines) == 30
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["variant_id"] == "full_front"
    assert parsed[0]["prompt"] == result.items[0].prompt
    assert parsed[0]["meta"]["subseed"] == result.items[0].meta["subseed"]


def test_captions_zip_entries(silver_witch):
    result = _result(silver_witch)
    data = write_zip_bytes(result)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert names[0] == "000_full_front.txt"
        assert len(names) == 30
        caption = zf.read("000_full_front.txt").decode("utf-8")
        assert caption == result.items[0].caption + "\n"


def test_caption_filename_numbering(silver_witch):
    result = _result(silver_witch)
    assert caption_filename(result.items[0]) == "000_full_front.txt"
    assert caption_filename(result.items[29]) == "029_expr_silly.txt"


def test_comfy_payload_injection(silver_witch):
    """给定 mock workflow，注入后的字段正确，filename_prefix 唯一。"""
    result = _result(silver_witch)
    wf = _workflow()
    mapping = _mapping()
    prefixes = set()
    for item in result.items:
        payload = comfyui.build_payload(wf, mapping, result, item)
        assert payload is not wf
        assert payload["11"]["inputs"]["text"] == item.prompt
        assert payload["12"]["inputs"]["text"] == "blurry, ugly"
        assert payload["70"]["inputs"]["seed"] == result.seed + item.index
        prefix = payload["47"]["inputs"]["filename_prefix"]
        prefixes.add(prefix)
        assert prefix == comfyui.filename_prefix(result, item)
        # 原 workflow 未被污染
        assert wf["11"]["inputs"]["text"] == "old"
    assert len(prefixes) == 30
    assert any("_000_" in p for p in prefixes)


def test_comfy_seed_fixed_mode(silver_witch):
    result = _result(silver_witch, count=3)
    payload = comfyui.build_payload(_workflow(), _mapping(seed_mode="fixed"), result, result.items[2])
    assert payload["70"]["inputs"]["seed"] == result.seed


def test_comfy_seed_minus_one_kept_for_comfy_random(silver_witch):
    result = _result(silver_witch, count=1)
    mapping = _mapping()
    for entry in mapping["mappings"]:
        if entry["field"] == "seed":
            entry["mode"] = "fixed"
            entry["default"] = -1
    wf = _workflow()
    # overrides 把 seed 改成 -1 → 保留 -1（交给 ComfyUI 随机）
    payload = comfyui.build_payload(wf, mapping, result, result.items[0], overrides={"seed": -1})
    assert payload["70"]["inputs"]["seed"] == -1
    # workflow 未注入的默认 -1 也保持
    payload2 = comfyui.build_payload(_workflow(), mapping, result, result.items[0], overrides={"seed": -1})
    assert payload2["70"]["inputs"]["seed"] == -1


def test_comfy_payload_missing_node(silver_witch):
    result = _result(silver_witch, count=1)
    mapping = _mapping()
    mapping["mappings"][0]["node_id"] = "999"
    with pytest.raises(ValueError) as exc_info:
        comfyui.build_payload(_workflow(), mapping, result, result.items[0])
    assert "999" in str(exc_info.value)


def test_comfy_payload_on_missing_clears_unused_reference_slots(silver_witch):
    """可选参考图槽位未注入时：按 on_missing 断开消费者引用并移除 LoadImage 节点。"""
    result = _result(silver_witch, count=1)
    wf = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
        "514": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "515": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        "459:474": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "prompt": ["459:480", 0],
                "images.image_1": ["514", 0],
                "images.image_2": ["515", 0],
            },
        },
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}},
    }
    mapping = {
        "output_nodes": ["47"],
        "mappings": [
            {"field": "prompt", "node_id": "459:474", "input_key": "prompt", "required": True},
            {
                "field": "image",
                "node_id": "514",
                "input_key": "image",
                "required": False,
                "on_missing": {"remove_node": True, "unlink": [{"node_id": "459:474", "input_key": "images.image_1"}]},
            },
            {
                "field": "image2",
                "node_id": "515",
                "input_key": "image",
                "required": False,
                "on_missing": {"remove_node": True, "unlink": [{"node_id": "459:474", "input_key": "images.image_2"}]},
            },
        ],
    }

    # 两个都提供：注入 + 保留节点/引用
    payload = comfyui.build_payload(wf, mapping, result, result.items[0], overrides={"image": "a.png", "image2": "b.png"})
    assert payload["514"]["inputs"]["image"] == "a.png"
    assert payload["515"]["inputs"]["image"] == "b.png"
    assert payload["459:474"]["inputs"]["prompt"] == result.items[0].prompt
    assert payload["459:474"]["inputs"]["images.image_1"] == ["514", 0]
    assert payload["459:474"]["inputs"]["images.image_2"] == ["515", 0]

    # 只提供第一个：image2 槽位被摘除，绝不带 workflow 内置默认图
    payload = comfyui.build_payload(wf, mapping, result, result.items[0], overrides={"image": "a.png"})
    assert "515" not in payload
    assert payload["459:474"]["inputs"]["images.image_1"] == ["514", 0]
    assert "images.image_2" not in payload["459:474"]["inputs"]

    # 一个都不提供：两个槽位全部摘除
    payload = comfyui.build_payload(wf, mapping, result, result.items[0], overrides={})
    assert "514" not in payload and "515" not in payload
    assert "images.image_1" not in payload["459:474"]["inputs"]
    assert "images.image_2" not in payload["459:474"]["inputs"]


def test_comfy_payload_missing_required(silver_witch):
    result = _result(silver_witch, count=1)
    mapping = _mapping()
    # field 命名空间里没有 negative override（必填），且无 default
    mapping["mappings"][1] = {"field": "negative_prompt", "node_id": "12", "input_key": "text", "required": True}
    with pytest.raises(ValueError) as exc_info:
        comfyui.build_payload(_workflow(), mapping, result, result.items[0])
    assert "negative_prompt" in str(exc_info.value)


def test_workflows_dir_pair_loading(tmp_path):
    (tmp_path / "DemoWF.json").write_text(json.dumps(_workflow()), encoding="utf-8")
    (tmp_path / "DemoWF.mapping.json").write_text(json.dumps(_mapping()), encoding="utf-8")
    (tmp_path / "OnlyWorkflow.json").write_text(json.dumps(_workflow()), encoding="utf-8")
    workflows = comfyui.list_workflows(tmp_path)
    demo = next(w for w in workflows if w["name"] == "DemoWF")
    assert demo == {"name": "DemoWF", "has_workflow": True, "has_mapping": True}
    orphan = next(w for w in workflows if w["name"] == "OnlyWorkflow")
    assert orphan == {"name": "OnlyWorkflow", "has_workflow": True, "has_mapping": False}

    wf, mapping = comfyui.load_workflow_pair("DemoWF", workflows_dir=tmp_path)
    assert "11" in wf and mapping["output_nodes"] == ["47"]
    with pytest.raises(FileNotFoundError):
        comfyui.load_workflow_pair("OnlyWorkflow", workflows_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        comfyui.load_workflow_pair("NoSuchWorkflow", workflows_dir=tmp_path)


# ---------- 负向词三态（显式空串 ≠ 缺失） ----------

def test_submit_negative_prompt_states(silver_witch, monkeypatch) -> None:
    """继承(None/缺失) → 景别回退；显式值含空串生效；显式值永远优先于回退。"""
    import httpx as httpx_mod

    from charvar.exporters import comfyui as ce

    from charvar.models import PromptItem

    captured: list[dict] = []

    def handler(request: httpx_mod.Request) -> httpx_mod.Response:
        if request.url.path == "/prompt":
            captured.append(json.loads(request.content.decode()))
            return httpx_mod.Response(200, json={"prompt_id": "p"})
        return httpx_mod.Response(404)

    class FakeClient:
        def __init__(self, **kw):
            self._inner = httpx_mod.Client(transport=httpx_mod.MockTransport(handler))

        def __enter__(self):
            return self._inner

        def __exit__(self, *args):
            self._inner.close()

    monkeypatch.setattr(ce, "_httpx_client", lambda **kw: FakeClient(**kw))

    closeup_item = PromptItem(
        index=0, variant_id="chest_front", prompt="p", master="", variant_text="v",
        background=None, lighting=None, caption="c", meta={"framing": "chest"},
    )
    full_item = PromptItem(
        index=1, variant_id="full_front", prompt="p", master="", variant_text="v",
        background=None, lighting=None, caption="c", meta={"framing": "full"},
    )
    result = SimpleNamespace(seed=42, subject_id="demo", items=[closeup_item, full_item])

    wf = _workflow()
    mapping = _mapping()  # negative_prompt 字段 default "blurry, ugly"
    closeup_default = "full body, lower body, hips, waist, shorts, socks, shoes, legs, feet, knees, thighs, floor, ground"

    def _negative_of(payload: dict, item_index_key: int) -> str:
        return payload["prompt"]["12"]["inputs"]["text"]

    # 1) overrides 缺失 → 继承：chest → closeup 句；full → 空串
    ce.submit(result, wf, mapping, wait=False)
    assert _negative_of(captured[0], 0) == closeup_default
    assert _negative_of(captured[1], 1) == ""

    # 2) 显式空串 → 明确不用（不被 closeup 回退覆盖）
    captured.clear()
    ce.submit(result, wf, mapping, wait=False, overrides={"negative_prompt": ""})
    assert _negative_of(captured[0], 0) == ""
    assert _negative_of(captured[1], 1) == ""

    # 3) 显式自定义值（含对 chest 优先于回退）
    captured.clear()
    ce.submit(result, wf, mapping, wait=False, overrides={"negative_prompt": "custom neg"})
    assert _negative_of(captured[0], 0) == "custom neg"
    assert _negative_of(captured[1], 1) == "custom neg"
