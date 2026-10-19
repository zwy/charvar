"""设计文档 §10.1：q21_default_30 展开后正好 30 条，文本与原 JSON 逐字一致。

原 JSON 位置：仓库根 characterDataset_q21Chardatasetbuilder.json；
外层 nodes[]（id=1194，Prompt 子图实例）widgets_values[3] 为当前接入流程的 30 行文本。
子图内 Text Multiline 节点残留旧版 30 行，作为 q21_legacy_30 预设。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from charvar.preset_loader import ListPreset, MatrixPreset, load

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "characterDataset_q21Chardatasetbuilder.json"


def _workflow_lines() -> list[str]:
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    widget = wf["nodes"][5]["widgets_values"][3]
    return [line for line in widget.split("\n") if line.strip()]


def _legacy_lines() -> list[str]:
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    node = wf["definitions"]["subgraphs"][1]["nodes"][6]
    widget = node["widgets_values"][0]
    return [line for line in widget.split("\n") if line.strip()]


def test_q21_default_30_matches_workflow_verbatim():
    preset = load("q21_default_30")
    assert isinstance(preset, ListPreset)
    assert len(preset.variants) == 30
    expected = _workflow_lines()
    for i, spec in enumerate(preset.variants):
        assert spec.text == expected[i], f"variants[{i}] 文本与原 JSON 不一致"


def test_q21_legacy_30_matches_subgraph_multiline():
    preset = load("q21_legacy_30")
    assert len(preset.variants) == 30
    expected = _legacy_lines()
    for i, spec in enumerate(preset.variants):
        assert spec.text == expected[i]
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

WORKFLOW_V2 = ROOT / "characterDataset_q21Chardatasetbuilder_v2.json"
V2_IDS = {
    *(f"{fr}_{ang}" for fr in ("full", "chest") for ang in
      ("front", "l45", "r45", "l90", "r90")),
    "full_l135", "full_r135", "full_back",
    "chest_high", "chest_low", "chest_back",
    *(f"head_{ang}" for ang in ("front", "l45", "r45", "l90", "r90", "back")),
    *(f"expr_{e}" for e in ("shocked", "laugh", "neutral", "smile", "sad", "pout", "cringe", "silly")),
}


def _v2_lines() -> list[str]:
    import re

    wf = json.loads(WORKFLOW_V2.read_text(encoding="utf-8"))
    sg = next(s for s in wf["definitions"]["subgraphs"] if s["name"] == "Prompt")
    node = next(n for n in sg["nodes"] if n["id"] == 1289)  # Four Images（推荐 4 参考图）

    def clean(text: str) -> str:
        t = re.sub(
            r"\[(?:BODY|PORTRAIT|HEAD|BACK)_NOTES\]|\[(?:BACKGROUND|LIGHTING)\]", "", text
        )
        return re.sub(r"\s{2,}", " ", t).strip()

    return [clean(line) for line in node["widgets_values"][0].split("\n") if line.strip()]


def test_q21_v2_30_matches_workflow_verbatim():
    preset = load("q21_v2_30")
    assert isinstance(preset, ListPreset)
    assert len(preset.variants) == 30
    expected = _v2_lines()
    for i, spec in enumerate(preset.variants):
        assert spec.text == expected[i], f"variants[{i}] 文本与新工作流不一致"
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
