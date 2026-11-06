"""配方 ``video_script`` 步：LLM 给首帧写一段「直出视频提示词」（video_model 步用）。

输入全部来自同一套描述链（动作描述 / 服饰要点 / 场景文本）—— 首帧就是这套链出图
的，脚本**默认文本模式不传图**（不用视觉模型，慢且不必）。成稿结构对齐 dance 系列
配方的人工提示词（画面连续性 / 镜头 / 动作整体 / 分秒动作顺序 / 衔接 / 避免出现）。

看图模式（``write_script(image_path=…)``）：把**真实首帧图**（combo_image 步产物）
随消息带给视觉 LLM，brief 换「以附图实际姿态接续」措辞 —— 首帧渲染与描述链有偏差
时编舞锚在真实画面上（舞蹈变体 random_dance_video 即此用法）。

大幅度档（``amplitude="large"``）：规则 6 换成大幅版（跳跃/转体/甩头/大幅挥臂），并
追加编排结构规则（起势 → 主段逐段升级 → 收尾定格），正文其余字节不变 —— 舞蹈变体
配方即此用法；normal 档保持原 brief（向后兼容）。

多镜头 MV 模式（``write_script(multishot=True)``）：编号镜头 + 每镜起始秒切点 + 机位
运镜的分镜结构，正文后附【实录声场】【配乐】两段（支持音频生成的模型照此出声，
不支持的作氛围描述读取）；``ensemble`` = 伴舞从第 2 镜入画、``shots`` = 镜头数
（缺省按时长推）—— 随机 MV 配方 `random_mv_video` 即此用法。

片段类型的随机不走 LLM：类型池 ``DEFAULT_TYPES``（配方 YAML ``types:`` 可覆盖），
``wildcard.rng_for`` 子种子抽取注入 brief —— 掷骰可复现，LLM 只负责把类型编成
动作脚本。

LLM 链与其它环节一致：请求显式 model > DB 设置（调用方传入 config）> env。
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

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
    amplitude: str = "normal",
    image_attached: bool = False,
) -> str:
    scene_line = f"【场景与光线（来自场景设计步，保持与首帧一致）】\n{scene_text}\n" if scene_text else ""
    if image_attached:
        header = (
            f"你是短视频动作编剧。写一段 {duration} 秒 AI 生成视频的「直出视频提示词」。"
            "视频的首帧画面：已附的这张图片，你的提示词会连同这张首帧图一起发给"
            "视频模型，从图中的姿态开始动起来。\n\n"
        )
        connect_rule = (
            "4. 动作顺序必须从附图首帧的实际姿态直接接续 —— 先承接图中人物当下的躯干朝向、"
            "手臂位置、重心与视线，0 秒就是图中的姿态，"
            "不要先摆好姿势再开始。\n"
        )
    else:
        header = (
            f"你是短视频动作编剧。写一段 {duration} 秒 AI 生成视频的「直出视频提示词」。"
            "视频的首帧画面已经存在（另一张已生成的图），你的提示词会连同首帧一起发给"
            "视频模型，从首帧开始动起来。\n\n"
        )
        connect_rule = (
            "4. 动作顺序必须从首帧姿态直接接续 —— 首帧是什么姿势，0 秒就是什么姿势，"
            "不要先摆好姿势再开始。\n"
        )
    if amplitude == "large":
        pose_rule = (
            "5. 按起始姿态合理设计：站姿类可以做该类型相关的节奏动作，跳跃、跨步、转体都是"
            "从站姿自然可达的大位移；坐/卧/俯身/倚靠类只做该姿态下自然可达的连贯动态"
            "（缓慢伸展、转头、重心挪动、手部局部动作），"
            "不要编排起立、落地之类脱离起始姿态的动作。\n"
        )
        amp_rules = (
            "6. 动作幅度大：允许并鼓励跳跃、转体、甩头、跨步与大幅挥臂，躯干与四肢全程"
            "参与，面部表情随节奏自然变化；四肢与手指结构连贯清楚，动作连续合理，"
            "不做镜头前的怪异扭曲。\n"
            "7. 编排结构：起势——0 秒从首帧姿态直接带出；主段——2–3 组动作主题依次展开，"
            "幅度逐段升级，头部、肩、手臂、腰胯、腿与脚步轮换参与，不整段重复同一动作；"
            "收尾——回到镜头前自然亮相定格，在动作余韵中收束。"
        )
    else:
        pose_rule = (
            "5. 按起始姿态合理设计：站姿类可以做该类型相关的节奏动作；坐/卧/俯身/倚靠类"
            "只做该姿态下自然可达的连贯动态（缓慢伸展、转头、重心挪动、手部局部动作），"
            "不要编排站起来、跳跃之类脱离起始姿态的大位移。\n"
        )
        amp_rules = "6. 动作幅度适中，四肢与手指结构连贯清楚，不做镜头前的怪异扭曲。"
    return (
        header
        + f"【首帧内容·动作描述】\n{pose_text}\n"
        + f"【动作】{pose_title}" + (f"（{category}）" if category else "") + "\n"
        + (f"【人物服饰要点】\n{costume_text}\n" if costume_text else "")
        + scene_line
        + f"【本段视频类型】{clip_type}\n"
        + "\n输出要求（直接输出提示词正文，中文，不要标题、不要任何解释）：\n"
        + f"1. 开头一句：以输入首帧为唯一视觉依据，生成一段约{duration}秒的单人{clip_type}视频。\n"
        + "2. 分节成文，每节一小段落：画面连续性 / 镜头 / 动作整体 / 动作顺序 / 动作衔接 / "
        + "避免出现。「动作顺序」必须给分段时轴（0—X 秒…共"
        + f"{duration}秒），每段写清身体与四肢的连续动作，前一段自然带出后一段。\n"
        + "3. 硬规则：全程同一名人物、同发型同服装；背景与光线全程与首帧一致；基本固定"
        + "机位一镜到底（最多轻微缓推）；人物始终留在画面内；结尾在自然的动作余韵中"
        + "收束，不要突然僵住；不出现第二个人或动物、不切镜头、不换背景、不出现文字水印。\n"
        + connect_rule
        + pose_rule
        + amp_rules
    )


def _resolve_shots(duration: int, shots: int | None) -> int:
    """镜头数：给定点用给定值；缺省按时长推（≥12→4 / ≥8→3 / 其余→2）。"""
    if shots is not None:
        return int(shots)
    if duration >= 12:
        return 4
    if duration >= 8:
        return 3
    return 2


def _brief_multishot(
    pose_text: str,
    pose_title: str,
    category: str,
    costume_text: str,
    scene_text: str,
    duration: int,
    clip_type: str,
    shots_count: int,
    amplitude: str = "normal",
    ensemble: bool = False,
    image_attached: bool = False,
) -> str:
    """多镜 MV brief：编号镜头+分秒切点+机位运镜，正文后附【实录声场】【配乐】两段。"""
    scene_line = (
        f"【场景与光线（来自场景设计步，全程一致）】\n{scene_text}\n" if scene_text else ""
    )
    if image_attached:
        first_frame = "已附的这张图片——第 1 镜的开场画面，0 秒就是图中的姿态"
    else:
        first_frame = "另一张已生成的首帧图（随提示词一起发给视频模型）——第 1 镜的开场画面"
    if ensemble:
        cast_rule = (
            "4. 伴舞安排：从第 2 镜开始伴舞入画，人数 3–5 名，穿与首席同色系的简化服装、"
            "与首席保持明显身形区分；动作与首席同步呼应、阵型对称合理并始终让出首席的中心"
            "位置，不遮挡、不顶替首席。\n"
        )
    else:
        cast_rule = "4. 全程只有首席一人出镜，任何镜头都不出现第二个人或动物。\n"
    if amplitude == "large":
        amp_rule = (
            "5. 动作幅度大：允许并鼓励跳跃、转体、甩头、跨步与大幅挥臂，躯干与四肢全程参与，"
            "面部表情随节奏自然变化；四肢与手指结构连贯清楚，不做镜头前的怪异扭曲。\n"
        )
    else:
        amp_rule = (
            "5. 动作幅度适中，四肢与手指结构连贯清楚，不做镜头前的怪异扭曲。\n"
        )
    return (
        f"你是 MV 导演兼视频编剧。写一段 {duration} 秒、{shots_count} 个镜头的 AI 生成视频"
        "「直出视频提示词」，采用多镜头 MV 分镜结构。\n"
        f"视频的首帧画面：{first_frame}；后续镜头是脚本切换出的新构图，由视频模型按分镜执行。\n\n"
        + f"【首席·动作描述】\n{pose_text}\n"
        + f"【首席·动作】{pose_title}" + (f"（{category}）" if category else "") + "\n"
        + (f"【首席·服饰要点】\n{costume_text}\n" if costume_text else "")
        + scene_line
        + f"【本支 MV 风格】{clip_type}\n"
        + "\n输出要求（直接输出提示词正文，中文，不要标题、不要任何解释）：\n"
        + f"1. 开头一句：以输入首帧为唯一视觉依据，生成一段约{duration}秒、{shots_count}个镜头的{clip_type}MV 视频。\n"
        + "2. 分镜编号成文（镜头一／镜头二……）：每镜写明起始秒（第几秒起，切点均匀排满"
        + f"{duration}秒）、镜头语言（从特写、中景、全景、低位仰拍、跟拍后退、横摇、推近、环绕"
        + "中选用）与该镜的画面内容及编舞细节；第 1 镜 0 秒从首帧姿态直接接续，不要先摆好姿势"
        + "再开始；后续镜头切换机位但仍在同一场景中，动作在切镜点前后保持衔接感。\n"
        + "3. 硬规则：全程同一首席——同发型同服装同妆容，每个镜头里首席都是画面焦点，不混入"
        + "人群失去主体；切镜不换背景与光线；不出现文字水印；结尾在动作余韵中收束。\n"
        + cast_rule
        + amp_rule
        + "6. 编舞按首席的起始姿态合理可达，结构上：起势（第 1 镜从首帧姿态带出）→ 主段"
        + "（贯穿各镜的 2–3 组动作主题，幅度逐段升级）→ 收尾（末镜回到镜头前亮相定格，"
        + "动作余韵中收束）。\n"
        + "7. 正文之后按以下两个小段收尾（各 2–3 句）：\n"
        + "【实录声场】写画内实录声音（脚步、呼吸、服装窸窣、现场混响等）；支持音频生成的"
        + "视频模型会按此出声，不支持的模型仅作氛围参考。\n"
        + "【配乐】写画外配乐：曲风、配器要素，以及节奏与编舞动作同步的关系。"
    )


def validate_script(text: str, multishot: bool = False) -> str:
    """校验脚本文本；不合格抛 ValueError。多镜模式另要求分镜结构词。"""
    text = str(text or "").strip().strip("“”\"' ")
    if not _CJK_RE.search(text):
        raise ValueError("视频脚本没有中文内容")
    if not _MIN_LEN <= len(text) <= _MAX_LEN:
        raise ValueError(f"视频脚本长度不合适：{len(text)} 字（限 {_MIN_LEN}–{_MAX_LEN}）")
    if "首帧" not in text:
        raise ValueError("视频脚本没有提到「首帧」—— 连续性依据缺失")
    if multishot and "镜头" not in text:
        raise ValueError("多镜模式视频脚本缺少分镜结构（提到「镜头」）")
    return text


def call_llm(messages: list[dict], model: str | None, config: dict | None = None) -> str:
    """单次 LLM 调用（集中在此便于测试替换）；text 输出，不走 JSON。"""
    return llm.chat(messages, model=model, config=config, json_mode=False)


def pick_clip_type(seed: int, key: str, types: list[str] | None = None) -> str:
    """片段类型：池子可由配方 YAML types 覆盖；子种子抽取（可复现）。"""
    pool = [t for t in (types or []) if str(t or "").strip()] or list(DEFAULT_TYPES)
    return wildcard.rng_for(seed, key).choice(pool)


def _image_data_url(image_path: str | Path) -> str:
    """首帧图 → data URL：长边 ≤1280 JPEG（脚本 LLM 要看的是姿态与构图，不必吃原图）。"""
    from io import BytesIO

    from PIL import Image

    with Image.open(image_path) as img:
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > 1280:
            scale = 1280.0 / long_edge
            img = img.resize(
                (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                Image.LANCZOS,
            )
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


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
    image_path: str | Path | None = None,
    amplitude: str = "normal",
    multishot: bool = False,
    ensemble: bool = False,
    shots: int | None = None,
) -> dict:
    """写视频脚本；返回 {"script", "clip_type"}。LLM 失败/输出不合法抛 LLMError/ValueError。

    image_path 非空 = 看图模式：messages 附上首帧图（PIL 压到长边 ≤1280 再 base64），
    brief 换「以附图实际姿态接续」措辞 —— 配方步传 combo_image 的真实产物进来。
    amplitude="large" = 大幅度档：规则 6 换大幅版并追加编排结构规则；缺省 normal
    保持原 brief 不变（向后兼容）。
    multishot=True = 多镜头 MV 分镜：编号镜头+分秒切点+机位运镜，正文后附【实录声场】
    【配乐】两段；ensemble 配合 multishot 写伴舞条款；shots 给定镜头数（缺省按时长推）。
    """
    if multishot:
        brief = _brief_multishot(
            str(pose_text or "").strip(),
            str(pose_title or ""),
            str(category or ""),
            str(costume_text or ""),
            str(scene_text or ""),
            int(duration),
            str(clip_type or "摆动"),
            _resolve_shots(int(duration), shots),
            amplitude=amplitude,
            ensemble=bool(ensemble),
            image_attached=bool(image_path),
        )
    else:
        brief = _brief(
            str(pose_text or "").strip(),
            str(pose_title or ""),
            str(category or ""),
            str(costume_text or ""),
            str(scene_text or ""),
            int(duration),
            str(clip_type or "摆动"),
            amplitude=amplitude,
            image_attached=bool(image_path),
        )
    if image_path:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": brief},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
                ],
            }
        ]
    else:
        messages = [{"role": "user", "content": brief}]
    text = call_llm(messages, model, config)
    return {"script": validate_script(text, multishot=multishot), "clip_type": clip_type}