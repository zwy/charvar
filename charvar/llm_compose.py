"""LLM 生成优化模式（需求 §生成优化）：批量改写 30 条变体提示词。

- 与 composer.generate 平行的新模式：LLM 结果完全替代 master_template 拼接，
  经典模式不受影响；
- 背景与光照采样复用同一子种子规则 rng_for(seed, spec.id)（composer.generate 同款），
  保证同 seed 可复现；
- 产物仍是标准 GenerateResult，正常落 runs 表 → 历史记录 / 导出 / 图片墙全部照常。
"""
from __future__ import annotations

import json
import sqlite3

from charvar import llm
from charvar import outfit as outfit_service
from charvar.caption import build_caption
from charvar.composer import (
    _apply_sampling,
    aspect_meta,
    closeup_framing_guard,
    expand_matrix,
    make_run_id,
    normalize,
    render,
)
from charvar.llm_prompts import REWRITE_SCHEMA, build_rewrite_messages
from charvar.models import GenerateRequest, GenerateResult, PromptItem, SubjectProfile
from charvar.preset_loader import ListPreset, MatrixPreset, Preset, PresetError
from charvar.sticker_policy import (
    is_sticker_preset,
    llm_response_bans,
    render_lettering_block,
    sticker_meta,
)
from charvar.wildcard import expand_inline, rng_for, seed_for

_REWRITE_MAX_TOKENS: int | None = None
# None = 请求不传 max_tokens，输出长度交由模型自身上限（q21_v2_30 + 4 参考图时
# 30 条全批输出约 1.5 万 token，固定 12000 会截断 JSON）。
# 截断兜底见 _salvage_variants / 缺条补调；若仍偶发失败，可退回分批：
# rewrite_prompts(..., max_per_call=10)。


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
        outfit_text: str | None = None
        if spec.outfit_text:
            # 服装组合：动作段与服装段分列。合并文本里服装段夹在动作文本尾部时，
            # 模型会把它当成「参考图自带服装」整段丢弃（实测 LLM 模式 24/24 条丢服装），
            # 所以动作段单独喂、服装段走独立字段 + 显式规则 + 程序兜底补写。
            pose_raw = render(
                spec.action_text if spec.action_text is not None else spec.text,
                {"subject_noun": subject.subject_noun, "name": subject.name},
            )
            pose_text, _ = expand_inline(pose_raw, rng)
            outfit_raw = render(
                spec.outfit_text, {"subject_noun": subject.subject_noun, "name": subject.name}
            )
            outfit_text, _ = expand_inline(outfit_raw, rng)
            variant_text = pose_text
        intent = {
            "id": spec.id,
            "text": variant_text,
            "framing": spec.framing,
            "angle": spec.angle,
            "expression": spec.expression,
            "pose": spec.pose,
            "background": background,
            "lighting": lighting,
            "framing_guard": closeup_framing_guard(spec.framing) or None,
        }
        if outfit_text:
            intent["outfit"] = outfit_text
            intent["outfit_label"] = spec.outfit_label
        if is_sticker_preset(preset):
            # 只带策略标签（英文）进 LLM 输入；display_text/lettering 由程序按原文附加，
            # 不进 intents（LLM 不翻译、不改写、不复制中文文案）
            intent["purpose"] = preset.purpose
            intent["text_policy"] = preset.text_policy
            intent["composition_policy"] = preset.composition_policy
        intents.append(intent)
    return intents


def _salvage_variants(text: str) -> dict:
    """从被截断的响应文本中抢救已完整的 variant 条目（{"variants": [...] 形式）。

    JSON 断在字符串中间时整体解析失败，但数组里已经写完的对象可以逐个
    raw_decode 捞回，返回值交给缺条补调补齐剩下的。
    """
    entries: list[dict] = []
    pos = text.find('"variants"')
    if pos >= 0:
        decoder = json.JSONDecoder()
        while True:
            brace = text.find("{", pos)
            if brace < 0:
                break
            try:
                obj, end = decoder.raw_decode(text, brace)
            except json.JSONDecodeError:
                break  # 从这里开始是被截断的残段
            pos = end
            if isinstance(obj, dict) and isinstance(obj.get("id"), str) and isinstance(obj.get("prompt"), str):
                entries.append({"id": obj["id"], "prompt": obj["prompt"]})
    return {"variants": entries}


def _rewrite_once(
    subject: SubjectProfile,
    template_md: str,
    refs: list[dict],
    instructions: str | None,
    chunk: list[dict],
    rewritten: dict[str, str],
    config: dict | None,
    *,
    text_policy: str = "forbid",
    composition_policy: str = "legacy",
    language: str = "any",
    rejected: dict[str, list[str]] | None = None,
) -> None:
    """改写一批 intent 并把 {id: prompt} 并入 rewritten；截断时先抢救半截 JSON。

    required_label（sticker）：LLM 输出若自带绘字相关内容（禁字句/中文/绘字指令），
    该条不被收录并记入 rejected → 视同缺失交补调重试；不能全局替换后蒙混过关。

    language：改写输出的语言（zh/en/any），透传给提示词构造。
    """
    messages = build_rewrite_messages(
        subject, template_md, refs, chunk, instructions,
        text_policy=text_policy, composition_policy=composition_policy,
        language=language,
    )
    try:
        data = llm.chat_json(
            messages,
            max_tokens=_REWRITE_MAX_TOKENS,
            schema=REWRITE_SCHEMA,
            schema_name="rewrite_variants",
            config=config,
        )
    except llm.LLMParseError as exc:
        # 输出被截断：整体 JSON 解析失败，先抢救已完整的条目
        data = _salvage_variants(exc.text)
    variants = data.get("variants")
    if not isinstance(variants, list):
        raise llm.LLMError(f"LLM 输出缺少 variants 列表：{str(data)[:200]}")
    for entry in variants:
        if not isinstance(entry, dict):
            continue
        variant_id = str(entry.get("id") or "")
        prompt = str(entry.get("prompt") or "").strip()
        if not (variant_id and prompt):
            continue
        if text_policy == "required_label":
            bans = llm_response_bans(prompt)
            if bans:
                if rejected is not None:
                    rejected[variant_id] = bans
                continue
        rewritten[variant_id] = prompt


def rewrite_prompts(
    subject: SubjectProfile,
    template_md: str,
    intents: list[dict],
    ref_kinds: list[str],
    instructions: str | None = None,
    *,
    config: dict | None = None,
    max_per_call: int = 30,
    text_policy: str = "forbid",
    composition_policy: str = "legacy",
    language: str = "any",
) -> dict[str, str]:
    """一次（或分批）chat_json 调用，返回 {variant_id: prompt}。

    请求 json_schema（strict）结构化输出，供应商不支持时 chat() 自动降级
    json_object；max_tokens 不设上限，避免 30 条全批被截断。
    单批有缺失/截断/被拒（sticker 禁字句）时，用缺失的 id 缩批补调一次；
    仍缺失 → LLMError（附被拒原因，可定位）。
    校验：每个 intent 的 id 都必须出现且 prompt 非空。
    ref_kinds 按顺序分配 <image1>、<image2>… 标签。
    """
    if not intents:
        raise llm.LLMError("没有可改写的变体")
    refs = [{"kind": kind, "tag": f"<image{i + 1}>"} for i, kind in enumerate(ref_kinds)]
    rewritten: dict[str, str] = {}
    rejected: dict[str, list[str]] = {}
    chunks = [intents[i : i + max_per_call] for i in range(0, len(intents), max_per_call)]
    for chunk in chunks:
        common = {
            "text_policy": text_policy,
            "composition_policy": composition_policy,
            "language": language,
            "rejected": rejected,
        }
        _rewrite_once(subject, template_md, refs, instructions, chunk, rewritten, config, **common)
        missing = [item for item in chunk if not rewritten.get(item["id"])]
        if not missing:
            continue
        # 缺条补调一次：只带缺失条目再调用（批次更小，输出更短，命中率高）
        _rewrite_once(subject, template_md, refs, instructions, missing, rewritten, config, **common)
        still_missing = [item["id"] for item in missing if not rewritten.get(item["id"])]
        if still_missing:
            detail = ""
            if rejected:
                detail = "；被拒原因：" + "；".join(
                    f"{vid}（{('、'.join(bans))[:80]}）" for vid, bans in sorted(rejected.items()) if vid in still_missing
                )
            raise llm.LLMError(
                f"LLM 输出缺少变体提示词：{still_missing[:5]}（共缺 {len(still_missing)} 条）{detail}"
            )
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
    """LLM 模式入口：改写提示词并组装标准 GenerateResult（落 runs 表）。

    sticker（required_label）：改写结果先 normalize，再由共享编译器按 spec 的
    display_text 原文追加唯一 lettering block（与 classic 相同，中文不改写）。

    llm_language（zh/en/any）：改写输出的语言。sticker 强制英文——中文文案由
    程序按 display_text 原文追加，LLM 段一旦输出中文就会被 sticker 禁字规则判为
    不合格（反复补调也救不回来），所以「中文」在 sticker 下被忽略并给出声明。
    """
    sticker = is_sticker_preset(preset)
    intents = collect_intent(subject, preset, req)
    warnings: list[str] = []
    language = str(getattr(req, "llm_language", "any") or "any")
    if sticker and language == "zh":
        language = "en"
        warnings.append(
            "带字表情包预设：中文文案由程序按原文追加，LLM 改写段不能输出中文 —— "
            "已忽略「提示词语言＝中文」，本支按英文改写。"
        )
    rewritten = rewrite_prompts(
        subject,
        template_md,
        intents,
        reference_kinds,
        req.llm_instructions,
        config=config,
        text_policy=getattr(preset, "text_policy", "forbid"),
        composition_policy=getattr(preset, "composition_policy", "legacy"),
        language=language,
    )
    items: list[PromptItem] = []
    missing_outfit: list[str] = []
    for i, intent in enumerate(intents):
        spec = _spec_by_id(preset, intent["id"])
        prompt = normalize(rewritten[intent["id"]])
        outfit_text = str(intent.get("outfit") or "").strip()
        if outfit_text and not outfit_service.outfit_covered(prompt, outfit_text):
            # 兜底：模型没把这套衣服写进提示词 → 由程序按原文补写（插在背景/光照句前）。
            prompt = outfit_service.insert_outfit_sentence(prompt, outfit_text)
            missing_outfit.append(spec.id)
        if sticker:
            prompt = req.separator.join(
                part
                for part in (
                    prompt,
                    render_lettering_block(spec.display_text, preset.lettering),
                )
                if part.strip()
            )
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
                    "pose": spec.pose,
                    "tags": spec.tags,
                    "sampling": req.sampling,
                    "preset_type": preset.type,
                    "outfit": spec.outfit,
                    "outfit_label": spec.outfit_label,
                    "mode": "llm",
                    "reference_kinds": list(reference_kinds),
                    **aspect_meta(preset),
                    **sticker_meta(preset, spec),
                },
            )
        )
    if missing_outfit:
        warnings.append(
            f"{len(missing_outfit)}/{len(intents)} 条变体的服装描述没在 LLM 改写结果里体现，"
            "已由程序按服装预设原文补写（服装以原文为准）。变体："
            + "、".join(missing_outfit[:5])
            + ("…" if len(missing_outfit) > 5 else "")
        )
    return GenerateResult(
        run_id=make_run_id(subject.id, preset.name, req.seed),
        subject_id=subject.id,
        preset=preset.name,
        seed=req.seed,
        items=items,
        warnings=warnings,
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
