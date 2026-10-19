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
        subject, "# 模板", [{"kind": "headshot", "tag": "<image1>"}], intents
    )
    system = msgs[0]["content"]
    # 身份以参考图为准（不复述整段角色设定）；锚定句全批一致；背面省略表情
    assert "身份细节交给参考图" in system
    assert "逐字相同" in system
    assert "angle 为 back 时省略表情句" in system
    assert "framing_guard" in system
    assert "<image1>" in system