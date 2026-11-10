"""配方 ``scene_design`` 步：LLM 按「动作描述 + 服饰」设计一段环境/背景/光线。

产出是一段中文场景文本（30–150 字），经 ``pose_prompt.build_prompt(background_text=…)``
替换固定的「背景简洁，突出角色。」句 —— 这是配方侧的 LLM 提示词优化通路；CLI/网页
不传 scene 时行为不变。目前没有场景库（presets/wildcards 是随机短句池，不是
语义匹配）—— 场景文本由 LLM 依据动作与服饰现配，产出落 run manifest 可查可改。

LLM 链与其它环节一致：请求显式 model > DB 设置（settings_service.llm_overrides，
调用方传入）> env。llm.chat_json 出 {"scene": …}。
"""
from __future__ import annotations

import re

from charvar import llm

_CJK_RE = re.compile(r"[一-鿿]")
# 场景段边界：短了没信息量，长了稀释动作段（服装段 ≤200 同量级；实测 LLM 偶发 150+，
# 上限放宽到 220，brief 里同时要求 40–120 字）
_MIN_LEN, _MAX_LEN = 30, 220

_SCHEMA = {
    "type": "object",
    "properties": {"scene": {"type": "string"}},
    "required": ["scene"],
    "additionalProperties": False,
}


def _brief(
    pose_text: str,
    pose_title: str,
    category: str,
    costume_text: str,
    hint: str,
    script_text: str = "",
) -> str:
    costume_line = f"- 人物服饰：{costume_text}\n" if costume_text else ""
    hint_line = f"- 设计方向（用户提示）：{hint}\n" if hint else ""
    script_line = (
        f"\n【视频脚本（已定稿，首帧画面从这些拍点开始）】\n{script_text}\n"
        if str(script_text or "").strip()
        else ""
    )
    return (
        "你是画面氛围设计师。给一张即将生成的 AI 图片设计「环境/背景/光线」。\n"
        "这张图：一个人物居中的单人构图，人物正摆出下面的动作：\n"
        f"【动作描述】\n{pose_text}\n"
        f"【动作】{pose_title}"
        + (f"（{category}）" if category else "")
        + "\n"
        + costume_line
        + hint_line
        + script_line
        + "\n输出 JSON：{\"scene\": \"背景与光线描述\"}\n"
        "要求：\n"
        "1. 只写 环境/地点/时间天气/背景物件 与 光照（自然光或人工光的主方向、软硬、"
        "色温、明暗对比、氛围），2–3 句成文、40–120 字，不要写成长段落；\n"
        "2. 场景要与动作和服饰合理相称（坐/卧姿给可靠的支撑面，礼服别配工地，运动装"
        "别配晚宴厅），但不给前景加会遮挡或抢视线的复杂物件；\n"
        "3. 有【视频脚本】材料时以脚本为最高依据：脚本拍点涉及的舞台、道具与其方位"
        "必须在场景里装得下，别引入脚本之外的画面事件；\n"
        "4. 不写人物的外观（发型/发色/服装/身材/表情）、不写人数、不写姿势动作（那些由"
        "其它段落定）、不写画风/相机参数/文字水印；\n"
        "5. 光线必须能照亮人物主体，不让主要光落在人物背后造成剪影。"
    )


def validate_scene(text: str) -> str:
    """校验并清理场景文本；不合格抛 ValueError。"""
    text = str(text or "").strip().strip("“”\"' ")
    text = re.sub(r"^(场景设计|背景与光线|场景)[:：]\s*", "", text)
    if not _CJK_RE.search(text):
        raise ValueError("场景描述没有中文内容")
    if not _MIN_LEN <= len(text) <= _MAX_LEN:
        raise ValueError(f"场景描述长度不合适：{len(text)} 字（限 {_MIN_LEN}–{_MAX_LEN}）")
    lowered = text.lower()
    if "<image" in lowered or "image1" in lowered:
        raise ValueError("场景描述不该引用参考图槽位")
    return text


def _call_llm(messages: list[dict], model: str | None, config: dict | None = None) -> dict:
    """单次 LLM 调用（集中在此便于测试替换）；JSON 输出 {"scene": …}。"""
    return llm.chat_json(messages, model=model, config=config, schema=_SCHEMA, schema_name="scene")


def design_scene(
    *,
    pose_text: str,
    pose_title: str = "",
    category: str = "",
    costume_text: str = "",
    hint: str = "",
    script_text: str = "",
    model: str | None = None,
    config: dict | None = None,
) -> dict:
    """设计场景；返回 {"scene": str}。LLM 失败/输出不合法抛 LLMError/ValueError。"""
    messages = [
        {
            "role": "user",
            "content": _brief(
                str(pose_text or "").strip(), str(pose_title or ""), str(category or ""),
                str(costume_text or ""), str(hint or ""), str(script_text or ""),
            ),
        }
    ]
    data = _call_llm(messages, model, config)
    scene = validate_scene(str((data or {}).get("scene") or ""))
    return {"scene": scene}