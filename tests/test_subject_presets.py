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
def test_subject_preset_locks_are_english_sentences(subject_id: str):
    """锁定项进的是英文提示词：不能混入中文，否则生成的提示词会中英夹杂。"""
    data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
    subject = SubjectProfile.model_validate(data)
    for group in (subject.style_lock, subject.identity_lock, subject.outfit_lock):
        for item in group:
            assert item == item.strip()
            assert not any("一" <= ch <= "鿿" for ch in item), f"锁定项含中文：{item}"
    assert not any("一" <= ch <= "鿿" for ch in subject.extra)


@pytest.mark.parametrize("subject_id", SUBJECT_IDS)
def test_subject_preset_maps_into_prompt_placeholders(subject_id: str):
    """subject_mapping 的每个值都要能填进 {xxx} 占位符，不能留空壳。"""
    data = yaml.safe_load(_subject_path(subject_id).read_text(encoding="utf-8"))
    subject = SubjectProfile.model_validate(data)
    mapping = subject_mapping(subject)
    assert mapping["subject_noun"] == "woman"
    assert mapping["reference_tag"] == "<image1>"
    assert mapping["trigger_word"]
    for key in ("style_lock_sentence", "identity_lock_sentence", "outfit_lock_sentence"):
        assert mapping[key].startswith("Maintain ")
        assert mapping[key].endswith(" ")


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
