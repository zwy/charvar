"""贴字预设（presets/overlays/*.yaml）与 PIL 后处理渲染器。

与 sticker（带字表情包）是两套机制：sticker 的字是**提示词驱动**（模型原生画字，
charvar/sticker_policy.py）；贴字是**确定性 PIL 后处理**（已生成图上盖字，不改
原图，产物落 ``labeled/{index:06d}.png``）。两者可共存：同一 run 既可以推送
sticker 预设，也可以事后贴字。

预设字段全部来自 YAML（同 presets/video 的房格）：
- position：left_top/right_top/left_bottom/right_bottom/center；
- margin_x/margin_y：画面宽/高的占比边距；size：短边占比字号；
- stroke/stroke_color：描边；plate=pill 时文字下垫圆角底板（半透明色）；
- max_width：换行阈值（画面宽占比；CJK 按字、拉丁按词归行）；
- rotate：整块文字旋转角度（绕自身中心，不裁画面）；
- opacity：整层透明度。font 从 presets/overlays/fonts/ 与系统字体表解析。

文本来源由调用方决定（text_from）：
- ``outfit_label``：服装组合变体的中文服装名（meta.outfit_label，缺省回退变体名）；
- ``pose_tag``：动作/体式 tag（meta.pose）；
- ``variant_id``：变体 id 本身；
- ``fixed``：调用方给的固定文本。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from charvar import paths
from charvar.assets import original_dir, run_assets_dir, safe_run_id
from charvar.models import GenerateResult
from charvar.preset_loader import PresetError

TEXT_SOURCES = ("outfit_label", "pose_tag", "variant_id", "fixed")

# 系统字体候选（带 CJK 的常见路径；仓库自带 fonts/ 优先，其次按序探测）。
_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/notocjk/NotoSansCJK-Regular.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
)

_OVERLAY_ROOT_KEYS = frozenset(
    {
        "name", "type", "version", "description", "position", "margin_x", "margin_y",
        "font", "size", "color", "stroke", "stroke_color", "plate", "plate_color",
        "plate_padding", "opacity", "rotate", "max_width", "align", "line_gap",
    }
)


class OverlayPreset(BaseModel):
    """贴字预设（presets/overlays/*.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["overlay"] = "overlay"
    version: int = 1
    description: str = ""
    position: Literal["left_top", "right_top", "left_bottom", "right_bottom", "center"] = "left_bottom"
    margin_x: float = Field(default=0.04, ge=0.0, le=0.5)   # 画面宽占比
    margin_y: float = Field(default=0.04, ge=0.0, le=0.5)   # 画面高占比
    font: str = ""                                          # 空 = 自动（仓库自带 → 系统候选）
    size: float = Field(default=0.05, ge=0.005, le=0.5)     # 字号 = 短边占比
    color: str = "#FFFFFF"
    stroke: float = Field(default=0.004, ge=0.0, le=0.1)    # 描边宽 = 短边占比
    stroke_color: str = "#000000"
    plate: Literal["none", "pill"] = "none"                 # pill = 圆角底板
    plate_color: str = "#00000080"                          # 支持 #RRGGBBAA
    plate_padding: float = Field(default=0.012, ge=0.0, le=0.2)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    rotate: float = 0.0                                     # 整块旋转角度（度）
    max_width: float = Field(default=0.6, ge=0.1, le=1.0)   # 换行阈值 = 画面宽占比
    align: Literal["left", "center", "right"] = "left"
    line_gap: float = Field(default=0.14, ge=0.0, le=1.0)   # 行距 = 字号占比


def overlays_dir() -> Path:
    return paths.presets_dir() / "overlays"


def fonts_dir() -> Path:
    return overlays_dir() / "fonts"


def parse_overlay_file(path: Path) -> OverlayPreset:
    if not path.exists():
        raise PresetError(f"贴字预设文件不存在：{path}", status=404)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise PresetError(f"贴字预设 {path} 不是合法 YAML 映射")
    unknown = sorted(set(raw) - _OVERLAY_ROOT_KEYS)
    if unknown:
        raise PresetError(f"贴字预设 {path.name} 有未知根字段：{unknown}")
    if raw.get("type") != "overlay":
        raise PresetError(f"贴字预设 {path.name} 的 type 必须是 overlay，当前 {raw.get('type')!r}")
    try:
        return OverlayPreset.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise PresetError(f"贴字预设 {path} 校验失败：\n{exc}") from exc


def resolve_overlay_path(name: str) -> Path:
    """按贴字预设名定位 YAML（presets/overlays/<name>.yaml；可带 .yaml 后缀）。"""
    base = overlays_dir()
    if "/" in name or "\\" in name or name in (".", ".."):
        raise PresetError(f"贴字预设名不合法：{name!r}")
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    return base / name


def load_overlay(name: str) -> OverlayPreset:
    return parse_overlay_file(resolve_overlay_path(name))


def list_overlay_presets() -> list[dict[str, Any]]:
    base = overlays_dir()
    result: list[dict[str, Any]] = []
    if not base.exists():
        return result
    for path in sorted(base.glob("*.yaml")):
        try:
            preset = parse_overlay_file(path)
        except PresetError:
            continue
        font_path = resolve_font(preset.font)
        result.append(
            {
                "name": preset.name,
                "type": preset.type,
                "version": preset.version,
                "description": preset.description,
                "position": preset.position,
                "font": preset.font,
                "font_available": font_path is not None,
            }
        )
    return result


def resolve_font(name: str) -> Path | None:
    """字体文件解析：仓库自带（fonts/）优先，其次系统候选。

    name 为空 = 自动选第一个可用的候选（macOS/Linux/Windows 都有带 CJK 的兜底）。
    """
    if name:
        base = fonts_dir()
        for candidate in (base / name, base / f"{name}.ttf", base / f"{name}.otf", base / f"{name}.ttc"):
            if candidate.exists():
                return candidate
        return None
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def labeled_dir(run_id: str) -> Path:
    """贴字产物目录：run 目录下的 labeled/ 子目录（原图 original/ 不动）。"""
    return run_assets_dir(run_id) / "labeled"


def labeled_path(run_id: str, item_index: int) -> Path:
    return labeled_dir(run_id) / f"{item_index:06d}.png"


def resolve_text(
    text_from: str,
    fixed: str | None,
    meta: dict[str, Any],
    variant_id: str,
) -> str | None:
    """按 text_from 解析一条变体要贴的文字；解析不到返回 None（调用方跳过）。"""
    if text_from not in TEXT_SOURCES:
        raise PresetError(f"text_from 只支持 {'/'.join(TEXT_SOURCES)}，当前 {text_from!r}")
    if text_from == "fixed":
        return (fixed or "").strip() or None
    if text_from == "outfit_label":
        # 组合变体的中文服装名；非组合 run 回退变体名（组合 id = outfit__pose）
        label = str(meta.get("outfit_label") or "").strip()
        return label or (variant_id.split("__", 1)[0] if "__" in variant_id else variant_id)
    if text_from == "pose_tag":
        return str(meta.get("pose") or "").strip() or None
    return variant_id


def _rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """#RGB / #RRGGBB / #RRGGBBAA → (r, g, b, a)；解析不了回退默认。"""
    raw = value.strip().lstrip("#")
    if raw.lower() == "none":
        return fallback
    try:
        if len(raw) == 3:
            return (*tuple(int(c * 2, 16) for c in raw), 255)
        if len(raw) == 6:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), 255)
        if len(raw) == 8:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), int(raw[6:8], 16))
    except ValueError:
        pass
    return fallback


def _tokens(text: str) -> list[str]:
    """归行 token：拉丁字母/数字成词，其余逐字符（CJK 无词界，按字断行）。"""
    out: list[str] = []
    word = ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "_-./&%+"):
            word += ch
        else:
            if word:
                out.append(word)
                word = ""
            out.append(ch)
    if word:
        out.append(word)
    return out


def wrap_text(text: str, measure, max_px: float) -> list[str]:
    """把一段文本按 max_px 像素宽归行：显式换行符优先，其余贪心装填。

    measure 是可注入的测量函数（签名 (text) -> float）——测试可注入文本长度，
    不必真的加载字体。逐 token 测宽（CJK 按字、拉丁按词），装不下就断行。
    """
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for token in _tokens(para):
            if token.isspace():
                continue
            candidate = cur + token
            if cur and measure(candidate) > max_px:
                lines.append(cur)
                cur = token
            else:
                cur = candidate
        lines.append(cur)
    return lines or [""]


def _render_block(lines, font, *, fill, stroke_px, stroke_fill, plate, radius, gap_px, align):
    """把行列表画进一张透明层。plate = (填色, 内边距) 或 None。

    返回 (layer, block_w, block_h)：block 尺寸是**未旋转**的文字块（含 plate），
    外层定位/旋转都以它为基准。
    """
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    widths = [probe.textlength(line, font=font) for line in lines]
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    pad = plate[1] if plate else 0
    block_w = int(round(max(widths))) + pad * 2 + stroke_px * 2
    step = line_h + gap_px
    block_h = line_h * len(lines) + gap_px * (len(lines) - 1) + pad * 2 + stroke_px * 2
    layer = Image.new("RGBA", (max(block_w, 1), max(block_h, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if plate is not None:
        draw.rounded_rectangle((0, 0, block_w - 1, block_h - 1), radius=radius, fill=plate[0])
    for index, line in enumerate(lines):
        line_w = widths[index]
        body_w = block_w - pad * 2 - stroke_px * 2
        if align == "center":
            offset = (body_w - line_w) / 2
        elif align == "right":
            offset = body_w - line_w
        else:
            offset = 0
        draw.text(
            (pad + stroke_px + offset, pad + stroke_px + index * step),
            line,
            font=font,
            fill=fill[:3],
            stroke_width=stroke_px or 0,
            stroke_fill=stroke_fill[:3],
        )
    return layer, block_w, block_h


def render_overlay(
    src: Path,
    dst: Path,
    text: str,
    preset: OverlayPreset,
    font_path: Path,
) -> Path:
    """把 text 按 preset 盖到 src 图上，产物写 dst（不覆盖原图）。"""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(src) as opened:
        base = opened.convert("RGBA")
    width, height = base.size
    font_px = max(6, round(preset.size * min(width, height)))
    font = ImageFont.truetype(str(font_path), font_px)

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    max_px = preset.max_width * width
    lines = wrap_text(text, lambda value: probe.textlength(value, font=font), max_px)
    gap_px = round(preset.line_gap * font_px)

    fill = _rgba(preset.color, (255, 255, 255, 255))
    stroke_color = _rgba(preset.stroke_color, (0, 0, 0, 255))
    stroke_px = round(preset.stroke * min(width, height))
    plate = None
    radius = 0
    if preset.plate == "pill":
        padding = round(preset.plate_padding * min(width, height))
        plate = (_rgba(preset.plate_color, (0, 0, 0, 128)), padding)
        radius = max(2, padding)
    layer, block_w, block_h = _render_block(
        lines,
        font,
        fill=fill,
        stroke_px=stroke_px,
        stroke_fill=stroke_color,
        plate=plate,
        radius=radius,
        gap_px=gap_px,
        align=preset.align,
    )

    if preset.rotate:
        layer = layer.rotate(preset.rotate, expand=True)

    if preset.opacity < 1.0:
        alpha = layer.getchannel("A").point(lambda value: int(value * preset.opacity))
        layer.putalpha(alpha)

    if preset.position == "center":
        x = (width - block_w) // 2
        y = (height - block_h) // 2
    else:
        to_left = preset.position in ("left_top", "left_bottom")
        to_top = preset.position in ("left_top", "right_top")
        margin_x = round(preset.margin_x * width)
        margin_y = round(preset.margin_y * height)
        x = max(0, margin_x if to_left else width - margin_x - block_w)
        y = max(0, margin_y if to_top else height - margin_y - block_h)
    # 旋转后层变大了：以未旋转块的中心为基准回贴，块在角上的位置不因旋转漂移
    paste_x = x - (layer.width - block_w) // 2
    paste_y = y - (layer.height - block_h) // 2
    base.alpha_composite(layer, (paste_x, paste_y))

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        base.convert("RGB").save(dst, quality=92)
    elif dst.suffix.lower() == ".webp":
        base.save(dst, quality=92)
    else:
        base.save(dst)
    return dst


def apply_overlay(
    conn,
    run_id: str,
    *,
    overlay: str,
    text_from: str = "outfit_label",
    fixed: str | None = None,
    items: list[int] | None = None,
) -> dict[str, Any]:
    """对一个已生成 run 的所有成品图贴字，产物落 labeled/。

    返回 {"run_id", "overlay", "text_from", "applied": [...], "skipped": [...]}；
    applied 项带 item_index/labeled_url/text，skipped 项带 item_index/reason。
    """
    from charvar import storage

    preset = load_overlay(overlay)
    result = storage.get_run(conn, run_id)
    if result is None:
        raise PresetError(f"生成记录不存在：{run_id}", status=404)
    font_path = resolve_font(preset.font)
    if font_path is None:
        raise PresetError(
            f"贴字预设 {preset.name} 的字体不可用：{preset.font!r}"
            "（把字体文件放进 presets/overlays/fonts/ 或改用系统自带字体）"
        )
    target_indexes = set(items) if items else None
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in result.items:
        if target_indexes is not None and item.index not in target_indexes:
            continue
        original = None
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = original_dir(run_id) / f"{item.index:06d}{suffix}"
            if candidate.exists():
                original = candidate
                break
        if original is None:
            skipped.append({"item_index": item.index, "reason": "该条还没有成品图"})
            continue
        text = resolve_text(text_from, fixed, item.meta, item.variant_id)
        if not text:
            skipped.append({"item_index": item.index, "reason": f"解析不到文本（{text_from}）"})
            continue
        dst = labeled_path(run_id, item.index)
        render_overlay(original, dst, text, preset, font_path)
        applied.append(
            {
                "item_index": item.index,
                "labeled_url": f"/runs/{safe_run_id(run_id)}/assets/labeled/{dst.name}",
                "text": text,
            }
        )
    return {
        "run_id": run_id,
        "overlay": preset.name,
        "text_from": text_from,
        "applied": applied,
        "skipped": skipped,
    }