"""LLM 生成优化模式（需求 §生成优化）：批量改写 30 条变体提示词。

- 与 composer.generate 平行的新模式：LLM 结果完全替代 master_template 拼接，
  经典模式不受影响；
- 背景与光照采样复用同一子种子规则 rng_for(seed, spec.id)（composer.generate 同款），
  保证同 seed 可复现；
- 产物仍是标准 GenerateResult，正常落 runs 表 → 历史记录 / 导出 / 图片墙全部照常。
"""
from __future__ import annotations

import sqlite3

from charvar import llm
from charvar.caption import build_caption
from charvar.composer import (
    _apply_sampling,
    closeup_framing_guard,
    expand_matrix,
    make_run_id,
    normalize,
    render,
)
from charvar.llm_prompts import build_rewrite_messages
from charvar.models import GenerateRequest, GenerateResult, PromptItem, SubjectProfile
from charvar.preset_loader import ListPreset, MatrixPreset, Preset, PresetError
from charvar.wildcard import expand_inline, rng_for, seed_for

_REWRITE_MAX_TOKENS = 12000


def collect_intent(subject: SubjectProfile, preset: Preset, req: GenerateRequest) -> list[dict]:
    """展开预设变体并采样背景/光照，产出喂给 LLM 的结构化意图列表。"""
    if isinstance(preset, ListPreset):
        specs = list(preset.variants)
    elif isinstance(preset, MatrixPreset):
        specs = expand_matrix(preset)
    else:  # pragma: no cover - 类型系统已约束
        raise PresetError(f"不支持的预设类型：{type(preset)}")
    if not specs:
        raise PresetError(f"预设 {preset.name} 没有任何变体")
    specs = _apply_sampling(specs, req)

    intents: list[dict] = []
    for spec in specs:
        rng = rng_for(req.seed, spec.id)
        background = req.fixed_background or (
            rng.choice(preset.backgrounds) if req.randomize_background and preset.backgrounds else None
        )
        lighting = req.fixed_lighting or (
            rng.choice(preset.lighting) if req.randomize_lighting and preset.lighting else None
        )
        variant_raw = render(spec.text, {"subject_noun": subject.subject_noun, "name": subject.name})
        variant_text, _ = expand_inline(variant_raw, rng)
        intents.append(
            {
                "id": spec.id,
                "text": variant_text,
                "framing": spec.framing,
                "angle": spec.angle,
                "expression": spec.expression,
                "background": background,
                "lighting": lighting,
                "framing_guard": closeup_framing_guard(spec.framing) or None,
            }
        )
    return intents


def rewrite_prompts(
    subject: SubjectProfile,
    template_md: str,
    intents: list[dict],
    ref_kinds: list[str],
    instructions: str | None = None,
    *,
    config: dict | None = None,
    max_per_call: int = 30,
) -> dict[str, str]:
    """一次（或分批）chat_json 调用，返回 {variant_id: prompt}。

    校验：每个 intent 的 id 都必须出现且 prompt 非空，缺失/为空 → LLMError。
    ref_kinds 按顺序分配 <image1>、<image2>… 标签。
    """
    if not intents:
        raise llm.LLMError("没有可改写的变体")
    refs = [{"kind": kind, "tag": f"<image{i + 1}>"} for i, kind in enumerate(ref_kinds)]
    rewritten: dict[str, str] = {}
    chunks = [intents[i : i + max_per_call] for i in range(0, len(intents), max_per_call)]
    for chunk in chunks:
        messages = build_rewrite_messages(subject, template_md, refs, chunk, instructions)
        data = llm.chat_json(messages, max_tokens=_REWRITE_MAX_TOKENS, config=config)
        variants = data.get("variants")
        if not isinstance(variants, list):
            raise llm.LLMError(f"LLM 输出缺少 variants 列表：{str(data)[:200]}")
        for entry in variants:
            if not isinstance(entry, dict):
                continue
            variant_id = str(entry.get("id") or "")
            prompt = str(entry.get("prompt") or "").strip()
            if variant_id and prompt:
                rewritten[variant_id] = prompt
        missing = [i["id"] for i in chunk if not rewritten.get(i["id"])]
        if missing:
            raise llm.LLMError(f"LLM 输出缺少变体提示词：{missing[:5]}（共缺 {len(missing)} 条）")
    return rewritten


def generate_llm(
    subject: SubjectProfile,
    preset: Preset,
    req: GenerateRequest,
    template_md: str,
    reference_kinds: list[str],
    *,
    config: dict | None = None,
) -> GenerateResult:
    """LLM 模式入口：改写 30 条提示词并组装标准 GenerateResult（落 runs 表）。"""
    intents = collect_intent(subject, preset, req)
    rewritten = rewrite_prompts(
        subject,
        template_md,
        intents,
        reference_kinds,
        req.llm_instructions,
        config=config,
    )
    items: list[PromptItem] = []
    for i, intent in enumerate(intents):
        spec = _spec_by_id(preset, intent["id"])
        prompt = normalize(rewritten[intent["id"]])
        caption = build_caption(
            subject, spec, intent["background"], intent["lighting"], preset.caption_tags,
            background_suffix=preset.caption_background_suffix,
        )
        items.append(
            PromptItem(
                index=i,
                variant_id=spec.id,
                prompt=prompt,
                master="(llm 模式：LLM 全量重写)",
                variant_text=intent["text"],
                background=intent["background"],
                lighting=intent["lighting"],
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
                    "mode": "llm",
                    "reference_kinds": list(reference_kinds),
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


def _spec_by_id(preset: Preset, spec_id: str):
    if isinstance(preset, ListPreset):
        pool = list(preset.variants)
    else:
        pool = expand_matrix(preset)
    for spec in pool:
        if spec.id == spec_id:
            return spec
    raise PresetError(f"变体不存在：{spec_id}")  # pragma: no cover - rewrite 已校验
