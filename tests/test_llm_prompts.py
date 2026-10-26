"""llm_prompts 消息构造：视角/构图/镜像规范必须注入起草与改写上下文。"""
from charvar.llm_prompts import (
    ANGLE_VIEW_SENTENCES,
    FULL_BODY_CAMERA_SENTENCE,
    build_rewrite_messages,
    draft_subject_image_prompt_messages,
)
from charvar.models import SubjectProfile


def _subject() -> SubjectProfile:
    return SubjectProfile(id="a_b", name="测试", subject_noun="person")


def test_draft_character_sheet_carries_layout_rules():
    msgs = draft_subject_image_prompt_messages(_subject(), "character_sheet", "# 模板")
    text = msgs[0]["content"] + msgs[1]["content"]
    # 半身像＋三视图的全部布局条款都在上下文里
    assert "头顶与鞋底分别对齐" in text
    assert "腰部裁切" in text
    assert "90 度侧面" in text and "不回头" in text
    assert "不添加文字、标注、分格线" in text


def test_draft_edit_kinds_reference_image1():
    for kind in ("headshot", "front", "side", "behind"):
        msgs = draft_subject_image_prompt_messages(_subject(), kind, "")
        text = msgs[0]["content"]
        assert "<image1>" in text, kind
        assert "无广角变形" in text, kind
    # 背面：不回头、不把正面细节画到背面
    behind = draft_subject_image_prompt_messages(_subject(), "behind", "")[0]["content"]
    assert "不回头" in behind and "不得画到背面" in behind
    # 侧面：严格 90 度、鼻尖朝向明确
    side = draft_subject_image_prompt_messages(_subject(), "side", "")[0]["content"]
    assert "严格 90 度侧面" in side and "不转头看镜头" in side


def test_rewrite_messages_inject_only_present_angles():
    subject = _subject()
    intents = [
        {"id": "full_l90", "angle": "l90", "framing": "full"},
        {"id": "full_back", "angle": "back", "framing": "full"},
    ]
    msgs = build_rewrite_messages(
        subject, "# 模板", [{"kind": "character_sheet", "tag": "<image1>"}], intents
    )
    user = msgs[1]["content"]
    assert "【视角规范】" in user
    assert ANGLE_VIEW_SENTENCES["l90"] in user
    assert ANGLE_VIEW_SENTENCES["back"] in user
    assert ANGLE_VIEW_SENTENCES["front"] not in user
    # 全身构图句与遮挡/镜像句始终注入
    assert FULL_BODY_CAMERA_SENTENCE in user
    assert "never mirrored" in user


def test_rewrite_system_rules():
    subject = _subject()
    intents = [{"id": "chest_l45", "angle": "l45", "framing": "chest"}]
    msgs = build_rewrite_messages(
        subject, "# 模板", [{"kind": "headshot", "tag": "<image1>"}], intents, language="en"
    )
    system = msgs[0]["content"]
    # 身份以参考图为准（不复述整段角色设定）；锚定句全批一致；背面省略表情
    assert "身份细节交给参考图" in system
    assert "逐字相同" in system
    assert "angle 为 back 时省略表情句" in system
    assert "framing_guard" in system
    assert "参考图分工锚定句" in system

def test_reference_anchor_sentence_per_kind():
    from charvar.llm_prompts import REFERENCE_TAIL_SENTENCE, reference_anchor_sentence

    refs = [
        {"kind": "headshot", "tag": "<image1>"},
        {"kind": "front", "tag": "<image2>"},
        {"kind": "side", "tag": "<image3>"},
        {"kind": "behind", "tag": "<image4>"},
    ]
    s = reference_anchor_sentence(refs)
    # 逐图分工与 q21_v2_30 的映射一致：脸→<image1>、服装→<image2>、朝向→<image3>、背面→<image4>
    assert "Copy the face" in s and "appear in <image1>" in s
    assert "complete outfit, and footwear" in s and "<image2>" in s
    assert "true side profile" in s and "<image3>" in s
    assert "back of the head" in s and "<image4>" in s
    assert REFERENCE_TAIL_SENTENCE in s

    # 单张：只有该类型的分工条款；未提供的 tag 不悬空
    s1 = reference_anchor_sentence([{"kind": "front", "tag": "<image1>"}])
    assert "from <image1>" in s1 and "<image2>" not in s1
    assert reference_anchor_sentence([]) == ""


def test_rewrite_messages_carry_role_anchor():
    from charvar.llm_prompts import reference_anchor_sentence

    refs = [{"kind": "headshot", "tag": "<image1>"}, {"kind": "front", "tag": "<image2>"}]
    msgs = build_rewrite_messages(
        _subject(), "# 模板", refs, [{"id": "full_front", "angle": "front", "framing": "full"}],
        language="en",
    )
    user = msgs[1]["content"]
    assert "【参考图分工锚定句】" in user
    assert reference_anchor_sentence(refs) in user
    system = msgs[0]["content"]
    # 锚定句改为逐字采用生成的分工句（旧例句已移除）
    assert "逐字采用给定的" in system and "参考图分工锚定句" in system


# ---------- policy 分支（旧默认逐字不变；sticker 不套通用构图规范） ----------

def test_rewrite_messages_policy_branches(silver_witch):
    from charvar.llm_prompts import (
        ANGLE_VIEW_SENTENCES,
        FULL_BODY_CAMERA_SENTENCE,
        build_rewrite_messages,
    )

    legacy_intent = {"id": "full_front", "text": "front facing full body", "framing": "full",
                     "angle": "front", "expression": None, "background": "bg",
                     "lighting": "light", "framing_guard": None}
    legacy = build_rewrite_messages(silver_witch, "# 模板", [{"kind": "front", "tag": "<image1>"}],
                                    [legacy_intent], language="en")
    legacy_user = legacy[1]["content"]
    # 旧系统词与规范注入逐字保持：全身句 + 视角规范 + 遮挡 + no text 尾句
    assert "One character only, no text." in legacy_user
    assert FULL_BODY_CAMERA_SENTENCE in legacy_user
    assert ANGLE_VIEW_SENTENCES["front"] in legacy_user
    assert "不出现中文" in legacy[0]["content"]

    sticker_intent = {"id": "sticker_received", "text": "raises one forepaw beside the head",
                      "framing": None, "angle": None, "expression": "smile",
                      "background": "white", "lighting": "soft", "framing_guard": None,
                      "purpose": "sticker", "text_policy": "required_label",
                      "composition_policy": "variant"}
    sticker = build_rewrite_messages(silver_witch, "# 模板",
                                     [{"kind": "front", "tag": "<image1>"}], [sticker_intent],
                                     text_policy="required_label", composition_policy="variant",
                                     language="en")
    sticker_user = sticker[1]["content"]
    # variant 构图：不注入全身句与角度规范；保留身份一致性（遮挡/镜像）条款
    assert FULL_BODY_CAMERA_SENTENCE not in sticker_user
    assert ANGLE_VIEW_SENTENCES["front"] not in sticker_user
    assert "asymmetric details" in sticker_user or OCCLUSION_SENTENCE in sticker_user
    # 锚定句换成 required_label 版；中文由程序附加的规则在 system 里
    assert "prescribed Chinese lettering" in sticker_user
    assert "One character only, no text." not in sticker_user
    assert "不要输出任何中文字符" in sticker[0]["content"]
    assert "no text / no writing" in sticker[0]["content"]


# ---------- body_lock（可选体形锁定）：追加进锚定块 ----------

def _subject_with_body_lock() -> SubjectProfile:
    return SubjectProfile(
        id="a_b", name="测试", subject_noun="woman",
        body_lock=["a very large, full, round bust reading clearly wider than her shoulders"],
    )


def test_body_lock_clauses_empty_by_default():
    from charvar.llm_prompts import body_lock_clauses

    assert body_lock_clauses(None, [{"kind": "front", "tag": "<image1>"}]) == []
    assert body_lock_clauses([], [{"kind": "front", "tag": "<image1>"}]) == []
    # 有锁定项但没有参考图 → 不注入（无锚定句可挂）
    assert body_lock_clauses(["a very large bust"], []) == []


def test_body_lock_appended_after_role_clauses():
    from charvar.llm_prompts import reference_anchor_sentence

    refs = [{"kind": "front", "tag": "<image1>"}]
    s = reference_anchor_sentence(
        refs, outfit_mode=True, body_lock=["a very large, full, round bust"]
    )
    assert "Copy the body shape, proportions, bust volume and hip width from <image1>" in s
    # body_lock 分句紧跟逐图分工句，tag 用第一张参考图的 tag
    assert "keep the figure exactly as shown in <image1>: a very large, full, round bust" in s
    # 分句顺序：逐图分工在前、body_lock 在后
    assert s.index("from <image1>") < s.index("keep the figure")

    # 多条 body_lock → 多个分句
    s2 = reference_anchor_sentence(
        [{"kind": "front", "tag": "<image1>"}],
        outfit_mode=True,
        body_lock=["a very large bust", "wide hips"],
    )
    assert ": a very large bust" in s2 and ": wide hips" in s2

    # 无 body_lock → 锚定句与旧版逐字相同（存量行为不变）
    s3 = reference_anchor_sentence(refs)
    assert "keep the figure exactly" not in s3


def test_rewrite_messages_carry_body_lock():
    subject = _subject_with_body_lock()
    msgs = build_rewrite_messages(
        subject, "# 模板", [{"kind": "front", "tag": "<image1>"}],
        [{"id": "full_front", "angle": "front", "framing": "full"}], language="en",
    )
    user = msgs[1]["content"]
    # body_lock 编成分句进【参考图分工锚定句】，随现有硬规则逐字采用、全批相同
    assert "【参考图分工锚定句】" in user
    assert "keep the figure exactly as shown in <image1>: a very large, full, round bust" in user
    # 系统提示词不动（冻结红线）：没有新增体形规则句
    assert "体形" not in msgs[0]["content"]
