"""画幅比例 + 分辨率档位注入设计验证。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from charvar.exporters import comfyui
from charvar.models import PromptItem


@pytest.mark.parametrize(("ratio", "expected_1k"), [
    ("1:1", (1024, 1024)),
    ("3:4", (768, 1024)),
    ("16:9", (1024, 576)),
    ("4:3", (1024, 768)),
    ("9:16", (576, 1024)),
    ("2:3", (704, 1024)),
    ("3:2", (1024, 704)),
    ("21:9", (1024, 448)),
])
def test_all_ui_ratios(ratio, expected_1k):
    width, height = comfyui.dimensions_for(ratio, "1k", None)
    assert (width, height) == expected_1k


@pytest.mark.parametrize(("tier", "expected_2_3"), [
    ("1k", (704, 1024)),
    ("2k", (1344, 2048)),
    ("4k", (2752, 4096)),
])
def test_resolution_tiers_and_4k_cap(tier, expected_2_3):
    assert comfyui.dimensions_for("2:3", tier, None) == expected_2_3


def test_auto_maps_by_framing():
    item = SimpleNamespace(index=0, variant_id="full_front", meta={"framing": "full"})
    near_item = SimpleNamespace(index=1, variant_id="head_front", meta={"framing": "head"})
    assert comfyui.dimensions_for_item("auto", "1k", item) == (704, 1024)
    assert comfyui.dimensions_for_item("auto", "1k", near_item) == (768, 1024)


def test_payload_injects_width_height() -> None:
    workflow = {"11": {"class_type": "EmptyLatentImage", "inputs": {"width": 1, "height": 1}}}
    mapping = {"mappings": [
        {"field": "width", "node_id": "11", "input_key": "width"},
        {"field": "height", "node_id": "11", "input_key": "height"},
    ]}
    item = PromptItem(
        index=0,
        variant_id="full_front",
        prompt="demo prompt",
        master="master",
        variant_text="demo",
        background="gray",
        lighting="soft light",
        caption="demo caption",
        meta={"framing": "full"},
    )
    payload = comfyui.build_payload(
        workflow,
        mapping,
        SimpleNamespace(seed=42, subject_id="demo"),
        item,
        {"width": 2048, "height": 1152},
    )
    assert payload["11"]["inputs"]["width"] == 2048
    assert payload["11"]["inputs"]["height"] == 1152
