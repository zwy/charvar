"""预设加载与结构校验。

第三方工作流 JSON（characterDataset_q21*.json）已移出仓库；「30 条文本逐字不改动」
的红线改用 sha256 基准锁定，不在仓库保留第三方文件或全文副本。基准配方：
    sha256(("\n".join(v.text for v in preset.variants)).encode("utf-8")).hexdigest()
基准值取自当时与原工作流 JSON 逐字比对全绿的版本；如确需修改预设，请显式重算并
更新基准（并说明理由）。
"""
from __future__ import annotations

import hashlib

import pytest

from charvar.preset_loader import ListPreset, MatrixPreset, PresetError, load, load_file

# 文本基准 sha256（= 与原工作流 JSON 逐字比对通过版本的 preset 文本）
Q21_DEFAULT_30_DIGEST = "847afccf764b43e55af82d8c80d2e5fc816aa82eaf3500f6852d18a8b599ae13"
Q21_LEGACY_30_DIGEST = "aada7b57aee5e27438db8bbb45e87749ac09000415af506a6249a6ae0b83ad9a"


def _texts_digest(preset_name: str) -> str:
    preset = load(preset_name)
    return hashlib.sha256("\n".join(v.text for v in preset.variants).encode("utf-8")).hexdigest()


def test_q21_default_30_digest_locked():
    """红线：q21_default_30 的 30 条 variants 文本逐字不变（基准不再依赖第三方 JSON）。"""
    assert len(load("q21_default_30").variants) == 30
    assert _texts_digest("q21_default_30") == Q21_DEFAULT_30_DIGEST, (
        "q21_default_30 的变体文本偏离原工作流基准；如确需修改，请显式重算并更新基准"
    )


def test_q21_legacy_30_digest_locked():
    """legacy 30 条（原工作流子图残留旧版）同样锁定。"""
    preset = load("q21_legacy_30")
    assert len(preset.variants) == 30
    assert _texts_digest("q21_legacy_30") == Q21_LEGACY_30_DIGEST
    assert any("smooth youthful skin" in v.text for v in preset.variants)


def test_q21_default_structured_fields():
    preset = load("q21_default_30")
    by_id = {v.id: v for v in preset.variants}
    assert by_id["full_l45"].framing == "full"
    assert by_id["full_l45"].angle == "l45"
    assert by_id["chest_back"].framing == "chest"
    assert by_id["chest_back"].angle == "back"
    assert by_id["head_front"].framing == "head"
    assert by_id["head_front"].angle == "front"
    assert by_id["expr_smile"].expression == "smile"
    assert by_id["expr_smile"].framing == "head"
    assert by_id["expr_smile"].angle == "front"
    assert {v.id for v in preset.variants} == {
        *(f"{fr}_{ang}" for fr in ("full", "chest", "head") for ang in
          ("front", "l45", "r45", "l90", "r90", "l135", "r135", "back")),
        *(f"expr_{e}" for e in ("neutral", "smile", "sad", "pout", "cringe", "silly")),
    }


def test_wildcard_files_loaded():
    preset = load("q21_default_30")
    assert preset.backgrounds == ["gray", "gradient blue white", "red", "green", "white", "black", "yellow"]
    assert preset.lighting[0] == "soft natural window light, diffused and even"
    assert len(preset.lighting) == 5


def test_matrix_basic_expands_30():
    preset = load("matrix_basic")
    assert isinstance(preset, MatrixPreset)
    from charvar.composer import expand_matrix

    specs = expand_matrix(preset)
    assert len(specs) == 30
    ids = [s.id for s in specs]
    assert len(set(ids)) == 30
    assert ids[0] == "full_front"
    assert ids[23] == "head_back"
    assert ids[24] == "head_front_neutral"
    assert ids[29] == "head_front_silly"
    # 顺序稳定
    assert [s.id for s in expand_matrix(preset)] == ids


# ---------- q21_v2_30（新版 Q21 数据集构建工作流提取） ----------

V2_IDS = {
    *(f"{fr}_{ang}" for fr in ("full", "chest") for ang in
      ("front", "l45", "r45", "l90", "r90")),
    "full_l135", "full_r135", "full_back",
    "chest_high", "chest_low", "chest_back",
    *(f"head_{ang}" for ang in ("front", "l45", "r45", "l90", "r90", "back")),
    *(f"expr_{e}" for e in ("shocked", "laugh", "neutral", "smile", "sad", "pout", "cringe", "silly")),
}

Q21_V2_30_DIGEST = "11f45f6a8f4e7b123005d6c82d9b50ac1fb44b2837d9c424a9d57e5c7e2f9ec2"


def test_q21_v2_30_digest_locked():
    preset = load("q21_v2_30")
    assert isinstance(preset, ListPreset)
    assert len(preset.variants) == 30
    assert _texts_digest("q21_v2_30") == Q21_V2_30_DIGEST, (
        "q21_v2_30 的变体文本偏离新版工作流基准；如确需修改，请显式重算并更新基准"
    )
    assert {v.id for v in preset.variants} == V2_IDS


def test_q21_v2_30_structured_fields_and_caption_settings():
    preset = load("q21_v2_30")
    by_id = {v.id: v for v in preset.variants}
    assert by_id["chest_high"].framing == "chest"
    assert by_id["chest_high"].angle == "high"
    assert by_id["chest_low"].angle == "low"
    assert by_id["head_back"].framing == "head"
    assert by_id["head_back"].angle == "back"
    assert by_id["expr_shocked"].expression == "shocked"
    assert by_id["expr_laugh"].expression == "laugh"
    assert by_id["expr_smile"].framing == "head"
    assert by_id["expr_smile"].angle == "front"
    assert "chest_l135" not in by_id  # 新版 chest 组没有背侧三四机位

    # loader 必须保留 caption_tags / caption_background_suffix（此前 caption_tags 被静默丢弃）
    assert preset.caption_background_suffix is False
    assert preset.caption_tags["angle"]["high"] == "high-angle view"
    assert preset.caption_tags["angle"]["low"] == "low-angle view"
    assert preset.caption_tags["expression"]["shocked"] == "shocked expression"
    assert preset.caption_tags["expression"]["laugh"] == "laughing"

    # 参考图顺序声明：<image1>=headshot … <image4>=behind（推送 ComfyUI 时按此勾选/上传）
    assert preset.reference_order == ["headshot", "front", "side", "behind"]


def test_q21_v2_30_wildcards():
    preset = load("q21_v2_30")
    assert len(preset.backgrounds) == 20
    assert preset.backgrounds[0] == "a seamless light gray studio backdrop"
    assert "a sandy beach, with calm blue ocean and a clear sky" in preset.backgrounds
    assert len(preset.lighting) == 5
    assert preset.lighting[0] == "soft natural window light, diffused and even"
    assert (
        preset.lighting[3]
        == "a dramatic single-source key light with strong contrast, with the face and body clearly lit"
    )


def test_q21_v2_30_prompt_and_caption():
    from charvar import composer
    from charvar.caption import build_caption
    from charvar.models import GenerateRequest, SubjectProfile

    subject = SubjectProfile(id="demo", name="演示", subject_noun="woman", trigger_word="sw1tch")
    subject.style_lock = ["the color palette"]
    preset = load("q21_v2_30")

    spec = preset.variants[0]
    caption = build_caption(
        subject, spec, "a seamless light gray studio backdrop",
        "soft natural window light, diffused and even",
        preset.caption_tags, background_suffix=preset.caption_background_suffix,
    )
    assert caption.endswith("a seamless light gray studio backdrop, soft natural window light")
    assert "backdrop background" not in caption

    req = GenerateRequest(
        subject_id="demo", preset="q21_v2_30", seed=42,
        randomize_background=True, randomize_lighting=True,
    )
    result = composer.generate(subject, preset, req)
    item = result.items[0]
    # 原工作流感残留在提取时即剥除；最终提示词为背景/光照完整句
    assert "[BACKGROUND]" not in item.prompt and "[BODY_NOTES]" not in item.prompt
    assert "The background is " in item.prompt
    assert "The lighting is " in item.prompt
    assert " backdrop background" not in item.caption

    # 只开背景随机 → 光照句省略（_render_suffix 逐句省略逻辑对新句式同样成立）
    req2 = GenerateRequest(subject_id="demo", preset="q21_v2_30", seed=9, randomize_lighting=False)
    item2 = composer.generate(subject, preset, req2).items[0]
    assert item2.background
    assert item2.lighting is None
    assert item2.prompt.endswith("no text.") or "The lighting is" not in item2.prompt

    # 背景光照都关 → suffix 整句省略
    req3 = GenerateRequest(
        subject_id="demo", preset="q21_v2_30", seed=9,
        randomize_background=False, randomize_lighting=False,
    )
    item3 = composer.generate(subject, preset, req3).items[0]
    assert item3.prompt.endswith("no text.")


# ---------- expressions_v1_24（纯表情预设 V1：24 种，独立于 q21 场景预设） ----------

EXPRESSIONS_V1_24_DIGEST = "15ea68460a071c9cdde27214aafed37dd304e958edc1e8d1972c95f47496fdd5"

EXPRESSION_IDS = (
    "neutral", "soft_smile", "toothy_smile", "laugh", "sad", "tearful", "crying", "angry",
    "furious", "surprised", "afraid", "disgusted", "confused", "skeptical", "aggrieved",
    "annoyed", "unimpressed", "smug", "embarrassed", "sleepy", "pout", "puffed_cheeks",
    "wink", "tongue_out",
)


def test_expressions_v1_24_digest_locked():
    """红线：expressions_v1_24 的 24 条 variants 文本逐字不变（V1 基准 sha256）。"""
    preset = load("expressions_v1_24")
    assert isinstance(preset, ListPreset)
    assert len(preset.variants) == 24
    assert {v.id for v in preset.variants} == {f"expr_{e}" for e in EXPRESSION_IDS}
    assert _texts_digest("expressions_v1_24") == EXPRESSIONS_V1_24_DIGEST, (
        "expressions_v1_24 的变体文本偏离 V1 基准；如确需修改，请显式重算并更新基准"
    )


def test_expressions_v1_24_structure_and_caption():
    from charvar import composer
    from charvar.caption import build_caption
    from charvar.models import GenerateRequest, SubjectProfile

    preset = load("expressions_v1_24")
    # 全部为头肩正面；expression 字段与 caption 标签一一对应
    for spec in preset.variants:
        assert spec.framing == "head"
        assert spec.angle == "front"
        assert spec.expression == spec.id[len("expr_"):]
    # 单项背景/光照池（固定值），caption 不加 " background" 后缀
    assert preset.caption_background_suffix is False
    assert len(preset.backgrounds) == 1
    assert len(preset.lighting) == 1
    assert "headshot" in load("expressions_v1_24").reference_order
    assert set(preset.caption_tags["expression"]) == set(EXPRESSION_IDS)

    subject = SubjectProfile(id="demo", name="演示", subject_noun="woman", reference_tag="<image1>")
    subject.style_lock = ["2D hand-drawn illustration"]
    spec = preset.variants[0]
    caption = build_caption(
        subject, spec, preset.backgrounds[0], preset.lighting[0],
        preset.caption_tags, background_suffix=preset.caption_background_suffix,
    )
    assert "neutral expression" in caption
    assert "a seamless neutral light gray studio backdrop, plain and uncluttered" in caption
    assert "backdrop background" not in caption

    req = GenerateRequest(subject_id="demo", preset="expressions_v1_24", seed=42,
                          randomize_background=True, randomize_lighting=True)
    result = composer.generate(subject, preset, req)
    assert len(result.items) == 24
    item = result.items[0]
    # master（含参考标签与表情覆盖声明）+ 变体文本 + 背景/光照句
    assert "<image1>" in item.prompt
    assert "Create one straight-on head-and-shoulders portrait" in item.prompt
    assert "A calm neutral expression." in item.prompt
    assert "The background is " in item.prompt
    assert "The lighting is " in item.prompt


# ---------- chat_replies_v1_12（带字聊天表情包：schema 2，仅新预设启用） ----------

CHAT_REPLIES_V1_12_DIGEST = "39a394efd7c0bb85b9f2718fe98e06ac7bda1fd68384e26de771211f9bba188f"

CHAT_REPLIES_CONTENTS = (
    "收到！", "来啦来啦", "啊？", "让我看看", "包的", "我尽量",
    "不想动", "我先躺会", "你最好是", "求夸夸", "抱一下", "好耶！",
)

CHAT_REPLIES_IDS = (
    "sticker_received", "sticker_coming", "sticker_huh", "sticker_let_me_see",
    "sticker_count_on_me", "sticker_ill_try", "sticker_dont_wanna_move",
    "sticker_lie_down", "sticker_youd_better", "sticker_praise_me",
    "sticker_hug", "sticker_yay",
)


def test_chat_replies_v1_12_digest_locked():
    preset = load("chat_replies_v1_12")
    assert isinstance(preset, ListPreset)
    assert len(preset.variants) == 12
    assert {v.id for v in preset.variants} == set(CHAT_REPLIES_IDS)
    assert _texts_digest("chat_replies_v1_12") == CHAT_REPLIES_V1_12_DIGEST, (
        "chat_replies_v1_12 的变体文本偏离 V1 基准；如确需修改，请显式重算并更新基准"
    )


def test_chat_replies_v1_12_contents_exact():
    preset = load("chat_replies_v1_12")
    contents = [v.display_text.content for v in preset.variants]
    assert tuple(contents) == CHAT_REPLIES_CONTENTS
    layouts = {v.id: v.display_text.layout for v in preset.variants}
    assert layouts["sticker_received"] == "voice"
    assert layouts["sticker_dont_wanna_move"] == "state"
    assert layouts["sticker_hug"] == "interaction"


def test_chat_replies_v1_12_sticker_contract():
    from charvar.preset_loader import MatrixPreset

    preset = load("chat_replies_v1_12")
    assert preset.schema_version == 2 and preset.purpose == "sticker"
    assert preset.text_policy == "required_label"
    assert preset.composition_policy == "variant"
    assert preset.preferred_aspect_ratio == "1:1"
    assert preset.lettering is not None and preset.lettering.language == "zh-Hans"
    assert preset.reference_order == ["front"]
    for spec in preset.variants:
        assert spec.framing is None and spec.angle is None  # 构图以 text 为准
    # caption 仍是图像描述；caption_background_suffix=false
    assert preset.caption_background_suffix is False


def test_chat_replies_loader_rejects_bad_configs(tmp_path):
    """未知 schema、未知根字段、sticker 合同缺失都报可读错误（明确配置错误而非“找不到预设”）。"""
    import yaml as _yaml

    lettering = {"language": "zh-Hans", "style": "s", "fill": "f", "outline": "o",
                 "accent": "a", "rules": "r", "safe_margin": 0.06}

    def _base(**extra):
        base = {
            "name": "bad", "type": "list", "version": 1,
            "master_template": "m", "suffix_template": "s",
            "backgrounds": ["b"], "lighting": ["l"],
            "purpose": "sticker", "text_policy": "required_label",
            "composition_policy": "variant", "preferred_aspect_ratio": "1:1",
            "schema_version": 2, "lettering": lettering,
            "variants": [_variant()],
        }
        base.update(extra)
        return base

    def _variant(display=None, framing=None):
        v = {
            "id": "sticker_a", "text": "Pose.", "tags": [],
            "framing": framing, "angle": None, "expression": "smile",
            "display_text": display or {"content": "收到！", "layout": "voice",
                                        "placement": "beside", "flow": "upright", "emphasis": "！"},
        }
        return {k: val for k, val in v.items() if val is not None or k in ("framing", "angle")}

    def _expect_error(data: dict, needle: str):
        p = tmp_path / "bad.yaml"
        p.write_text(_yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(PresetError) as e:
            load_file(p)
        assert needle in str(e.value)

    _expect_error({**_base(), "schema_version": 3}, "schema_version")   # 未知版本显式报错
    _expect_error({**_base(), "master_templates": "typo"}, "master_templates")  # 未知根字段
    # 变体级未知字段（拼写错误）同样拒绝，不静默丢弃
    _expect_error({**_base(), "variants": [{"id": "a", "text": "t", "framnig": None}]}, "framnig")
    _expect_error(_base(variants=[_variant(framing="head")]), "framing")  # 合同：framing 必须为空
    _expect_error(_base(lettering=None), "lettering")                     # 合同：需要 lettering
    _expect_error(
        _base(variants=[{"id": "a", "text": "t"}]),  # 合同：每条必须有 display_text
        "display_text",
    )
    # display_text：content 空白 / 未知 layout → 校验失败
    _expect_error(_base(variants=[_variant({"content": "  ", "layout": "voice",
                                            "placement": "b", "flow": "f", "emphasis": ""})]),
                  "content")
    _expect_error(_base(variants=[_variant({"content": "收到了", "layout": "banner",
                                            "placement": "b", "flow": "f", "emphasis": ""})]),
                  "layout")
    # schema 1 文件携带 schema 2 字段 → 要求显式升级
    _expect_error({k: val for k, val in _base().items()
                   if k not in ("purpose", "text_policy", "composition_policy",
                                "preferred_aspect_ratio", "schema_version")},
                  "schema_version")
