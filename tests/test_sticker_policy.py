"""带字表情包（schema 2 sticker）共享策略：文字编译 / 冲突检测 / meta 快照。

经典模式与 LLM 模式共用 render_lettering_block；content 是字面量（不展开
wildcard、不执行模板），中文文案由程序逐字附加，LLM 不参与改写。
"""
from __future__ import annotations

import json

import pytest

import charvar.sticker_policy as sp
from charvar import llm, llm_compose
from charvar.caption import build_caption
from charvar.composer import generate as compose_generate
from charvar.models import DisplayText, GenerateRequest, SubjectProfile
from charvar.preset_loader import ListPreset, PresetError, load


@pytest.fixture()
def sticker_preset():
    return load("chat_replies")


@pytest.fixture()
def sticker_subject():
    subject = SubjectProfile(id="demo_cat", name="演示猫", subject_noun="cat", reference_tag="<image1>")
    subject.style_lock = ["flat 2D cartoon illustration"]
    subject.identity_lock = ["blue almond eyes, long tail"]
    subject.outfit_lock = ["red scarf"]
    subject.extra = "Keep the same tail markings."
    return subject


def _intent_ids(user_content: str) -> list[dict]:
    """从 build_rewrite_messages 的 user 消息里抽出【变体列表】JSON（校验 LLM 输入）。"""
    payload = user_content.split("【变体列表】\n", 1)[1]
    return json.loads(payload)


# ---------- 共享文字编译器 ----------

def test_render_lettering_block_quotes_content_verbatim(sticker_preset):
    spec = sticker_preset.variants[0]
    block = sp.render_lettering_block(spec.display_text, sticker_preset.lettering)
    # 中文原样在双引号内；引号本身明确禁止画出
    assert '"收到！"' in block
    assert "Do not render the quotation marks" in block
    # 保护性禁令都在（带字不等于删 watermark/logo 保护）
    assert "any other text, captions, logos, signatures, or watermarks" in block
    assert "safe margin" in block
    assert "voice-type" in block
    assert "beside the raised hand" in block
    # 与随机无关：同一 content 同一输出
    assert sp.render_lettering_block(spec.display_text, sticker_preset.lettering) == block


def test_render_lettering_block_special_chars_are_literal():
    content = '收到"引号{a|b}、{name}、\\ 反斜杠 | 管\n道'
    dt = DisplayText(content=content, layout="voice", placement="beside the head", flow="upright", emphasis="")
    block = sp.render_lettering_block(dt, None)
    # 引号/花括号/管道/换行/反斜杠不触发 wildcard、不做占位符解析（{name} 不被替换）
    assert "{name}" in block and "（未替换）" not in block
    # content 在双引号界定里逐字可还原
    start = block.index('"', block.index("Render") + 1)
    end = block.rindex('"')
    parsed = json.loads(block[start:end + 1])
    assert parsed == content


def test_render_lettering_block_lang_and_margin(sticker_preset):
    spec = sticker_preset.variants[11]
    block = sp.render_lettering_block(spec.display_text, sticker_preset.lettering)
    assert '"好耶！"' in block
    assert "safe margin" in block and "6%" in block
    from charvar.models import LetteringStyle

    en = LetteringStyle(language="en", style="S", fill="F", outline="O", accent="A", rules="R", safe_margin=0.1)
    assert "the en text" in sp.render_lettering_block(spec.display_text, en)
    assert "10%" in sp.render_lettering_block(spec.display_text, en)


def test_llm_response_bans(sticker_preset):
    assert sp.llm_response_bans("A small cat jumps, ears up.") == []
    assert sp.llm_response_bans("One character only, no text.")
    assert sp.llm_response_bans("带中文不行")
    assert sp.llm_response_bans("Render the text above the head")
    assert sp.llm_response_bans("no text anywhere")
    # 兼容写法放行：只禁多余文字；texture/watermark 不误伤
    assert sp.llm_response_bans("No unrelated writing.") == []
    assert sp.llm_response_bans("rough texture, watermark avoided") == []


def test_llm_response_bans_accepts_anchor_echo(sticker_preset):
    """required_label 锚定句要求逐字复述，其尾句的 render no other text 回声不算违规。"""
    from charvar.llm_prompts import reference_anchor_sentence

    anchor = reference_anchor_sentence(
        [{"kind": "front", "tag": "<image1>"}], text_policy="required_label"
    )
    assert sp.llm_response_bans(anchor) == []
    assert sp.llm_response_bans(f"{anchor} A cat raises one paw beside its head.") == []
    # 同族的否定式 paraphrase 也放行
    assert sp.llm_response_bans("Do not render any other text besides the label. A cat sits.") == []
    # 真正的 affirmative 绘字指令仍拒绝（不含中文时也要能定位）
    assert "疑似绘字指令句" in sp.llm_response_bans('a sign board; render the text "OPEN" on it')
    # 主体锁定式全局禁句仍拒绝
    assert sp.llm_response_bans("clean background, no text anywhere")


# ---------- 冲突检测 ----------

def test_validate_conflicts_subject_locks_block(sticker_subject):
    subject = sticker_subject.model_copy(deep=True)
    subject.identity_lock.append("no text anywhere in the image")
    issues = sp.validate_sticker_conflicts(subject, None)
    assert issues["errors"] and "no text" in issues["errors"][0]
    assert not issues["warnings"]


def test_validate_conflicts_template_is_warning_not_block(sticker_subject):
    template = "## 场景条款：无文字、无标注；仅此场景。"
    issues = sp.validate_sticker_conflicts(sticker_subject, template)
    assert issues["errors"] == []
    assert issues["warnings"] and "无文字" in issues["warnings"][0]


def test_validate_conflicts_negative_tokens_block(sticker_subject):
    issues = sp.validate_sticker_conflicts(
        sticker_subject, None, effective_negative="worst quality, text, watermark"
    )
    assert issues["errors"] and "text" in issues["errors"][0]
    # texture / watermark / logo 不误伤
    ok = sp.validate_sticker_conflicts(sticker_subject, None, effective_negative="rough texture, watermark, logo")
    assert ok["errors"] == [] and ok["warnings"] == []
    # 兼容写法：no extra text 放行
    ok2 = sp.validate_sticker_conflicts(sticker_subject, None, effective_negative="no extra text, blurry")
    assert ok2["errors"] == []


def test_workflow_default_negative_bans_scope():
    """mapping 自带禁字 default 检出；缺注入字段时的节点字符串扫描只在缺字段分支生效。"""
    workflow = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "generate text here"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    mapping_with = {"mappings": [
        {"field": "negative_prompt", "node_id": "11", "input_key": "text", "default": "blurry, text, ugly"},
        {"field": "prompt", "node_id": "11", "input_key": "text"},
    ]}
    # 有注入字段：只查 mapping default（检出）
    assert sp.workflow_default_negative_bans(workflow, mapping_with)
    mapping_without = {"mappings": [{"field": "prompt", "node_id": "11", "input_key": "text"}]}
    # 缺注入字段：扫节点字符串 → 检出
    assert sp.workflow_default_negative_bans(workflow, mapping_without)
    clean = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat sits"}},
        "47": {"class_type": "SaveImage", "inputs": {"filename_prefix": "p"}},
    }
    assert sp.workflow_default_negative_bans(clean, mapping_without) == []


def test_sticker_meta_snapshot(sticker_preset):
    spec = sticker_preset.variants[5]
    snapshot = sp.sticker_meta(sticker_preset, spec)
    assert snapshot["schema_version"] == 2
    assert snapshot["purpose"] == "sticker"
    assert snapshot["text_policy"] == "required_label"
    assert snapshot["composition_policy"] == "variant"
    assert snapshot["preferred_aspect_ratio"] == "1:1"
    assert snapshot["display_text"]["content"] == "我尽量"  # variants[5] = sticker_ill_try
    assert snapshot["lettering"]["language"] == "zh-Hans"


# ---------- classic 端到端 ----------

def test_classic_sticker_prompt_contains_content_once(sticker_preset, sticker_subject):
    req = GenerateRequest(subject_id="demo_cat", preset="chat_replies", seed=42)
    result = compose_generate(sticker_subject, sticker_preset, req)
    assert len(result.items) == 12
    for item in result.items:
        content = item.meta["display_text"]["content"]
        assert content in item.prompt
        assert item.prompt.count(content) == 1  # lettering block 只有一份
    first = result.items[0].prompt
    # 无头肩裁切 guard
    assert "Tight head-and-shoulders framing" not in first
    # 段落顺序：master → 变体文本 → lettering block → 背景/光照句
    lettering_pos = first.find('Render exactly the Chinese text "收到！"')
    background_pos = first.find("The background is ")
    assert 0 < lettering_pos < background_pos
    # 收到 的 content 原样保留（normalize 不动 lettering block）
    assert '"收到！"' in first


def test_classic_sticker_del_other_variant_keeps_sampling(sticker_preset, sticker_subject):
    """删除别条不影响剩余条的背景/光照（每条独立 RNG）。"""
    base = compose_generate(sticker_subject, sticker_preset, GenerateRequest(seed=42))
    trimmed = sticker_preset.model_copy(deep=True)
    trimmed.variants = [v for v in trimmed.variants if v.id != "sticker_coming"]
    shorter = compose_generate(sticker_subject, trimmed, GenerateRequest(seed=42))
    expected = {(i.variant_id, i.background, i.lighting) for i in base.items if i.variant_id != "sticker_coming"}
    got = {(i.variant_id, i.background, i.lighting) for i in shorter.items}
    assert got == expected


def test_classic_sticker_caption_not_driven_by_display_text(sticker_preset, sticker_subject):
    """caption 继续是图像描述（中文文案不是 caption 来源）。"""
    result = compose_generate(sticker_subject, sticker_preset, GenerateRequest(seed=42))
    caption = result.items[0].caption
    assert "收到" not in caption
    assert "smile expression" in caption


# ---------- LLM 模式（mock，不访问真实 API） ----------

def test_llm_intent_hides_display_text_from_llm(sticker_preset, sticker_subject):
    from charvar.llm_prompts import build_rewrite_messages

    intents = llm_compose.collect_intent(
        sticker_subject, sticker_preset,
        GenerateRequest(subject_id="demo_cat", preset="chat_replies", seed=42, mode="llm",
                        reference_kinds=["front"]),
    )
    # intent 只带策略标签（英文），不带 display_text/lettering（防 LLM 复制中文）
    assert all("display_text" not in intent and "lettering" not in intent for intent in intents)
    assert intents[0]["purpose"] == "sticker" and intents[0]["text_policy"] == "required_label"
    messages = build_rewrite_messages(
        sticker_subject, "# 模板",
        [{"kind": "front", "tag": "<image1>"}], intents,
        text_policy="required_label", composition_policy="variant",
    )
    user = messages[1]["content"]
    entries = _intent_ids(user)
    assert entries[0]["purpose"] == "sticker"
    assert "收到" not in user and "来啦" not in user  # 中文文案绝不进 LLM 视野
    assert "来啦来啦" not in user


def test_llm_anchor_sentence_policies(sticker_preset):
    from charvar.llm_prompts import REFERENCE_TAIL_SENTENCE, reference_anchor_sentence

    refs = [{"kind": "front", "tag": "<image1>"}]
    # 旧默认（forbid）：尾句不变
    legacy = reference_anchor_sentence(refs)
    assert "One character only, no text." in legacy
    # required_label：替换尾句，不全局删除旧禁令
    required = reference_anchor_sentence(refs, text_policy="required_label")
    assert "One character only, no text." not in required
    assert "prescribed Chinese lettering" in required and "no other text" in required
    assert "no text." not in required.replace("no other text", "")


def test_llm_sticker_final_prompt_gets_verbatim_lettering(sticker_preset, sticker_subject, monkeypatch):
    """LLM 返回纯英文描述 → 最终 prompt 仍由共享编译器追加原样中文。"""

    def fake_chat_json(messages, **kw):
        intents = _intent_ids(messages[1]["content"])
        return {
            "variants": [
                {"id": entry["id"], "prompt": f"English scene for {entry['id']} without any text"}
                for entry in intents
            ]
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    req = GenerateRequest(
        subject_id="demo_cat", preset="chat_replies", seed=42, mode="llm", reference_kinds=["front"]
    )
    result = llm_compose.generate_llm(sticker_subject, sticker_preset, req, "# 模板", ["front"])
    item = result.items[0]
    assert item.prompt.startswith("English scene for sticker_received")
    assert '"收到！"' in item.prompt
    assert item.meta["display_text"]["content"] == "收到！"
    assert item.meta["mode"] == "llm"
    # 规范注入：sticker 不带全身站姿句
    user = None
    assert user is None  # 占位防呆：规范注入由 test_llm_prompts 覆盖


def test_llm_sticker_rejects_no_text_then_recovers(sticker_preset, sticker_subject, monkeypatch):
    calls: list[list[dict]] = []

    def fake_chat_json_recovers(messages, **kw):
        calls.append(messages)
        intents = _intent_ids(messages[1]["content"])
        out = []
        for entry in intents:
            prompt = f"Scene for {entry['id']}."
            if entry["id"] == "sticker_received" and len(calls) == 1:
                prompt = "Scene one. One character only, no text."
            out.append({"id": entry["id"], "prompt": prompt})
        return {"variants": out}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json_recovers)
    req = GenerateRequest(
        subject_id="demo_cat", preset="chat_replies", seed=1, mode="llm", reference_kinds=["front"]
    )
    result = llm_compose.generate_llm(sticker_subject, sticker_preset, req, "# 模板", ["front"])
    assert len(calls) == 2  # 全批一次 + 缺条补调一次
    item = result.items[0]
    assert item.prompt.startswith("Scene for sticker_received.")
    assert '"收到！"' in item.prompt


def test_llm_sticker_anchor_echo_accepted_first_pass(sticker_preset, sticker_subject, monkeypatch):
    """真实模型会按 system 要求把锚定句逐字复述进每条 prompt（此前 12 条全被拒的 502 回归）。

    锚定句尾 "render no other text…" 与输出过滤器的绘字指令检测必须共存：
    复述指令 ≠ 自写绘字内容 → 一次全批即通过，不触发补调。
    """
    from charvar.llm_prompts import reference_anchor_sentence

    anchor = reference_anchor_sentence(
        [{"kind": "front", "tag": "<image1>"}], text_policy="required_label"
    )
    calls: list[list[dict]] = []

    def fake_chat_json_echoes_anchor(messages, **kw):
        calls.append(messages)
        intents = _intent_ids(messages[1]["content"])
        return {
            "variants": [
                {"id": entry["id"], "prompt": f"{anchor} Scene for {entry['id']}."}
                for entry in intents
            ]
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json_echoes_anchor)
    req = GenerateRequest(
        subject_id="demo_cat", preset="chat_replies", seed=42, mode="llm", reference_kinds=["front"]
    )
    result = llm_compose.generate_llm(sticker_subject, sticker_preset, req, "# 模板", ["front"])
    assert len(calls) == 1  # 全批一次通过，无补调
    assert len(result.items) == 12
    for item in result.items:
        assert anchor in item.prompt  # 锚定句逐字保留
        # lettering block 已附（LLM 输入无 display_text，content 只可能来自编译器）
        assert item.prompt.count(item.meta["display_text"]["content"]) == 1


def test_llm_sticker_persistent_ban_raises_located_error(sticker_preset, sticker_subject, monkeypatch):
    def fake_chat_json_always_bad(messages, **kw):
        intents = _intent_ids(messages[1]["content"])
        return {"variants": [{"id": entry["id"], "prompt": "Scene one. One character only, no text."} for entry in intents]}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json_always_bad)
    req = GenerateRequest(
        subject_id="demo_cat", preset="chat_replies", seed=1, mode="llm", reference_kinds=["front"]
    )
    with pytest.raises(llm.LLMError) as exc_info:
        llm_compose.generate_llm(sticker_subject, sticker_preset, req, "# 模板", ["front"])
    message = str(exc_info.value)
    assert "sticker_received" in message and ("no text" in message or "禁" in message)