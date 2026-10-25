"""主体档案预设校验：presets/subjects/*.yaml 全部可加载且结构合法。

主体档案是「可复现提示词」的锁定源：style_lock / identity_lock / outfit_lock
按顺序拼成 "Maintain ..." 句子，extra 收尾。三份新档案分别来自：

- autumn_birch_girl：秋日枫林女孩（imaishi 风格手绘漫画版）
- mustard_cap_girl：黄帽红夹克女孩（二维手绘漫画原分支）
- yoga_teacher：瑜伽老师（自然真实摄影版）

秋日枫林女孩与黄帽红夹克女孩实为同一角色的两个版本，按用户决定拆成两份独立
档案，各自保留自己的模板与锁定项（手套/包肩侧/耳钉等细节互相矛盾，不互相继承）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from charvar import paths
from charvar.composer import subject_mapping
from charvar.models import SubjectProfile

SUBJECT_IDS = ("autumn_birch_girl", "mustard_cap_girl", "yoga_teacher")


def _subject_path(subject_id: str) -> Path:
    return paths.presets_dir() / "subjects" / f"{subject_id}.yaml"


@pytest.mark.parametrize("subject_id", SUBJECT_IDS)
def test_subject_preset_loads(subject_id: str):
    path = _subject_path(subject_id)
    assert path.exists(), f"缺少主体档案预设：{path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    subject = SubjectProfile.model_validate(data)
    assert subject.id == subject_id
    assert subject.name
    assert subject.subject_noun == "woman"
    assert subject.style_lock and subject.identity_lock and subject.outfit_lock
    assert subject.extra


@pytest.mark.parametrize("subject_id", SUBJECT_IDS)
def test_subject_preset_locks_are_clean_sentences(subject_id: str):
    """锁定项 / extra 原样进提示词：只查首尾空白。
    2026-10-22 起锁定项中英文皆可（Qwen-Image / OpenRouter 双语系模型没问题），
    不再强制 ASCII —— 与 AGENTS.md 的导入/校验红线一致。"""
    data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
    subject = SubjectProfile.model_validate(data)
    for group in (subject.style_lock, subject.identity_lock, subject.outfit_lock):
        for item in group:
            assert item == item.strip()


@pytest.mark.parametrize("subject_id", SUBJECT_IDS)
def test_subject_preset_maps_into_prompt_placeholders(subject_id: str):
    """subject_mapping 的每个值都要能填进 {xxx} 占位符，不能留空壳。"""
    data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
    subject = SubjectProfile.model_validate(data)
    mapping = subject_mapping(subject)
    assert mapping["subject_noun"] == "woman"
    assert mapping["reference_tag"] == "<image1>"
    # trigger_word 是**可选**的稳定标识，不承载身份或服装事实——审计会把
    # 「mustardcap」这类夹带身份的写法判为越界，该主体因此留空。
    # 见 docs/主体档案.md「审计会盯的身份细节边界」。留空时只要求不是空壳。
    trigger = mapping["trigger_word"]
    assert trigger is None or (isinstance(trigger, str) and trigger.strip())
    for key in ("style_lock_sentence", "identity_lock_sentence", "outfit_lock_sentence"):
        assert mapping[key].startswith("Maintain ")
        assert mapping[key].endswith(" ")


def test_subject_presets_avoid_multiview_layout_wording():
    """锁定项/extra 不能带「多视角画册」措辞——会诱导模型画成角色卡分栏。

    出图验证：yoga_teacher 档案里的 "character lookbook shot in a studio" 与
    extra 里的 "across every view"，配合误选的 character_sheet 参考图，会让模型
    把参考图的分栏布局照抄成多个同一人（19:06 跑批 14/14 张重复人物）。
    "across every view" 这类措辞对「换姿势不换人」没有帮助，只增加歧义。
    """
    banned = ("lookbook", "across every view", "character sheet", "multi-view", "turnaround")
    for subject_id in SUBJECT_IDS:
        data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
        subject = SubjectProfile.model_validate(data)
        blobs = list(subject.style_lock) + list(subject.identity_lock) + list(subject.outfit_lock)
        blobs.append(subject.extra)
        for blob in blobs:
            low = blob.lower()
            for word in banned:
                assert word not in low, f"{subject_id} 的锁定项含多视角措辞「{word}」：{blob}"


def test_subject_presets_do_not_lock_background_or_lighting():
    """锁定项不写背景/布光——那是预设 suffix_template 的活，写两遍会自相矛盾。

    背景：yoga_teacher 档案的 style_lock 里写过 "neutral seamless light grey studio
    backdrop, soft large key light from the front left and gentle fill light from the
    right"，而预设 suffix 会写 "The background is {background}. The lighting is
    {lighting}."。两处背景/布光同时出现，模型收到互相冲突的指令（实测出图
    背景与要求不符）。档案的 style_lock 只管人物与画风。
    """
    backdrop_words = ("backdrop", "background", "lighting", "key light", "fill light", "studio")
    for subject_id in SUBJECT_IDS:
        data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
        subject = SubjectProfile.model_validate(data)
        for blob in list(subject.style_lock) + list(subject.identity_lock) + list(subject.outfit_lock):
            low = blob.lower()
            for word in backdrop_words:
                assert word not in low, f"{subject_id} 的锁定项不该写背景/布光「{word}」：{blob}"


def test_subject_presets_stay_lean():
    """档案要瘦：锁定项越长，动作文本在最终提示词里占比越低。

    classic 模式把 master（档案锁定项）与动作文本拼成一条提示词，动作只有
    1/3 篇幅时模型会优先服从身份描述、弱化动作细节。yoga_teacher 精简前
    2839 字符 → 动作占比 29%；精简后 1611 → 40%。这里给一个宽松上限防回退。
    """
    from charvar.composer import lock_sentence

    for subject_id in SUBJECT_IDS:
        data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
        subject = SubjectProfile.model_validate(data)
        total = sum(
            len(lock_sentence(getattr(subject, group) or []))
            for group in ("style_lock", "identity_lock", "outfit_lock")
        ) + len(subject.extra)
        assert total <= 1900, f"{subject_id} 的锁定项过长（{total} 字符），会稀释动作文本"


def test_v2_wildcards_extended_with_subject_scenes():
    """V2 通配符池追加了三个新主体的场景背景/光照，原有条目不变。"""
    from charvar.preset_loader import load

    preset = load("q21_v2_30")
    assert len(preset.backgrounds) == 24
    assert len(preset.lighting) == 7
    # 原 20 条背景与 5 条光照逐字保留
    assert preset.backgrounds[0] == "a seamless light gray studio backdrop"
    assert preset.backgrounds[19] == "a snowy mountain meadow under a bright blue sky"
    assert preset.lighting[4] == "bright high-key lighting with minimal shadows, slightly overexposed"
    # 新增条目：与新主体档案的场景模块对应
    assert "a leaf-covered autumn birch forest, with white birch trunks and golden leaves softly out of focus" in preset.backgrounds
    assert "a warm indoor yoga studio, with pale walls and a large window, softly out of focus" in preset.backgrounds
    assert "soft autumn daylight, warm and evenly diffused with no harsh shadows" in preset.lighting
    assert "soft studio side light through a large window, warm in tone with gentle falloff on the far side" in preset.lighting
