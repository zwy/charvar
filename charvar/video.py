"""视频合成预设与 ffmpeg 渲染：把 run 里已生成的变体图按预设顺序轮播成 MP4。

与 contact sheet 同一思路（assets.py），区别是排序规则做成预设文件（presets/video/*.yaml）：
景别组内按朝向转一整圈（顺时针/逆时针），表情组顺序播放，首帧可放 contact sheet 当封面。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from charvar import assets, paths
from charvar.models import GenerateResult

# 顺时针转一圈（从正脸开始向画面左侧转头）：front → l45 → l90 → l135 → back → r135 → r90 → r45
CLOCKWISE_ANGLES = ("front", "l45", "l90", "l135", "back", "r135", "r90", "r45")
_ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "4:3": (4, 3), "3:4": (3, 4), "1:1": (1, 1)}
# 档位 = 短边像素：720P/1080P/4K → 720/1080/2160；4K 即 2×1080P 短边
_RES_SHORT = {"720p": 720, "1080p": 1080, "4k": 2160}
_COLOR_RE = re.compile(r"^[a-zA-Z0-9]{1,32}$")


class VideoPreset(BaseModel):
    """视频合成预设（presets/video/*.yaml）。"""

    name: str
    version: int = 1
    description: str = ""
    aspect: Literal["16:9", "9:16", "4:3", "3:4", "1:1"] = "9:16"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    fps: int = Field(default=24, ge=1, le=120)
    seconds_per_image: float = Field(default=1.0, gt=0, le=30)
    include_contact_sheet: bool = True
    contact_sheet_seconds: float = Field(default=2.0, gt=0, le=30)
    # 转场：先只支持硬切（fade 预留字段，设置后会报错提示未实现）
    transition: Literal["cut"] = "cut"
    # 正方图放进不同比例画面的处理：blur=模糊背景填满 / contain=纯色留边 / cover=居中裁切
    fit: Literal["blur", "contain", "cover"] = "blur"
    background: str = "black"  # fit=contain 的底色（ffmpeg 颜色名）
    # 播放顺序：turnaround=景别组各转一圈+表情顺序 / sequential=按生成顺序
    order_mode: Literal["turnaround", "sequential"] = "turnaround"
    group_order: list[str] = Field(default_factory=lambda: ["full", "chest", "head"])
    direction: Literal["clockwise", "counterclockwise"] = "clockwise"


class VideoPresetError(ValueError):
    """视频预设缺失或格式错误。"""


def video_presets_dir() -> Path:
    return paths.presets_dir() / "video"


def parse_video_preset(path: Path) -> VideoPreset:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise VideoPresetError(f"视频预设格式错误：{path}")
    data.setdefault("name", path.stem)
    try:
        return VideoPreset.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise VideoPresetError(f"视频预设字段有误：{path} · {exc}") from exc


def list_video_presets() -> list[VideoPreset]:
    if not video_presets_dir().exists():
        return []
    presets = []
    for path in sorted(video_presets_dir().glob("*.yaml")):
        try:
            presets.append(parse_video_preset(path))
        except VideoPresetError:
            continue  # 坏文件不挡其余预设
    return presets


def load_video_preset(name: str) -> VideoPreset:
    for preset in list_video_presets():
        if preset.name == name:
            return preset
    raise VideoPresetError(f"视频预设不存在：{name}（presets/video/）")


def resolution_size(aspect: str, resolution: str) -> tuple[int, int]:
    """档位 = 短边像素，按比例换算，结果取偶（H.264 yuv420p 要求）。"""
    ratio_w, ratio_h = _ASPECTS[aspect]
    short = _RES_SHORT[resolution]
    if ratio_w >= ratio_h:  # 横屏 / 方图：短边是高
        height = short
        width = round(height * ratio_w / ratio_h)
    else:  # 竖屏：短边是宽
        width = short
        height = round(width * ratio_h / ratio_w)
    width += width % 2
    height += height % 2
    return width, height


def resolve_order(items: list, preset: VideoPreset) -> list[int]:
    """按预设返回 item_index 播放顺序。

    turnaround：group_order 里每个景别，按 direction 的朝向序列转一圈，
    没在序列里的朝向（自定义角度）按生成顺序补在该组末尾；表情组随后按生成顺序；
    既无景别也无表情的条目兜底追加。缺景别元数据的 run 整体退化为生成顺序。
    """
    if preset.order_mode != "turnaround":
        return [item.index for item in items]
    has_framing = any(item.meta.get("framing") for item in items)
    has_expr = any(item.meta.get("expression") for item in items)
    if not (has_framing or has_expr):
        return [item.index for item in items]

    angles = list(CLOCKWISE_ANGLES)
    if preset.direction == "counterclockwise":
        angles = ["front"] + list(reversed(angles[1:]))  # front 起点不变，反向转
    ordered: list = []
    used: set[int] = set()

    def take(candidates: list) -> list:
        picked = [item for item in candidates if item.index not in used]
        used.update(item.index for item in picked)
        return picked

    for framing in preset.group_order:
        group = [
            item
            for item in items
            if item.meta.get("framing") == framing and not item.meta.get("expression")
        ]
        ranked = []
        for angle in angles:
            ranked.extend(item for item in group if item.meta.get("angle") == angle)
        # 序外角度（自定义朝向）按生成顺序兜底；带 expression 的条目（如 expr_* 的
        # 元数据是 framing=head + angle=front + expression=…）只归表情段，不进本组
        extra = [item for item in group if item.meta.get("angle") not in angles]
        ordered.extend(take(ranked) + take(extra))
    ordered.extend(take([item for item in items if item.meta.get("expression")]))
    ordered.extend(take(list(items)))  # 兜底：既无景别也无表情的条目按生成顺序补尾
    return [item.index for item in ordered]


def video_path(run_id: str, preset_name: str) -> Path:
    return assets.run_assets_dir(run_id) / "videos" / f"{assets.safe_filename(preset_name)}.mp4"


def video_dir(run_id: str) -> Path:
    return assets.run_assets_dir(run_id) / "videos"


def list_videos(run_id: str) -> list[dict[str, Any]]:
    """run 的合成视频列表（videos/*.mp4，按修改时间倒序）。"""
    directory = video_dir(run_id)
    if not directory.exists():
        return []
    entries = []
    for path in sorted(directory.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        entries.append(
            {
                "name": path.name,
                "url": f"/runs/{safe_run_id(run_id)}/assets/videos/{path.name}",
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def safe_run_id(run_id: str) -> str:
    return assets.safe_run_id(run_id)


def _find_original(run_id: str, item_index: int) -> Path | None:
    for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
        path = assets.original_dir(run_id) / f"{item_index:06d}{suffix}"
        if path.exists():
            return path
    return None


def _clip_filter(index: int, preset: VideoPreset, width: int, height: int, fps: int) -> str:
    """单输入 → 帧率对齐的成品流（各 fit 滤镜链 + fps/setpts 供 concat）。"""
    if preset.fit == "blur":
        sigma = round(max(width, height) / 36)
        return (
            f"[{index}:v]split=2[b{index}][f{index}];"
            f"[b{index}]scale={width}:{height},setsar=1,gblur=sigma={sigma}[bb{index}];"
            f"[f{index}]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[fs{index}];"
            f"[bb{index}][fs{index}]overlay=(W-w)/2:(H-h)/2,fps={fps},setpts=PTS-STARTPTS[v{index}]"
        )
    if preset.fit == "cover":
        body = (
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )
    else:  # contain
        background = preset.background if _COLOR_RE.fullmatch(preset.background) else "black"
        body = (
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background},setsar=1"
        )
    return f"{body},fps={fps},setpts=PTS-STARTPTS[v{index}]"


def _ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/ffmpeg")
    if fallback.exists():
        return str(fallback)
    raise VideoPresetError("未找到 ffmpeg：请先 brew install ffmpeg")


def render_video(
    run_id: str,
    result,
    records: list[dict[str, Any]],
    preset: VideoPreset,
    *,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """把 run 里已有原图按预设顺序合成 MP4，返回产物信息。"""
    width, height = resolution_size(preset.aspect, preset.resolution)
    fps = preset.fps
    frames_per_image = max(1, round(preset.seconds_per_image * fps))
    seconds_per_image = frames_per_image / fps

    # 首帧封面：contact sheet
    clips: list[tuple[Path, float, str]] = []
    if preset.include_contact_sheet and assets.sheet_path(run_id, "contact").exists():
        sheet_frames = max(1, round(preset.contact_sheet_seconds * fps))
        clips.append((assets.sheet_path(run_id, "contact"), sheet_frames / fps, "contact sheet"))

    skipped: list[int] = []
    used_images = 0
    for item_index in resolve_order(result.items, preset):
        path = _find_original(run_id, item_index)
        if path is None:
            skipped.append(item_index)
            continue
        label = next(
            (item.variant_id for item in result.items if item.index == item_index),
            f"#{item_index:03d}",
        )
        clips.append((path, seconds_per_image, label))
        used_images += 1
    if not clips:
        raise VideoPresetError("run 里还没有可用的成品图（先推送 ComfyUI 并同步输出）")

    graph_parts = [
        _clip_filter(i, preset, width, height, fps) + ";" for i in range(len(clips))
    ]
    concat_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
    graph = "".join(graph_parts) + f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vout]"

    cmd = [_ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    for path, seconds, _label in clips:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.6f}", "-i", str(path)]
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-r",
        str(fps),
    ]
    out = video_path(run_id, preset.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd.append(str(out))
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace")[-400:]
        raise VideoPresetError(f"ffmpeg 渲染失败：{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoPresetError("ffmpeg 渲染超时") from exc

    return {
        "path": out,
        "name": out.name,
        "preset": preset.name,
        "aspect": preset.aspect,
        "resolution": preset.resolution,
        "size": [width, height],
        "fps": fps,
        "seconds_per_image": preset.seconds_per_image,
        "duration": round(sum(seconds for _p, seconds, _l in clips), 3),
        "images_used": used_images,
        "skipped": skipped,
        "contact_sheet_open": clips[0][2] == "contact sheet" if clips else False,
    }