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
