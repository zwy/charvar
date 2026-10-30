"""LLM 模式 × 发型组合：发型必须真的进到最终提示词里。

镜像 test_llm_outfit.py，锁住四件事：
1. 发型段与动作段分列（LLM 输入里 hairstyle 是独立字段）；
2. 发型模式下锚定句不再让参考图提供头发结构（发色/脸仍由参考图提供）；
3. 发型+服装同时开时锚定句与系统规则都正确融合；
4. 模型仍没写发型时，程序按原文兜底补写并给声明，且插在服装段之前。
"""
from __future__ import annotations

import io
import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import llm_compose
from charvar.api import build_app

@pytest.fixture(autouse=True)
def _studio_outfit_db(tmp_path, monkeypatch):
    """隔离数据目录 + 从仓库镜像装 studio 服装家族（服装家族现住 costume 双表，不碰真库）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    from charvar import costumes as costumes_service
    from charvar import storage

    with storage.connect() as conn:
        costumes_service.import_studio_yaml(conn)

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


def _ready_subject(client):
    client.post("/subjects", json={"id": "demo_witch", "name": "演示"})
    up = client.post(
        "/subjects/demo_witch/images/character_sheet/upload",
        files={"file": ("sheet.png", _png_bytes(), "image/png")},
    )
    assert up.json()["is_ready"]
    client.put("/subjects/demo_witch/template", json={"template_md": "# 通用模板"})


# ---------- 组合层：动作段 / 发型段分列 ----------

def test_collect_intent_separates_hairstyle():
    from charvar import hairstyle as hairstyle_service
    from charvar.models import GenerateRequest, SubjectProfile
    from charvar.outfit import combine as outfit_combine, load_outfit

    subject = SubjectProfile(id="s", name="n", subject_noun="woman")
    pose = load("three_pose_pack")
    hair = hairstyle_service.load_hairstyle("popular_hairstyles_v1_4")
    merged = hairstyle_service.combine(pose, hair)
    req = GenerateRequest(preset="three_pose_pack", seed=1)
    intents = llm_compose.collect_intent(subject, merged, req)
    assert len(intents) == 12
    intent = intents[0]
    assert intent["hairstyle"], "发型段必须作为独立字段进 LLM 输入"
    assert "French bob" not in intent["text"], "动作段里不应再夹着发型段"
    assert intent["hairstyle_label"] == "法式波波头"

    # 发型+服装双层：三个意图字段各就各位
    layered = outfit_combine(merged, load_outfit("stockings_8"))
    intents2 = llm_compose.collect_intent(subject, layered, GenerateRequest(preset="x", seed=1))
    assert intents2[0]["hairstyle"] == intents[0]["hairstyle"]
    assert intents2[0]["outfit"], "服装字段仍在"
    assert "wearing" not in intents2[0]["text"]


def test_collect_intent_without_hairstyle_has_no_hairstyle_key():
    from charvar.models import GenerateRequest, SubjectProfile

    subject = SubjectProfile(id="s", name="n", subject_noun="woman")
    intents = llm_compose.collect_intent(
        subject, load("three_pose_pack"), GenerateRequest(preset="three_pose_pack", seed=1)
    )
    assert all("hairstyle" not in i for i in intents)


# ---------- 提示词构造层：锚定句与硬规则 ----------

def _messages(intents, refs=None, language="en"):
    from charvar.llm_prompts import build_rewrite_messages
    from charvar.models import SubjectProfile

    subject = SubjectProfile(id="s", name="n", subject_noun="woman", reference_tag="<image1>")
    return build_rewrite_messages(
        subject, "# 模板", refs if refs is not None else [{"kind": "headshot", "tag": "<image1>"}],
        intents, None, text_policy="forbid", composition_policy="legacy", language=language,
    )


def _intent(**over):
    base = {
        "id": "v1", "text": "pose text", "framing": "full", "angle": "front",
        "expression": None, "pose": None, "background": "bg", "lighting": "light",
        "framing_guard": None,
    }
    base.update(over)
    return base


def test_hairstyle_mode_anchor_does_not_take_hair_from_reference():
    """发型模式下 headshot 分句：脸/肤色/发色由参考图提供，发型结构归发型预设；
    且带裸头声明——参考图里的帽子和头饰会盖住刘海/发顶，预设描述落进被遮挡区
    （2026-10-27 实测：autumn_birch_girl 针织帽批 4/4 未落实预设发型）。"""
    msgs = _messages([_intent(hairstyle="chin-length French bob with light wispy bangs")])
    anchor = msgs[1]["content"].split("【参考图分工锚定句】", 1)[1]
    assert "skin tone, and hair color" in anchor, "发色锚定必须在"
    assert "follow the prescribed hairstyle instead of <image1>" in anchor
    assert "her head is bare" in anchor and "no headwear" in anchor
    # 基础版的「copy ... hair, skin tone ...」不能出现——那是照抄参考图发型
    assert "proportions, hair, skin tone" not in anchor


def test_hairstyle_mode_anchor_side_drops_hair_flow():
    msgs = _messages(
        [_intent(hairstyle="chin-length French bob")],
        refs=[{"kind": "side", "tag": "<image1>"}],
    )
    anchor = msgs[1]["content"].split("【参考图分工锚定句】", 1)[1]
    assert "hair flow" not in anchor, "发型模式下侧面分句不再锚 hair flow"


def test_outfit_hairstyle_both_anchor_fused():
    """发型+服装同时开：front 用服装版（体量锚），headshot 用发型版，behind 融合。"""
    msgs = _messages(
        [_intent(hairstyle="chin-length French bob", outfit="wearing a black swimsuit")],
        refs=[
            {"kind": "front", "tag": "<image1>"},
            {"kind": "behind", "tag": "<image2>"},
        ],
    )
    anchor = msgs[1]["content"].split("【参考图分工锚定句】", 1)[1]
    assert "bust volume and hip width" in anchor, "体量锚（服装版 front）在"
    assert "rear silhouette and outfit details" in anchor, "behind 融合分句在"
    assert "the back of the head follows the prescribed hairstyle" in anchor
    assert "the head bare with no headwear" in anchor, "发型模式 behind 分句带裸头声明"
    # front 服装版不再让参考图提供服装
    assert "complete outfit" not in anchor


def test_hairstyle_mode_system_has_hairstyle_rule():
    from charvar.llm_prompts import BARE_HEAD_SENTENCE

    msgs = _messages([_intent(hairstyle="chin-length French bob with light wispy bangs")])
    system = msgs[0]["content"]
    assert "hairstyle 字段是该变体的发型" in system
    assert "逐字" in system and "发色仍按参考图与档案保留" in system
    # 头部佩戴物不保留；裸头声明逐字进 prompt；发型句提前到锚定句之后
    assert "头部佩戴物不保留" in system
    assert BARE_HEAD_SENTENCE in system
    assert "贴住锚定句" in system or "锚定句之后" in system


def test_hairstyle_rule_absent_without_hairstyle():
    """无发型组合：裸头声明与发型规则不出现（存量行为不变）。"""
    msgs = _messages([_intent()])
    assert "her head is bare" not in msgs[1]["content"]
    assert "hairstyle 字段是该变体的发型" not in msgs[0]["content"]


def test_insert_hairstyle_sentence_includes_bare_head():
    """程序兜底补写：发型原文 + 裸头声明同组插入。"""
    from charvar import hairstyle as hairstyle_service

    prompt = "She stands facing the camera. The figure faces the camera directly. Background: a plain gray wall."
    out = hairstyle_service.insert_hairstyle_sentence(prompt, "chin-length French bob")
    assert "chin-length French bob" in out
    assert "no hat, no headwear" in out
    assert "Background:" in out and out.index("chin-length French bob") < out.index("Background:")


def test_hairstyle_mode_zh_allows_english_hairstyle_clause():
    """中文分支：发型描述逐字保留英文原文 → 中文规则要开这个例外。"""
    msgs = _messages([_intent(hairstyle="chin-length French bob")], language="zh")
    system = msgs[0]["content"]
    assert "唯一的例外是 hairstyle 字段的发型描述" in system


def test_both_mode_zh_merges_exception():
    msgs = _messages(
        [_intent(hairstyle="chin-length French bob", outfit="wearing a black swimsuit")],
        language="zh",
    )
    system = msgs[0]["content"]
    assert "唯一的例外是 outfit 与 hairstyle 字段的描述" in system


# ---------- 端到端（mock LLM） ----------

def _mock_llm(monkeypatch, *, mode: str):
    """mode=drop：改写结果里没有发型；mode=copy：逐字抄发型原文；
    mode=drop_all：发型与服装都丢。"""
    from charvar import llm

    def fake_chat_json(messages, **kw):
        user = messages[1]["content"]
        intents = json.loads(user.split("【变体列表】\n", 1)[1])
        variants = []
        for intent in intents:
            hair = intent.get("hairstyle") or ""
            outfit = intent.get("outfit") or ""
            body = "The figure stands facing the camera with feet shoulder-width apart."
            if mode == "copy" and hair:
                body = f"{body} {hair}"
            if mode == "copy" and outfit:
                body = f"{body} {outfit}"
            variants.append({"id": intent["id"], "prompt": body})
        return {"variants": variants}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)


def _gen(client, monkeypatch, mode, *, with_outfit: bool = False):
    _mock_llm(monkeypatch, mode=mode)
    _ready_subject(client)
    payload = {
        "subject_id": "demo_witch",
        "preset": "three_pose_pack",
        "hairstyle_preset": "popular_hairstyles_v1_4",
        "count": 3,
        "seed": 7,
        "mode": "llm",
        "reference_kinds": ["character_sheet"],
    }
    if with_outfit:
        payload["outfit_preset"] = "stockings_8"
    resp = client.post("/generate", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_llm_mode_hairstyle_appended_when_model_drops_it(client, monkeypatch):
    """模型把发型丢了 → 程序按原文补写（带裸头声明）+ run 声明。"""
    result = _gen(client, monkeypatch, mode="drop")
    assert len(result["items"]) == 3
    for item in result["items"]:
        assert "chin-length French bob" in item["prompt"]
        assert "no hat, no headwear" in item["prompt"], "补写组必须带裸头声明"
        assert item["meta"]["hairstyle"] == "french_bob"
    assert any("发型描述没在 LLM 改写结果里体现" in w for w in result["warnings"])


def test_llm_mode_hairstyle_kept_when_model_writes_it(client, monkeypatch):
    """模型自己写了发型 → 不重复补写，也不声明。"""
    result = _gen(client, monkeypatch, mode="copy")
    for item in result["items"]:
        assert item["prompt"].count("chin-length French bob") == 1
    assert not any("发型" in w for w in result["warnings"])


def test_llm_mode_both_dropped_order_outfit_before_hair(client, monkeypatch):
    """发型与服装都被丢时：服装段兜底落在最前面，发型段在其后。"""
    result = _gen(client, monkeypatch, mode="drop_all", with_outfit=True)
    hair_snip = "chin-length French bob, light wispy bangs"
    outfit_snip = "wearing sheer black nylon pantyhose"
    for item in result["items"]:
        assert hair_snip in item["prompt"] and outfit_snip in item["prompt"]
        assert item["prompt"].index(outfit_snip) < item["prompt"].index(hair_snip)
    assert any("发型描述没在" in w for w in result["warnings"])
    assert any("服装描述没在" in w for w in result["warnings"])


def test_classic_mode_hairstyle_still_in_prompt(client, monkeypatch):
    """经典模式不受影响：发型段照旧拼在动作段之后。"""
    _ready_subject(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "three_pose_pack",
            "hairstyle_preset": "popular_hairstyles_v1_4",
            "count": 1,
            "seed": 7,
        },
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert "chin-length French bob" in item["prompt"]
    assert item["meta"]["hairstyle"] == "french_bob"