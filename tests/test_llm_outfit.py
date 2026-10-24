"""LLM 模式 × 服装组合：服装必须真的进到最终提示词里。

背景（实测 bug）：run `white_skirt_stockings_red_bob_glasses_poses_mature_milf_outfits_s58201…`
的 24 条改写提示词**全部**没有服装描述——LLM 把服装段当成「参考图自带服装」整段丢弃
（锚定句当时要求 "copy the complete outfit … from <image1>"，两套服装指令互相矛盾）。

本文件锁住三件事：
1. 服装段与动作段分列（LLM 输入里 outfit 是独立字段）；
2. 服装模式下锚定句不再让参考图提供服装；
3. 模型仍没写服装时，程序按原文兜底补写并给声明。
"""
from __future__ import annotations

import io
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import llm_compose
from charvar.api import build_app
from charvar.outfit import insert_outfit_sentence, outfit_covered, outfit_keywords
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


def _mock_llm(monkeypatch, *, mode: str):
    """mode=drop：改写结果里没有服装；mode=copy：逐字抄服装原文。"""
    from charvar import llm

    def fake_chat_json(messages, **kw):
        user = messages[1]["content"]
        intents = json.loads(user.split("【变体列表】\n", 1)[1])
        variants = []
        for intent in intents:
            outfit = intent.get("outfit") or ""
            body = "The figure stands facing the camera with feet shoulder-width apart."
            if mode == "copy" and outfit:
                body = f"{body} {outfit}"
            variants.append({"id": intent["id"], "prompt": body})
        return {"variants": variants}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)


# ---------- 组合层：动作段 / 服装段分列 ----------

def test_outfit_combine_splits_action_and_outfit():
    from charvar.outfit import combine, load_outfit

    pose = load("three_pose_pack")
    merged = combine(pose, load_outfit("mature_milf_outfits"))
    spec = merged.variants[0]
    assert spec.outfit == "milf_a_shirt"
    assert spec.outfit_label == "衬衫版（紫罗兰衬衫＋砖红短裙）"
    # 分列留档：动作原文与服装原文各自完整
    assert spec.action_text == pose.variants[0].text
    assert spec.outfit_text and spec.outfit_text.startswith("wearing ")
    # 合并文本照旧（classic 模式直接拼这一份）
    assert spec.text == f"{spec.action_text} {spec.outfit_text}"


def test_collect_intent_separates_outfit():
    from charvar.models import GenerateRequest, SubjectProfile
    from charvar.outfit import combine, load_outfit

    subject = SubjectProfile(id="s", name="n", subject_noun="woman")
    merged = combine(load("three_pose_pack"), load_outfit("mature_milf_outfits"))
    req = GenerateRequest(preset="three_pose_pack", seed=1)
    intents = llm_compose.collect_intent(subject, merged, req)
    assert len(intents) == 24
    intent = intents[0]
    assert intent["outfit"], "服装段必须作为独立字段进 LLM 输入"
    assert "wearing" not in intent["text"], "动作段里不应再夹着服装段"
    assert intent["outfit_label"] == "衬衫版（紫罗兰衬衫＋砖红短裙）"


def test_collect_intent_without_outfit_has_no_outfit_key():
    from charvar.models import GenerateRequest, SubjectProfile

    subject = SubjectProfile(id="s", name="n", subject_noun="woman")
    intents = llm_compose.collect_intent(
        subject, load("three_pose_pack"), GenerateRequest(preset="three_pose_pack", seed=1)
    )
    assert all("outfit" not in i for i in intents)


# ---------- 提示词构造层：锚定句与硬规则 ----------

def _messages(intents, refs=None, language="en"):
    from charvar.llm_prompts import build_rewrite_messages
    from charvar.models import SubjectProfile

    subject = SubjectProfile(id="s", name="n", subject_noun="woman", reference_tag="<image1>")
    return build_rewrite_messages(
        subject, "# 模板", refs if refs is not None else [{"kind": "front", "tag": "<image1>"}],
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


def test_outfit_mode_anchor_does_not_take_outfit_from_reference():
    msgs = _messages([_intent(outfit="wearing a brick-red mini skirt and black pumps")])
    anchor = msgs[1]["content"].split("【参考图分工锚定句】", 1)[1]
    assert "complete outfit" not in anchor, "服装模式下锚定句不能让参考图提供服装"
    assert "Copy the body shape and proportions from <image1>" in anchor


def test_outfit_mode_system_has_outfit_rule():
    msgs = _messages([_intent(outfit="wearing a brick-red mini skirt and black pumps")])
    system = msgs[0]["content"]
    assert "outfit 字段是该变体必须穿着的服装" in system
    assert "逐字采用 outfit 原文" in system
    assert "不换用参考图里那套衣服" in system


def test_no_outfit_mode_system_prompt_unchanged():
    """没有服装组合时，系统提示词逐字不变（英文分支红线）。"""
    msgs = _messages([_intent()], language="en")
    assert msgs[0]["content"] == _EN_SYSTEM_SNAPSHOT
    assert "outfit 字段是该变体必须穿着的服装" not in msgs[0]["content"]


def test_outfit_mode_zh_allows_english_outfit_clause():
    """中文分支：服装描述逐字保留英文原文 → 中文规则要开这个例外。"""
    msgs = _messages([_intent(outfit="wearing a brick-red mini skirt")], language="zh")
    system = msgs[0]["content"]
    assert "唯一的例外是 outfit 字段的服装描述" in system


# ---------- 校验与兜底补写 ----------

def test_outfit_keywords_and_covered():
    text = "wearing a dark slate-violet button-up shirt, a brick-red mini skirt, black pumps"
    keys = outfit_keywords(text)
    assert "shirt" in keys and "slate-violet" in keys
    assert "with" not in keys and "wearing" not in keys  # 虚词不进内容词
    assert outfit_covered("She is wearing " + text, text)
    assert not outfit_covered("She stands facing the camera.", text)


def test_insert_outfit_sentence_before_background():
    prompt = "The figure stands facing the camera. The background is a plain white backdrop. The lighting is soft."
    out = insert_outfit_sentence(prompt, "wearing a brick-red mini skirt")
    assert out.index("wearing a brick-red mini skirt") < out.index("The background is")
    # 锚定句里句中的 background, and lighting 不该被当成锚点
    anchor = "Use the same art style. Create a new pose, camera angle, background, and lighting. One character only."
    out2 = insert_outfit_sentence(anchor, "wearing a brick-red mini skirt")
    assert out2.endswith("wearing a brick-red mini skirt.")


# ---------- 端到端（mock LLM） ----------

def _gen(client, monkeypatch, mode):
    _mock_llm(monkeypatch, mode=mode)
    _ready_subject(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "three_pose_pack",
            "outfit_preset": "mature_milf_outfits",
            "count": 3,
            "seed": 7,
            "mode": "llm",
            "reference_kinds": ["character_sheet"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_llm_mode_outfit_appended_when_model_drops_it(client, monkeypatch):
    """模型把服装丢了 → 程序按原文补写 + run 声明。"""
    result = _gen(client, monkeypatch, mode="drop")
    assert len(result["items"]) == 3
    for item in result["items"]:
        assert "wearing a dark slate-violet button-up shirt" in item["prompt"]
        assert item["meta"]["outfit"] == "milf_a_shirt"
    assert any("服装描述没在 LLM 改写结果里体现" in w for w in result["warnings"])


def test_llm_mode_outfit_kept_when_model_writes_it(client, monkeypatch):
    """模型自己写了服装 → 不重复补写，也不声明。"""
    result = _gen(client, monkeypatch, mode="copy")
    for item in result["items"]:
        assert item["prompt"].count("wearing a dark slate-violet button-up shirt") == 1
    assert not any("服装" in w for w in result["warnings"])


def test_classic_mode_outfit_still_in_prompt(client, monkeypatch):
    """经典模式不受影响：服装段照旧拼在动作段之后。"""
    _ready_subject(client)
    resp = client.post(
        "/generate",
        json={
            "subject_id": "demo_witch",
            "preset": "three_pose_pack",
            "outfit_preset": "mature_milf_outfits",
            "count": 1,
            "seed": 7,
        },
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert "wearing a dark slate-violet button-up shirt" in item["prompt"]
    # 经典模式：动作段在前、服装段拼在动作段之后（合并文本）
    assert item["variant_text"].endswith("matte leather")
    assert item["prompt"].index("wearing a dark slate-violet") > 0


# 英文分支系统提示词快照（改动前逐字记录，防止英文分支漂移）。
_EN_SYSTEM_SNAPSHOT = """你是角色图像提示词改写器。输入：角色档案、角色全场景通用提示词模板（权威人设依据）、若干参考图说明（每张有 <imageN> 标签与用途），以及一批变体的结构化意图。
为每条变体重写一条完整英文提示词，句子顺序固定：
1) 参考图锚定：逐字采用给定的【参考图分工锚定句】整句，不得改写、增删分句；没有参考图时省略该句；该锚定句在本批所有变体中逐字相同；
2) 目标景别：采用意图 text 的景别/构图描述（full=全身 / chest=胸部以上 / head=头部特写）；
3) 躯干/头部/视线：按【视角规范】里对应 angle 的英文语句写身体朝向、头部朝向与视线；意图 text 中的朝向与姿态语义必须保留，不得削弱或反转；
4) 目标表情：按意图 expression 写；angle 为 back 时省略表情句；l135/r135 只保留面部部分可见的说明，不细写眉眼嘴；
5) 背景/光照：逐字采用给定的 background 与 lighting，不改写。
硬性规则：
- 不在每条提示词里复述模板的整段角色设定，身份细节交给参考图；不混合模板的多个版本（正常/精简、多套服装）或双主体分段；
- framing=full 时必须包含【视角规范】中的全身完整入镜句；framing 为 chest/head 时必须纳入给定的 framing_guard 句子以阻断下半身；
- 被遮挡的细节按视角自然隐藏，不强行转体/错开肢体展示；不对称细节（痣、挑染、纹样）在左右朝向互换时保持贴身位置，不镜像；
- 每条 prompt 为一个英文字符串，不出现中文、【】小节标题或 {{}} 占位符；只输出提示词本身；
- 必须为输入列表中的每个 id 各输出一条，不得增删。
只输出 JSON：{"variants": [{"id": "<variant_id>", "prompt": "<english prompt>"}]}"""
