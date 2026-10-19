"""LLM 提示词模板（需求：生成优化 + 主体图起草）。

两组调用：
- draft_subject_image_prompt：为主体档案 5 种图片起草提示词
  （character_sheet = 文生图；其余 4 种 = 基于三视图参考图的编辑指令）；
- build_rewrite_messages：批量改写变体提示词，句序固定：
  参考图用途 → 目标景别 → 躯干/头部/视线 → 目标表情 → 背景/光照。
只构造 messages，不发起调用；调用方统一走 charvar.llm.chat_json。
"""
from __future__ import annotations

import json

from charvar.models import SubjectProfile
from charvar.subject_images import KIND_LABELS

_TEMPLATE_MAX_CHARS = 24_000

# 各 kind 的起草要求（system prompt 片段）
_KIND_BRIEFS: dict[str, str] = {
    "character_sheet": (
        "输出一条文生图提示词，要求：同一页面上并排呈现人物半身像＋全身三视图"
        "（正面 / 左侧面 / 背面），白色纯色背景，全身光照均匀无强烈阴影；"
        "角色外观、服饰必须与档案及模板中的锁定项完全一致，不添加模板之外的服饰与道具。"
    ),
    "headshot": (
        "输出一条图像编辑指令（基于提供的人物三视图参考图），要求：同一人物的半身像，"
        "正面朝向镜头，中性纯色背景，外貌与参考图完全一致。"
    ),
    "front": (
        "输出一条图像编辑指令（基于提供的人物三视图参考图），要求：同一人物的全身正面站姿，"
        "双脚完整可见，中性纯色背景，外貌与服饰与参考图完全一致。"
    ),
    "side": (
        "输出一条图像编辑指令（基于提供的人物三视图参考图），要求：同一人物的全身侧面站姿"
        "（与参考图侧面视图同方向），双脚完整可见，中性纯色背景，外貌与服饰与参考图完全一致。"
    ),
    "behind": (
        "输出一条图像编辑指令（基于提供的人物三视图参考图），要求：同一人物的全身背面站姿，"
        "双脚完整可见，中性纯色背景，外貌与服饰与参考图完全一致。"
    ),
}


def _clip_template(template_md: str) -> str:
    text = (template_md or "").strip()
    if len(text) > _TEMPLATE_MAX_CHARS:
        return text[:_TEMPLATE_MAX_CHARS] + "\n…（模板过长已截断）"
    return text


def _profile_payload(subject: SubjectProfile) -> str:
    return json.dumps(subject.model_dump(), ensure_ascii=False, indent=1)


def draft_subject_image_prompt_messages(
    subject: SubjectProfile,
    kind: str,
    template_md: str,
) -> list[dict]:
    """为主体图起草提示词的 messages（chat_json → {"prompt": "<英文提示词>"}）。"""
    brief = _KIND_BRIEFS.get(kind)
    if brief is None:
        raise ValueError(f"未知图片类型：{kind}")
    system = (
        "你是 ComfyUI 图像生成提示词专家。根据给定的角色档案与「角色全场景通用提示词模板」，"
        f"为该角色的「{KIND_LABELS.get(kind, kind)}」（{kind}）撰写一条提示词。\n"
        f"- 要求：{brief}\n"
        "- 提示词用英文，可直接用于 Stable Diffusion / Qwen-Image 类模型；"
        "风格与人物描述优先取自模板文档。\n"
        '只输出 JSON：{"prompt": "<英文提示词>"}'
    )
    user = (
        f"【角色档案】\n{_profile_payload(subject)}\n\n"
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（未提供模板）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_rewrite_messages(
    subject: SubjectProfile,
    template_md: str,
    refs: list[dict],
    intents: list[dict],
    instructions: str | None = None,
) -> list[dict]:
    """批量改写变体提示词的 messages（chat_json → {"variants": [{"id","prompt"}...]}）。

    refs: [{"kind": ..., "tag": "<image1>"}, ...]（tag 按顺序分配）；
    intents: collect_intent 的输出（id/text/framing/angle/expression/background/lighting/framing_guard）。
    """
    ref_lines = "\n".join(f"- {r['tag']}：{KIND_LABELS.get(r['kind'], r['kind'])}（{r['kind']}），作为该角色外观/服饰/构图的参考" for r in refs)
    system = (
        "你是角色图像提示词改写器。给定：角色档案、角色全场景通用提示词模板（权威人设依据）、"
        "若干参考图说明（每张有 <imageN> 标签与用途），以及若干条变体的结构化意图。\n"
        "为每条变体重写一条完整英文提示词，每条依次包含（句子级，顺序固定）：\n"
        "1) 参考图的用途——用对应 <imageN> 标签声明该图是人物外观/服饰/构图参考（如 "
        "\"Use <image1> as the identity and outfit reference for the character.\"）；\n"
        "2) 目标景别（framing：full=全身 / chest=胸部以上 / head=头部特写）；\n"
        "3) 躯干/头部/视线关系——按 angle 描述身体朝向、头部朝向与视线方向；\n"
        "4) 目标表情（expression，背面/遮挡视角可省略）；\n"
        "5) 背景/光照——逐字采用给定的 background 与 lighting，不改写。\n"
        "硬性规则：\n"
        "- 严格保持模板与档案中的角色前缀、身份与服饰锁定描述，不得改变外貌设定；\n"
        "- framing 为 chest/head 时必须纳入给定的 framing_guard 句子以阻断下半身；\n"
        "- 只输出提示词本身，不输出任何解释；每条 prompt 为一个英文字符串；\n"
        "- 必须为输入列表中的每个 id 各输出一条，不得增删。\n"
        '只输出 JSON：{"variants": [{"id": "<variant_id>", "prompt": "<english prompt>"}]}'
    )
    user_parts = [
        f"【角色档案】\n{_profile_payload(subject)}",
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（未提供模板）'}",
        f"【参考图说明】\n{ref_lines or '（无参考图）'}",
    ]
    if instructions and instructions.strip():
        user_parts.append(f"【用户附加要求】\n{instructions.strip()}")
    user_parts.append(
        "【变体列表】\n" + json.dumps(intents, ensure_ascii=False, indent=1)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
