"""LLM 视觉起稿：服装条目的文生图提示词（costume_prompts 表，source=ai_draft）。

与 pose_describe 同方法：看条目缩略图 → LLM 起稿这段服装的 t2i 提示词。
- 按条目 display_type 说明展示形态（ghost = 隐形模特陈列；flat_lay = 平铺俯拍）；
- 只写服装与配件（款式/配色/面料/层次/配件位置），不写人物，不写 <imageN>；
- 英文为主（与站点关键词/贴字 caption 同向；文本层另有中文服装段的合法通路）；
- 落库 source="ai_draft"（人工改稿 = charvar costume prompt-text --set）；
- 幂等：已有文本的条目自动跳过（--overwrite 重写）。
图片以 data URL 进 messages；当前配置的 LLM 不支持视觉时调用会失败 ——
用 --model 换一个视觉模型（OpenRouter 上如 gemini/gpt/qwen-vl 系）。
"""
from __future__ import annotations

import base64
import re

from charvar import costumes as costumes_service
from charvar import llm

_IDENT_HINTS = ("<image", "image1", "image2", "image3")

_DISPLAY_HINTS = {
    "ghost": "隐形模特（ghost mannequin）陈列图：没有头和身体，服装保持穿上时的立体形态悬空展示",
    "flat_lay": "服装平铺展示图（flat lay）：从上往下俯拍，衣物分开平放摆整齐",
}


def thumb_data_url(item_spec: str) -> str:
    """条目缩略图 → data URL（webp 缩略小图，几十 KB）。"""
    thumb = costumes_service.image_file(item_spec, "thumb")
    b64 = base64.b64encode(thumb.read_bytes()).decode("ascii")
    return f"data:image/webp;base64,{b64}"


def _instructions(item_id: str, display_type: str, keywords: str) -> str:
    display = _DISPLAY_HINTS.get(display_type, display_type)
    kw = f"站点关键词（方向参考，可在此基础上扩充细节）：{keywords}\n" if keywords else ""
    return (
        f"你是服装参考库的服装描述起草员。给你一张服装库条目图（{item_id}），"
        f"它是{display}。\n"
        "写这段服装的英文 t2i 提示词（生成的就是这种展示形态的服装图）。\n\n"
        "写作规则：\n"
        "1. 只写服装本身：单件/整套的款式、配色、面料质感、图案细节、层次结构与可见"
        "配件（鞋/帽/包等）；位置描述贴合图中陈列形态。\n"
        "2. 不写人物/模特/脸/发型/身体特征，不写任何 <imageN> 引用，不写画幅或背景指令。\n"
        "3. 英文一段成文，30–150 词，可直接整段当作 t2i prompt 使用。\n"
        "4. 与站点关键词同向扩充，不要发明图里没有的服装部件。\n"
        + (kw + "\n" if kw else "")
        + f"\n现在给条目 {item_id} 写提示词。直接输出提示词文本，不要任何解释。\n"
    )


def _validate(text: str) -> str:
    """校验起稿文本；返回清理后的文本，不合格抛 ValueError。"""
    text = str(text or "").strip().strip("“”\"' ")
    text = re.sub(r"^(prompt|提示词)[:：]\s*", "", text)
    if not text:
        raise ValueError("空文本")
    if not 30 <= len(text) <= 900:
        raise ValueError(f"长度不合适：{len(text)} 字符")
    lowered = text.lower()
    if any(h in lowered for h in _IDENT_HINTS):
        raise ValueError("写了 <imageN> 引用 —— t2i 提示词里不该出现")
    return text


def _call_llm(messages: list, model: str | None) -> str:
    """单次视觉调用（集中在此便于测试替换）；text 输出，不走 JSON。"""
    return llm.chat(messages, model=model, json_mode=False)


def describe_one(
    conn,
    ref: str,
    *,
    model: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict:
    """给一个条目起稿；已写且不 overwrite → skipped。"""
    try:
        row = costumes_service.resolve_ref(conn, ref)
    except costumes_service.CostumeError as exc:
        return {"ref": ref, "status": "error", "error": str(exc)}
    source_id, item_id = row["source_id"], row["item_id"]
    current = costumes_service.get_costume_prompt(conn, source_id, item_id)
    if current["prompt_text"].strip() and not overwrite:
        return {"ref": ref, "status": "skipped", "text": current["prompt_text"]}
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _instructions(item_id, row["display_type"], row["keywords"])},
                {"type": "image_url", "image_url": {"url": thumb_data_url(f"{source_id}/{item_id}")}},
            ],
        }
    ]
    draft = _call_llm(messages, model)
    try:
        draft = _validate(draft)
    except ValueError as exc:
        return {"ref": ref, "status": "error", "error": str(exc), "text": draft}
    if dry_run:
        return {"ref": ref, "status": "draft", "text": draft}
    data = costumes_service.set_costume_prompt(conn, source_id, item_id, draft, source="ai_draft")
    return {"ref": ref, "status": "done", "text": data["prompt_text"]}
