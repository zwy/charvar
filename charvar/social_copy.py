"""给一条 AI 视频/图片写「可直接发布的」通用短视频文案（标题 / 简介 / 话题标签）。

入口：web 视频页「最近生成」卡的「AI 文案」按钮 → POST /videos/{run_id}/copy；
产物存进该 run 的 manifest.json（adhoc_video.save_copy），重复点开旧文案不再花钱。

LLM 链与其余环节一致（scene_design.py 同骨架）：config（settings_service.llm_overrides，
调用方传入）> env。llm.chat_json 出 {"title","intro","tags"}，值级清洗集中在 validate_copy。
"""
from __future__ import annotations

import re

from charvar import llm

# 标题硬上限 20 字（用户拍板）；超宽清洗成截断，不因为 LLM 偶发超字直接整单失败
_TITLE_MAX = 20
_TAGS_MAX, _TAG_MAX_LEN = 5, 12
_INTRO_MAX = 200

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "intro", "tags"],
    "additionalProperties": False,
}


def _brief(video_prompt: str) -> str:
    return (
        "你是短视频文案编辑。下面是一段 AI 生成短视频的画面提示词，"
        "给它写一条可直接发布的中文文案（标题 / 简介 / 话题标签）。\n"
        f"【画面提示词】\n{video_prompt}\n"
        "输出 JSON：{\"title\": \"…\", \"intro\": \"…\", \"tags\": [\"…\", …]}\n"
        "要求：\n"
        "1. title：平台标题，最多 20 字，口语化、有钩子（悬念/反差/情绪），"
        "不带引号书名号、不带 # 话题、不写「AI 生成」之类说明；\n"
        "2. intro：简介 2–4 句、80–150 字，可用少量 emoji 点缀，"
        "第一句要能单独当开头，别逐句重复标题；\n"
        "3. tags：话题标签 1–5 个、每个 2–8 字，裸词不带 # 号（前端统一拼）；"
        "与画面内容强相关，其中含 1 个流量向泛标签；\n"
        "4. 全部中文；只输出 JSON。"
    )


def validate_copy(data: dict) -> dict:
    """清洗并限制 LLM 输出；缺 title/intro 抛 ValueError。"""
    data = data or {}
    title = str(data.get("title") or "").strip().strip("“”\"' 「」《》")
    intro = str(data.get("intro") or "").strip()
    if not title:
        raise ValueError("标题为空")
    if not intro:
        raise ValueError("简介为空")
    if len(title) > _TITLE_MAX:
        title = title[:_TITLE_MAX]
    intro = re.sub(r"\s+", " ", intro.replace("\r", ""))
    if len(intro) > _INTRO_MAX:
        intro = intro[:_INTRO_MAX].rstrip() + "…"
    tags: list[str] = []
    for raw in (data.get("tags") if isinstance(data.get("tags"), list) else []):
        tag = str(raw).strip().lstrip("#").strip().strip(" ")
        if tag and tag not in tags:
            tags.append(tag)
    tags = tags[:_TAGS_MAX]
    if not tags:
        tags = [title[:_TAG_MAX_LEN]] if title else ["AIGC"]
    return {"title": title, "intro": intro, "tags": tags}


def generate_copy(*, video_prompt: str, config: dict | None = None) -> dict:
    """生成并清洗文案；返回 {title,intro,tags}。LLM 失败抛 LLMError，输出非法抛 ValueError。"""
    messages = [{"role": "user", "content": _brief(str(video_prompt or "").strip())}]
    data = llm.chat_json(messages, config=config, schema=_SCHEMA, schema_name="social_copy")
    return validate_copy(data)
