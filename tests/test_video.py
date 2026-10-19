"""视频合成：预设解析 / 顺序 / 分辨率换算 / ffmpeg 渲染冒烟。"""
from __future__ import annotations

import shutil

import pytest

from charvar import video
from charvar.models import GenerateResult, PromptItem, VariantSpec


def _item(index: int, variant: VariantSpec) -> PromptItem:
    return PromptItem(
        index=index,
        variant_id=variant.id,
        prompt="p",
        master="m",
        variant_text="t",
        background=None,
        lighting=None,
        caption="c",
        meta={
            "framing": variant.framing,
            "angle": variant.angle,
            "expression": variant.expression,
        },
    )


def _q21_items(shuffled: bool) -> list[PromptItem]:
    angles = ["front", "l45", "r45", "l90", "r90", "l135", "r135", "back"]
    exprs = ["sad", "neutral", "smile", "pout", "cringe", "silly"]
    specs = []
    for framing in ["full", "chest", "head"]:
        for angle in angles:
            specs.append(VariantSpec(id=f"{framing}_{angle}", framing=framing, angle=angle, text="t"))
    for expr in exprs:
        # 真实数据里表情变体的 meta 同时带 framing=head + angle=front（预设 YAML 如此），
        # 解析器必须把它们只归表情段、不重复计入 head 组
        specs.append(VariantSpec(id=f"expr_{expr}", framing="head", angle="front", expression=expr, text="t"))
    if shuffled:  # 故意打乱，验证解析器按元数据重排
        specs = specs[11:] + specs[:11]
    return [_item(i, spec) for i, spec in enumerate(specs)]


def _preset(**overrides):
    base = video.load_video_preset("q21_turnaround")
    return base.model_copy(update=overrides)


def _result(items: list[PromptItem]) -> GenerateResult:
    return GenerateResult(
        run_id="r", subject_id="s", preset="q21_default_30", seed=42, items=items
    )


def test_video_preset_loads_with_defaults():
    presets = video.list_video_presets()
    names = {p.name for p in presets}
    assert "q21_turnaround" in names
    preset = video.load_video_preset("q21_turnaround")
    assert preset.aspect == "9:16"
    assert preset.fps == 24
    assert preset.seconds_per_image == 1.0
    assert preset.include_contact_sheet is True
    assert preset.fit == "blur"
    assert preset.order_mode == "turnaround"
    with pytest.raises(video.VideoPresetError):
        video.load_video_preset("missing_preset")


def test_resolution_size_table():
    assert video.resolution_size("9:16", "1080p") == (1080, 1920)
    assert video.resolution_size("16:9", "1080p") == (1920, 1080)
    assert video.resolution_size("4:3", "720p") == (960, 720)
    assert video.resolution_size("3:4", "1080p") == (1080, 1440)
    assert video.resolution_size("1:1", "1080p") == (1080, 1080)
    assert video.resolution_size("9:16", "720p") == (720, 1280)
    assert video.resolution_size("9:16", "4k") == (2160, 3840)
    assert video.resolution_size("16:9", "4k") == (3840, 2160)


def test_resolve_order_turnaround_clockwise():
    preset = _preset(direction="clockwise")
    result_items = _q21_items(shuffled=True)
    seq = [
        item.variant_id
        for item in (_result(result_items).items[i] for i in video.resolve_order(result_items, preset))
    ]
    assert seq == (
        [f"full_{a}" for a in ("front", "l45", "l90", "l135", "back", "r135", "r90", "r45")]
        + [f"chest_{a}" for a in ("front", "l45", "l90", "l135", "back", "r135", "r90", "r45")]
        + [f"head_{a}" for a in ("front", "l45", "l90", "l135", "back", "r135", "r90", "r45")]
        + ["expr_sad", "expr_neutral", "expr_smile", "expr_pout", "expr_cringe", "expr_silly"]
    )
    assert sorted(video.resolve_order(result_items, preset)) == sorted(
        item.index for item in result_items
    )


def test_resolve_order_direction_and_fallback():
    result_items = _q21_items(shuffled=False)
    ccw = _preset(direction="counterclockwise", group_order=["head"])
    seq = [
        item.variant_id
        for item in (_result(result_items).items[i] for i in video.resolve_order(result_items, ccw))
    ]
    # 只取 head 组：逆时针从 front 开始反向
    head_seq = [v for v in seq if v.startswith("head")]
    assert head_seq == [
        "head_front", "head_r45", "head_r90", "head_r135", "head_back", "head_l135", "head_l90", "head_l45",
    ]

    # 缺景别元数据 → 退化为生成顺序
    plain = [_item(i, VariantSpec(id=f"v{i}", text="t")) for i in range(3)]
    assert video.resolve_order(plain, _preset()) == [0, 1, 2]
    # sequential 模式 = 原顺序
    assert video.resolve_order(result_items, _preset(order_mode="sequential")) == [
        item.index for item in result_items
    ]


def _write_png(path, size=(64, 64)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (20, 40, 60)).save(path, format="PNG")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_video_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    run_id = "demo_run"
    result_items = _q21_items(shuffled=False)[:3]  # 3 张就够
    for item in result_items:
        _write_png(tmp_path / "generated" / run_id / "original" / f"{item.index:06d}.png")
    result = _result(result_items)

    preset = _preset(
        include_contact_sheet=False, fps=8, seconds_per_image=0.25, resolution="720p"
    )
    info = video.render_video(run_id, result, [], preset, timeout=120)
    out: str = str(info["path"])
    assert out.endswith("q21_turnaround.mp4")
    assert info["duration"] == pytest.approx(3 * 2 / 8)  # 0.25s×8fps=2 帧/张
    data = open(out, "rb").read(64)
    assert len(data) > 0
    assert b"ftyp" in data[:32]
    assert video.list_videos(run_id)[0]["name"] == "q21_turnaround.mp4"