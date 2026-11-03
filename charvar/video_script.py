"""配方 ``video_script`` 步：LLM 给首帧写一段「直出视频提示词」（video_model 步用）。

输入全部来自同一套描述链（动作描述 / 服饰要点 / 场景文本）—— 首帧就是这套链出图
的，脚本**默认文本模式不传图**（不用视觉模型，慢且不必）。成稿结构对齐 dance 系列
配方的人工提示词（画面连续性 / 镜头 / 动作整体 / 分秒动作顺序 / 衔接 / 避免出现）。

片段类型的随机不走 LLM：类型池 ``DEFAULT_TYPES``（配方 YAML ``types:`` 可覆盖），
``wildcard.rng_for`` 子种子抽取注入 brief —— 掷骰可复现，LLM 只负责把类型编成
动作脚本。

LLM 链与其它环节一致：请求显式 model > DB 设置（调用方传入 config）> env。
"""
from __future__ import annotations

import re

from charvar import llm, wildcard

_CJK_RE = re.compile(r"[一-鿿]")
# 脚本边界：dance 系列实测 1500–2600 字；短了排不出分秒节拍，长了稀释关键词
_MIN_LEN, _MAX_LEN = 400, 3200

# 片段类型池（配方 YAML types 可覆盖）。写「从姿态可达」的运动族而非具体脚本 ——
# 具体动作编排在 brief 里交给 LLM；坐/卧/俯身等非站姿由 brief 的自适应规则兜住。
DEFAULT_TYPES: tuple[str, ...] = (
    "跟着节奏跳舞",
    "原地律动摇摆",
    "缓慢转体亮相",
    "连续更换摆拍姿势",
    "小幅走位后停住",
    "缓慢伸展舒展身体",
    "回眸与眼神流转",
    "轻轻拨弄头发后定格",
)


def _brief(
    pose_text: str,
    pose_title: str,
    category: str,
    costume_text: str,
    scene_text: str,
    duration: int,
    clip_type: str,
) -> str:
    scene_line = f"【场景与光线（来自场景设计步，保持与首帧一致）】\n{scene_text}\n" if scene_text else ""
    return (
        f"你是短视频动作编剧。写一段 {duration} 秒 AI 生成视频的「直出视频提示词」。"
        "视频的首帧画面已经存在（另一张已生成的图），你的提示词会连同首帧一起发给"
        "视频模型，从首帧开始动起来。\n\n"
        f"【首帧内容·动作描述】\n{pose_text}\n"
        f"【动作】{pose_title}" + (f"（{category}）" if category else "") + "\n"
        + (f"【人物服饰要点】\n{costume_text}\n" if costume_text else "")
        + scene_line
        + f"【本段视频类型】{clip_type}\n"
        "\n输出要求（直接输出提示词正文，中文，不要标题、不要任何解释）：\n"
        f"1. 开头一句：以输入首帧为唯一视觉依据，生成一段约{duration}秒的单人{clip_type}视频。\n"
        "2. 分节成文，每节一小段落：画面连续性 / 镜头 / 动作整体 / 动作顺序 / 动作衔接 / "
        "避免出现。「动作顺序」必须给分段时轴（0—X 秒…共"
        f"{duration}秒），每段写清身体与四肢的连续动作，前一段自然带出后一段。\n"
        "3. 硬规则：全程同一名人物、同发型同服装；背景与光线全程与首帧一致；基本固定"
        "机位一镜到底（最多轻微缓推）；人物始终留在画面内；结尾在自然的动作余韵中"
        "收束，不要突然僵住；不出现第二个人或动物、不切镜头、不换背景、不出现文字水印。\n"
        "4. 动作顺序必须从首帧姿态直接接续 —— 首帧是什么姿势，0 秒就是什么姿势，"
        "不要先摆好姿势再开始。\n"
        "5. 按起始姿态合理设计：站姿类可以做该类型相关的节奏动作；坐/卧/俯身/倚靠类"
        "只做该姿态下自然可达的连贯动态（缓慢伸展、转头、重心挪动、手部局部动作），"
        "不要编排站起来、跳跃之类脱离起始姿态的大位移。\n"
        "6. 动作幅度适中，四肢与手指结构连贯清楚，不做镜头前的怪异扭曲。"
    )


def validate_script(text: str) -> str:
    """校验脚本文本；不合格抛 ValueError。"""
    text = str(text or "").strip().strip("“”\"' ")
    if not _CJK_RE.search(text):
        raise ValueError("视频脚本没有中文内容")
    if not _MIN_LEN <= len(text) <= _MAX_LEN:
        raise ValueError(f"视频脚本长度不合适：{len(text)} 字（限 {_MIN_LEN}–{_MAX_LEN}）")
    if "首帧" not in text:
        raise ValueError("视频脚本没有提到「首帧」—— 连续性依据缺失")
    return text


def call_llm(messages: list[dict], model: str | None, config: dict | None = None) -> str:
    """单次 LLM 调用（集中在此便于测试替换）；text 输出，不走 JSON。"""
    return llm.chat(messages, model=model, config=config, json_mode=False)


def pick_clip_type(seed: int, key: str, types: list[str] | None = None) -> str:
    """片段类型：池子可由配方 YAML types 覆盖；子种子抽取（可复现）。"""
    pool = [t for t in (types or []) if str(t or "").strip()] or list(DEFAULT_TYPES)
    return wildcard.rng_for(seed, key).choice(pool)


def write_script(
    *,
    pose_text: str,
    pose_title: str = "",
    category: str = "",
    costume_text: str = "",
    scene_text: str = "",
    duration: int = 5,
    clip_type: str = "",
    model: str | None = None,
    config: dict | None = None,
) -> dict:
    """写视频脚本；返回 {"script", "clip_type"}。LLM 失败/输出不合法抛 LLMError/ValueError。"""
    messages = [
        {
            "role": "user",
            "content": _brief(
                str(pose_text or "").strip(),
                str(pose_title or ""),
                str(category or ""),
                str(costume_text or ""),
                str(scene_text or ""),
                int(duration),
                str(clip_type or "摆动"),
            ),
        }
    ]
    text = call_llm(messages, model, config)
    return {"script": validate_script(text), "clip_type": clip_type}