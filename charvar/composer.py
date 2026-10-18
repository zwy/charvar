"""核心：组合维度 → 变体列表 → 拼接最终提示词（设计文档 §6）。

关键规则：
- 可复现：禁止全局 random；子种子 = sha256(f"{seed}:{spec.id}") 前 8 字节；
- render：只替换已知占位符（缺失保留原样并记警告），不执行任意代码；
- 每条变体独立的 RNG：增删变体不影响其他条目的背景/光照（测试 §10.4）；
- normalize：合并多余空格、句号后补空格、去 ".."、修复 ".P"/".p" 粘连。
"""
from __future__ import annotations

import itertools
from typing import Any
import logging
import re
from datetime import datetime
from random import Random

from charvar.caption import REAR_ANGLES, build_caption
from charvar.models import (
    GenerateRequest,
    GenerateResult,
    PromptItem,
    SubjectProfile,
    VariantSpec,
)
from charvar.preset_loader import ListPreset, MatrixPreset, Preset, PresetError
from charvar.wildcard import expand_inline, rng_for, seed_for

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render(text: str, mapping: dict[str, str | None]) -> str:
    """把 {key} 占位符替换为 mapping 中的值；缺失的键保留原样并记录警告。"""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            logger.warning("占位符未提供值，保留原样：%s", key)
            return match.group(0)
        value = mapping[key]
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_sub, text)


def lock_sentence(items: list[str]) -> str:
    """style_lock/identity_lock/outfit_lock → "Maintain X. Maintain Y. "；空列表输出空串。"""
    if not items:
        return ""
    return "".join(f"Maintain {item}. " for item in items)


def subject_mapping(subject: SubjectProfile) -> dict[str, str | None]:
    return {
        "subject_noun": subject.subject_noun,
        "reference_tag": subject.reference_tag,
        "name": subject.name,
        "trigger_word": subject.trigger_word,
        "extra": subject.extra,
        "style_lock_sentence": lock_sentence(subject.style_lock),
        "identity_lock_sentence": lock_sentence(subject.identity_lock),
        "outfit_lock_sentence": lock_sentence(subject.outfit_lock),
    }


CLOSEUP_FRAMING_GUARDS = {
    "chest": (
        "Chest-up framing, no lower body, hips, waist, shorts, socks, shoes, "
        "legs, feet, knees, thighs, floor or ground."
    ),
    "head": (
        "Tight head-and-shoulders framing, no lower body, hips, waist, shorts, "
        "socks, shoes, legs, feet, knees, thighs, floor or ground."
    ),
}


def closeup_framing_guard(framing: str | None) -> str:
    """非全身景别自动追加 guard；默认留空，由 prompt 显式阻断下半身。"""
    return CLOSEUP_FRAMING_GUARDS.get(str(framing or ""), "")


def expand_matrix(preset: MatrixPreset) -> list[VariantSpec]:
    """matrix 预设 → VariantSpec 列表。

    - 生成顺序 = rules 顺序，每条 rule 内按维度顺序笛卡尔积；
    - "*" = 该维度的全部取值（YAML 声明顺序）；
    - exclude 条目：值必须匹配；值为 "*" 时要求该维度在组合中存在（任意值），
      因此 {angle: back, expression: "*"} 只排除背面的表情组合，不影响背面常规条目。
    """
    dims = preset.dimensions
    specs: list[VariantSpec] = []
    seen: set[str] = set()

    for rule_index, rule in enumerate(preset.rules):
        product = rule.get("product")
        if not isinstance(product, dict):
            raise PresetError(f"matrix 预设 rules[{rule_index}] 缺少 product")
        dim_names: list[str] = []
        value_lists: list[list[str]] = []
        for dim, values in product.items():
            if dim not in dims:
                raise PresetError(f"matrix 预设规则引用未知维度：{dim}")
            if values == "*":
                values = list(dims[dim].keys())
            else:
                values = [str(v) for v in values]
                unknown = [v for v in values if v not in dims[dim]]
                if unknown:
                    raise PresetError(f"matrix 预设维度 {dim} 不包含取值：{unknown}")
            dim_names.append(dim)
            value_lists.append(values)

        dim_names_tuple = tuple(dim_names)
        for combo in itertools.product(*value_lists):
            combo_map = dict(zip(dim_names_tuple, combo))
            if _excluded(combo_map, preset.exclude):
                continue
            spec_id = "_".join(str(combo_map[d]) for d in dim_names_tuple)
            if spec_id in seen:
                raise PresetError(f"matrix 预设产生重复变体 id：{spec_id}")
            seen.add(spec_id)
            text_parts = [dims[d][combo_map[d]] for d in dim_names_tuple]
            text = preset.join.join(text_parts)
            if preset.end:
                text += preset.end
            specs.append(
                VariantSpec(
                    id=spec_id,
                    framing=combo_map.get("framing"),
                    angle=combo_map.get("angle"),
                    expression=combo_map.get("expression"),
                    text=text,
                    tags=[str(combo_map[d]) for d in dim_names_tuple],
                )
            )
    return specs


def _excluded(combo: dict[str, str], excludes: list[dict[str, Any]]) -> bool:
    """exclude 条目：所有键都要命中才排除；"*" 表示该维度存在即可（任意值）。"""
    for entry in excludes:
        if all(
            (dim in combo) and (want == "*" or combo[dim] == want)
            for dim, want in entry.items()
        ):
            return True
    return False


def count_matrix_variants(preset: MatrixPreset) -> int:
    return len(expand_matrix(preset))


def normalize(text: str) -> str:
    """合并多余空格；句号后有词就补空格；去掉 ".."；首尾 trim。"""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\.(?=[A-Za-z])", ". ", text)
    text = re.sub(r"\,(?=[A-Za-z])", ", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def make_run_id(subject_id: str, preset_name: str, seed: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"{subject_id}_{preset_name}_s{seed}_{stamp}"


def _apply_sampling(specs: list[VariantSpec], req: GenerateRequest) -> list[VariantSpec]:
    if req.count is None:
        return specs
    if req.count <= 0:
        raise ValueError("count 必须为正整数或 None")
    if req.count >= len(specs):
        return specs
    if req.sampling == "first":
        return specs[: req.count]
    if req.sampling == "random":
        rng: Random = rng_for(req.seed, "sampling")
        return rng.sample(specs, req.count)
    return specs[: req.count]  # "all" / 未知值：截前 N 条


def _render_suffix(template: str, background: str | None, lighting: str | None) -> str:
    """按句子拆 suffix 模板：缺背景/缺光照时整句省略，而不是输出空残句。

    默认 suffix_template = "Put them against a {background} background. The lighting is {lighting}."
    - 两者都有 → 完整输出；
    - 只有背景 → 省略 "The lighting is …" 句；
    - 都没有 → 空串（suffix 整个省略）。
    """
    if not (background or lighting):
        return ""
    sentences = re.split(r"(?<=\.)\s+", template.strip()) if template.strip() else []
    parts: list[str] = []
    for sentence in sentences:
        if "{background}" in sentence and not background:
            continue
        if "{lighting}" in sentence and not lighting:
            continue
        rendered = render(
            sentence,
            {"background": background or "", "lighting": lighting or ""},
        )
        if rendered.strip():
            parts.append(rendered.strip())
    return " ".join(parts)


def generate(subject: SubjectProfile, preset: Preset, req: GenerateRequest) -> GenerateResult:
    if isinstance(preset, ListPreset):
        specs = list(preset.variants)
    elif isinstance(preset, MatrixPreset):
        specs = expand_matrix(preset)
    else:  # pragma: no cover - 类型系统已约束
        raise PresetError(f"不支持的预设类型：{type(preset)}")
    if not specs:
        raise PresetError(f"预设 {preset.name} 没有任何变体")
    specs = _apply_sampling(specs, req)

    master_raw = render(preset.master_template, subject_mapping(subject))
    items: list[PromptItem] = []

    for i, spec in enumerate(specs):
        rng = rng_for(req.seed, spec.id)
        background = req.fixed_background or (
            rng.choice(preset.backgrounds) if req.randomize_background and preset.backgrounds else None
        )
        lighting = req.fixed_lighting or (
            rng.choice(preset.lighting) if req.randomize_lighting and preset.lighting else None
        )

        variant_raw = render(spec.text, {"subject_noun": subject.subject_noun, "name": subject.name})
        variant_text, _ = expand_inline(variant_raw, rng)
        master = normalize(expand_inline(master_raw, rng)[0])

        suffix = _render_suffix(preset.suffix_template, background, lighting)
        if suffix:
            suffix = expand_inline(suffix, rng)[0]

        framing_guard = closeup_framing_guard(spec.framing)
        prompt = normalize(
            req.separator.join(
                part for part in (master, variant_text, framing_guard, suffix) if part.strip()
            )
        )
        caption = build_caption(subject, spec, background, lighting, preset.caption_tags)
        items.append(
            PromptItem(
                index=i,
                variant_id=spec.id,
                prompt=prompt,
                master=master,
                variant_text=variant_text,
                background=background,
                lighting=lighting,
                caption=caption,
                meta={
                    "seed": req.seed,
                    "subseed": seed_for(req.seed, spec.id),
                    "framing": spec.framing,
                    "angle": spec.angle,
                    "expression": spec.expression,
                    "tags": spec.tags,
                    "sampling": req.sampling,
                    "preset_type": preset.type,
                    "rear_angle": spec.angle in REAR_ANGLES if spec.angle else False,
                },
            )
        )

    return GenerateResult(
        run_id=make_run_id(subject.id, preset.name, req.seed),
        subject_id=subject.id,
        preset=preset.name,
        seed=req.seed,
        items=items,
    )
