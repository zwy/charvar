"""视频合成：预设解析 / 顺序 / 分辨率换算 / ffmpeg 渲染冒烟。"""
from __future__ import annotations

import json
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

def test_resolve_order_q21_v2_turnaround():
    """v2 预设的 chest_high/chest_low 是序列外机位，落在 chest 组尾部（生成序）。"""
    v2_angles = [
        *(f"full_{a}" for a in ("front", "l45", "r45", "l90", "r90", "l135", "r135", "back")),
        *(f"chest_{a}" for a in ("front", "l45", "r45", "l90", "r90", "high", "low", "back")),
        *(f"head_{a}" for a in ("front", "l45", "r45", "l90", "r90", "back")),
        *(f"expr_{e}" for e in ("shocked", "laugh", "neutral", "smile", "sad", "pout", "cringe", "silly")),
    ]
    specs = []
    for vid in v2_angles:
        if vid.startswith("expr_"):
            specs.append(VariantSpec(id=vid, framing="head", angle="front", expression=vid[5:], text="t"))
        else:
            framing, angle = vid.split("_")
            specs.append(VariantSpec(id=vid, framing=framing, angle=angle, text="t"))
    result_items = [_item(i, spec) for i, spec in enumerate(specs)]

    seq = [
        item.variant_id
        for item in (_result(result_items).items[i] for i in video.resolve_order(result_items, _preset()))
    ]
    assert seq[:8] == [f"full_{a}" for a in ("front", "l45", "l90", "l135", "back", "r135", "r90", "r45")]
    # chest：顺时针圈到 back，high/low 两个序列外机位按生成序补尾
    assert seq[8:16] == [
        "chest_front", "chest_l45", "chest_l90", "chest_back", "chest_r90",
        "chest_r45", "chest_high", "chest_low",
    ]
    assert seq[22:] == [
        "expr_shocked", "expr_laugh", "expr_neutral", "expr_smile",
        "expr_sad", "expr_pout", "expr_cringe", "expr_silly",
    ]
    assert sorted(video.resolve_order(result_items, _preset())) == sorted(
        item.index for item in result_items
    )


def test_music_preset_fields_and_files():
    """默认预设带背景音乐 1.mp3，白名单能列出它。"""
    preset = video.load_video_preset("q21_turnaround")
    assert preset.music == "轻跳节拍.wav"
    assert preset.music_volume == pytest.approx(1.0)
    assert preset.music_fade == pytest.approx(1.0)
    assert (video.music_dir() / "轻跳节拍.wav").is_file()
    files = video.music_files()
    assert "1.mp3" in files and "轻跳节拍.wav" in files
    assert files == sorted(files)
    # 非音频/隐藏文件不进列表
    assert all(f.endswith((".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus")) for f in files)


def test_resolve_music_cases():
    preset = _preset()
    # None/空白 = 预设默认（轻跳节拍.wav，中文文件名须过白名单）
    assert video.resolve_music(preset, None) == video.music_dir() / "轻跳节拍.wav"
    assert video.resolve_music(preset, "  ") == video.music_dir() / "轻跳节拍.wav"
    # "none" = 明确不加
    assert video.resolve_music(preset, "none") is None
    # 无音乐预设 → None
    assert video.resolve_music(_preset(music=None), None) is None
    # 路径穿越/非法文件名 → 拒绝
    for bad in ("../evil.mp3", "/etc/passwd.mp3", "a/b.mp3", "sub.mp3.mp4", "tone"):
        with pytest.raises(video.VideoPresetError, match="不合法"):
            video.resolve_music(preset, bad)
    # 覆盖文件名不存在 → 拒绝
    with pytest.raises(video.VideoPresetError, match="不存在"):
        video.resolve_music(preset, "missing.mp3")


def _write_wav(path, seconds: float = 2.0, rate: int = 8000):
    import math
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            sample = int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / rate))
            frames += sample.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_video_with_music(tmp_path, monkeypatch):
    """背景音乐：短音乐循环补齐、时长对齐视频、产物有音频流。"""
    import subprocess

    wav = tmp_path / "music" / "tone.wav"
    _write_wav(wav, seconds=2.0)
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(video, "music_dir", lambda: wav.parent)
    run_id = "music_run"
    result_items = _q21_items(shuffled=False)[:3]
    for item in result_items:
        _write_png(tmp_path / "generated" / run_id / "original" / f"{item.index:06d}.png")
    result = _result(result_items)

    preset = _preset(
        include_contact_sheet=False, fps=8, seconds_per_image=0.25,
        resolution="720p", music="tone.wav",
    )
    info = video.render_video(run_id, result, [], preset, timeout=120)
    assert info["music"] == "tone.wav"
    assert info["duration"] == pytest.approx(0.75)  # 3 张 × 0.25s

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name:format=duration", "-of", "json", str(info["path"])],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        types = [stream["codec_type"] for stream in data["streams"]]
        assert "audio" in types and "video" in types
        audio = next(stream for stream in data["streams"] if stream["codec_type"] == "audio")
        assert audio["codec_name"] in ("aac", "mp4a")
        # 容器总时长 ≈ 视频时长（短 wav 循环 + 截断到位）
        assert 0.75 <= float(data["format"]["duration"]) <= 1.2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_video_music_override_none(tmp_path, monkeypatch):
    """请求 music_override="none" → 无音乐分支照常渲染。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    run_id = "nomusic_run"
    result_items = _q21_items(shuffled=False)[:2]
    for item in result_items:
        _write_png(tmp_path / "generated" / run_id / "original" / f"{item.index:06d}.png")
    preset = _preset(
        include_contact_sheet=False, fps=8, seconds_per_image=0.25,
        resolution="720p", music=None,
    )
    info = video.render_video(run_id, _result(result_items), [], preset, music_override="none", timeout=120)
    assert info["music"] is None


# ---------- API 层：/video-music 列表 + 音乐覆盖校验 ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import pathlib

    from fastapi.testclient import TestClient

    from charvar.api import build_app

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def _classic_run(client) -> str:
    client.post("/subjects", json={"id": "demo", "name": "演示"})
    return client.post(
        "/generate", json={"subject_id": "demo", "preset": "q21_default_30", "seed": 1}
    ).json()["run_id"]


def test_api_video_music_list(client):
    data = client.get("/video-music").json()
    assert "1.mp3" in data["files"]


def test_api_video_music_bad_name_400(client):
    run_id = _classic_run(client)
    resp = client.post(f"/runs/{run_id}/video", json={"preset": "q21_turnaround", "music": "../evil.mp3"})
    assert resp.status_code == 400
    assert "不合法" in resp.json()["detail"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_api_video_render_with_music(client, tmp_path, monkeypatch):
    """端到端：经典 run + 两张原图 + music="1.mp3" 覆盖 → 产物带音乐。"""
    run_id = _classic_run(client)
    _write_png(tmp_path / "generated" / run_id / "original" / "000000.png")
    _write_png(tmp_path / "generated" / run_id / "original" / "000001.png")
    resp = client.post(
        f"/runs/{run_id}/video", json={"preset": "q21_turnaround", "music": "1.mp3"}
    )
    assert resp.status_code == 200, resp.text
    info = resp.json()
    assert info["music"] == "1.mp3"
    assert info["duration"] == pytest.approx(2.0)  # contact sheet 缺失 → 只 2 张 × 1s
