"""设计文档 §6/§10.3-10.6：可复现、子种子、拼接修复、caption 规则。"""
from __future__ import annotations

import copy

from charvar.composer import generate, normalize, render
from charvar.models import GenerateRequest, SubjectProfile
from charvar.preset_loader import ListPreset, load


def _req(**kwargs) -> GenerateRequest:
    return GenerateRequest(seed=42, **kwargs)


def test_same_seed_same_output(silver_witch):
    preset = load("q21_default_30")
    a = generate(silver_witch, preset, _req())
    b = generate(silver_witch, preset, _req())
    # run_id 含时间戳，允许不同；输出内容必须完全一致
    assert a.items == b.items
    assert a.seed == b.seed == 42


def test_different_seed_changes_random_parts(silver_witch):
    preset = load("q21_default_30")
    bg_choices = set()
    for seed in range(20):
        result = generate(silver_witch, preset, GenerateRequest(seed=seed))
        bg_choices.add((result.items[0].background, result.items[0].lighting))
    # 20 个种子不可能全部命中同一组背景光照（7x5 组合）
    assert len(bg_choices) > 1


def test_removing_variant_does_not_shift_others(silver_witch):
    """§10.4：删除预设中第 5 条后，其余条目的背景/光照不变。"""
    original = load("q21_default_30")
    trimmed = copy.deepcopy(original)
    removed_id = trimmed.variants[4].id
    del trimmed.variants[4]

    base = {item.variant_id: (item.background, item.lighting) for item in generate(silver_witch, original, _req()).items}
    after = {item.variant_id: (item.background, item.lighting) for item in generate(silver_witch, trimmed, _req()).items}
    assert removed_id in base
    assert set(after) == set(base) - {removed_id}
    assert after == {vid: pair for vid, pair in base.items() if vid != removed_id}


def test_normalize_no_glued_period_or_double_space(silver_witch):
    preset = load("q21_default_30")
    result = generate(silver_witch, preset, _req(separator=""))
    for item in result.items:
        assert "  " not in item.prompt
        assert ".." not in item.prompt
        for bad in (".P", ".p", ".F", ".f", ".M"):
            assert bad not in item.prompt
    # 原工作流缺陷：空分隔符导致 camera.Put 粘连，normalize 修复为 camera. Put
    assert "camera. Put" in result.items[0].prompt


def test_subject_noun_man_not_woman():
    preset = load("matrix_basic")
    man = SubjectProfile(id="demo_man", name="Demo", subject_noun="man")
    req = GenerateRequest(seed=1, randomize_background=False, randomize_lighting=False)
    result = generate(man, preset, req)
    for item in result.items:
        assert "one woman" not in item.prompt
        assert "one man" in item.prompt
    also_woman = generate(
        SubjectProfile(id="demo_w", name="Demo", subject_noun="woman"), preset, req
    )
    assert "one woman" in also_woman.items[0].prompt


def test_rear_angle_caption_has_no_expression(silver_witch):
    preset = load("q21_default_30")
    result = generate(silver_witch, preset, _req(fixed_background="gray", fixed_lighting="soft natural window light"))
    by_id = {item.variant_id: item for item in result.items}
    for angle in ("l135", "r135", "back"):
        item = by_id[f"full_{angle}"]
        assert " expression" not in item.caption
        assert item.meta["rear_angle"] is True
    neutral = by_id["head_front"].caption
    assert "headshot" in neutral and "gray background" in neutral


def test_master_render_appx_a(silver_witch):
    preset = load("q21_default_30")
    result = generate(silver_witch, preset, _req())
    expected = (
        "Create an image of the woman in the character sheet in <image1>. "
        "Maintain the original illustration style. Maintain the color palette. "
        "Maintain long silver-white hair. Maintain the same face and eye color. "
        "Maintain the same physique. Maintain the black witch robe and pointed hat."
    )
    assert result.items[0].master == expected


def test_render_keeps_unknown_placeholder():
    assert render("{known}", {"known": "x"}) == "x"
    assert render("{unknown}", {}) == "{unknown}"


def test_sampling_first_and_random(silver_witch):
    preset = load("q21_default_30")
    req = _req(count=5, sampling="first")
    result = generate(silver_witch, preset, req)
    assert [i.variant_id for i in result.items] == [v.id for v in preset.variants[:5]]
    req2 = _req(count=5, sampling="random")
    result2 = generate(silver_witch, preset, req2)
    assert len(result2.items) == 5
    again = generate(silver_witch, preset, _req(count=5, sampling="random"))
    assert result2.items == again.items
    ids = {i.variant_id for i in result2.items}
    assert ids <= {v.id for v in preset.variants}
