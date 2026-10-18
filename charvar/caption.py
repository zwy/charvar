"""训练 caption 生成（设计文档 §6.2）。

格式：{trigger_word}, {framing_tag}, {angle_tag}[, {expression_tag}], {background} background, {lighting_short}
- angle ∈ {l135, r135, back}（背面/遮挡）时不写表情标签；
- 背景为空则省略；lighting 取逗号前短句；
- 预设可用 caption_tags 字段覆盖默认映射。
"""
from __future__ import annotations

from charvar.models import SubjectProfile, VariantSpec

REAR_ANGLES = {"l135", "r135", "back"}

DEFAULT_CAPTION_TAGS: dict[str, dict[str, str]] = {
    "framing": {
        "full": "full body",
        "chest": "chest up",
        "head": "headshot",
    },
    "angle": {
        "front": "front view",
        "l45": "three-quarter view from left",
        "r45": "three-quarter view from right",
        "l90": "side view from left",
        "r90": "side view from right",
        "l135": "three-quarter rear view from left",
        "r135": "three-quarter rear view from right",
        "back": "view from behind",
    },
    "expression": {
        "neutral": "neutral expression",
        "smile": "smiling",
        "sad": "sad expression",
        "pout": "pouty expression",
        "cringe": "grimace",
        "silly": "playful expression",
    },
}


def lighting_short(lighting: str) -> str:
    """取逗号前的短句，如 "soft natural window light, diffused and even" → "soft natural window light"。"""
    return lighting.split(",", 1)[0].strip()


def build_caption(
    subject: SubjectProfile,
    spec: VariantSpec,
    background: str | None,
    lighting: str | None,
    caption_tags: dict[str, dict[str, str]] | None = None,
) -> str:
    tags = {k: {**v} for k, v in ((k, DEFAULT_CAPTION_TAGS.get(k, {})) for k in ("framing", "angle", "expression"))}
    for group, mapping in (caption_tags or {}).items():
        tags.setdefault(group, {}).update(mapping)

    parts: list[str] = []
    if subject.trigger_word:
        parts.append(subject.trigger_word)
    if spec.framing:
        parts.append(tags["framing"].get(spec.framing, spec.framing))
    if spec.angle:
        parts.append(tags["angle"].get(spec.angle, spec.angle))
    if spec.expression and spec.angle not in REAR_ANGLES:
        parts.append(tags["expression"].get(spec.expression, spec.expression))
    if background:
        parts.append(f"{background} background")
    if lighting:
        parts.append(lighting_short(lighting))
    return ", ".join(parts)
