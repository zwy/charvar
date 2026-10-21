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
    ("2k", (1696, 2528)),
])
def test_resolution_tiers(tier, expected_2_3):
    assert comfyui.dimensions_for("2:3", tier, None) == expected_2_3


def test_2k_uses_qwen_official_pixels():
    """2k 走 Qwen-Image-2.1 官方表：面积恒定 ~4.2MP，不是"长边归一"。

    旧实现按长边归一给 2048，16:9 只有 2.4MP（原生的 56%）→ 模型被迫上采样、图糊。
    """
    official = {
        "1:1": (2048, 2048),
        "4:3": (2400, 1792),
        "3:4": (1792, 2400),
        "3:2": (2528, 1696),
        "2:3": (1696, 2528),
        "16:9": (2752, 1536),
        "9:16": (1536, 2752),
    }
    for ratio, expected in official.items():
        assert comfyui.dimensions_for(ratio, "2k", None) == expected


def test_2k_official_area_is_constant():
    """官方表按面积定义：各比例面积都 ~4.2MP（差在像素取整）。"""
    official = [
        (2048, 2048), (2400, 1792), (1792, 2400), (2528, 1696),
        (1696, 2528), (2752, 1536), (1536, 2752),
    ]
    areas = [w * h for w, h in official]
    assert max(areas) / min(areas) < 1.03


def test_4k_tier_removed():
    """4k 档已下线（超 2K 训练尺寸会丢提示词遵循度）→ 直接报错，不是静默降级。"""
    import pytest as _pytest

    with _pytest.raises(ValueError):
        comfyui.dimensions_for("16:9", "4k", None)


def test_openrouter_keeps_long_side_algorithm():
    """OpenRouter 通路跑的不是 Qwen → 不走官方表，保持长边归一。"""
    assert comfyui.dimensions_for("16:9", "2k", None, use_model_native=False) == (2048, 1152)
    assert comfyui.dimensions_for("2:3", "2k", None, use_model_native=False) == (1344, 2048)


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


def test_auto_uses_sticker_meta_preferred_ratio() -> None:
    """sticker 快照：auto 时读 item.meta.preferred_aspect_ratio（1:1），旧条目照旧按景别。"""
    item = SimpleNamespace(index=0, variant_id="sticker_received", meta={"preferred_aspect_ratio": "1:1"})
    assert comfyui.dimensions_for_item("auto", "1k", item) == (1024, 1024)
    legacy = SimpleNamespace(index=1, variant_id="full_front", meta={"framing": "full"})
    assert comfyui.dimensions_for_item("auto", "1k", legacy) == (704, 1024)
    # 显式比例永远优先于快照（dimensions_for 不走 auto 分支）
    assert comfyui.dimensions_for_item("3:4", "1k", item) == (768, 1024)


def test_promptitem_meta_ratio_needs_no_namespace() -> None:
    """真实 PromptItem（meta=sticker snapshot）同样生效。"""
    item = PromptItem(
        index=0, variant_id="sticker_received", prompt="p", master="m", variant_text="v",
        background=None, lighting=None, caption="c",
        meta={"preferred_aspect_ratio": "1:1", "purpose": "sticker"},
    )
    assert comfyui.dimensions_for_item("auto", "2k", item) == (2048, 2048)
