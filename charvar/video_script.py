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

拟人贱剧档（``amplitude="skit"``，只接猫）：规则 5–7 换「起手—转折—收尾」剧本三拍版，
双腿拟人身手 —— 贱猫小品配方即此用法。真猫捣乱档（``amplitude="naughty"``，只接猫）：
规则 5–7 换「盯上—得手—装无辜」恶作剧三拍版，四足真猫身手 —— 贱猫捣乱配方即此用法。

安全红线（``safety_text``）：【安全红线（全程一致）】材料段 + 输出要求追加「逐条落进
『避免出现』节」的规则 —— 组合 random_absurd_video 配方即此用法；多镜模式不接（显式报错）。

多镜头 MV 模式（``write_script(multishot=True)``）：编号镜头 + 每镜起始秒切点 + 机位
运镜的分镜结构，正文后附【实录声场】【配乐】两段（支持音频生成的模型照此出声，
不支持的作氛围描述读取）；``ensemble`` = 伴舞从第 2 镜入画、``shots`` = 镜头数
（缺省按时长推）—— 随机 MV 配方 `random_mv_video` 即此用法。

特写开场（``opening="face"``）：第 1 镜保持面部大特写（首帧 = 特写），第 2 镜起切到
全身开始编舞 —— 随机 MV 升级版 `random_mv_video_up` 即此用法。文字特效
（``text_fx`` 配 ``text_pool``）：两处背景大字（中段爆点 + 末镜亮相），词由
``pick_text_words`` 池子掷骰、逐词锁进脚本校验。

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

# 文字特效词池（配方 YAML text_pool 可覆盖）：背景大字从池子里掷一对（可复现），
# 中段爆点与末镜亮相各一处 —— 词交给视频模型渲染，超短大写词出字最稳。
DEFAULT_TEXT_POOL: tuple[str, ...] = ("HIT", "OWN IT", "SHINE", "GO GO", "LOVE")


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
    style_text: str = "",
    safety_text: str = "",
    subject_kind: str = "person",
    appearance_text: str = "",
    crowd: str = "solo",
) -> str:
    is_cat = str(subject_kind or "person") == "cat"
    scene_line = f"【场景与光线（来自场景设计步，保持与首帧一致）】\n{scene_text}\n" if scene_text else ""
    style_line = f"【全局质感（全程一致）】\n{style_text}\n" if style_text else ""
    safety_line = f"【安全红线（全程一致）】\n{safety_text}\n" if safety_text else ""
    appearance_line = (
        f"【主角·外观（与首帧是同一只猫）】\n{appearance_text}\n"
        if is_cat and str(appearance_text or "").strip()
        else ""
    )
    if is_cat:
        if image_attached:
            header = (
                f"你是短视频动作编剧。写一段 {duration} 秒 AI 生成视频的「直出视频提示词」。"
                "视频的主角是一只猫；视频的首帧画面：已附的这张图片（图中就是这只猫），"
                "你的提示词会连同这张首帧图一起发给视频模型，从图中的姿态开始动起来。\n\n"
            )
            connect_rule = (
                "4. 动作顺序必须从附图首帧的实际姿态直接接续 —— 先承接图中猫当下的躯干朝向、"
                "四肢位置、重心与视线，0 秒就是图中的姿态，"
                "不要先摆好姿势再开始。\n"
            )
        else:
            header = (
                f"你是短视频动作编剧。写一段 {duration} 秒 AI 生成视频的「直出视频提示词」。"
                "视频的主角是一只猫；视频的首帧画面已经存在（另一张已生成的猫图），"
                "你的提示词会连同首帧一起发给视频模型，从首帧开始动起来。\n\n"
            )
            connect_rule = (
                "4. 动作顺序必须从首帧姿态直接接续 —— 首帧猫是什么姿态，0 秒就是什么姿态，"
                "不要先摆好姿势再开始。\n"
            )
    elif image_attached:
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
    if amplitude == "skit":
        if not is_cat:
            raise ValueError("amplitude=skit 目前只接猫版拟人剧本（subject_kind=cat）")
        pose_rule = (
            "5. 按起始姿态合理设计：像人一样双腿直立是这只猫的常态，双臂、前爪、腰胯、"
            "头部与尾巴都做拟人表达；首帧里出现的道具（瓶、煎饼、书、毯子、蜡烛等）"
            "全程由它爪中或嘴边按脚本流转，不凭空消失、不换道具；小段四足跑动可以穿插，"
            "跑完接回拟人姿态继续演。\n"
        )
        amp_rules = (
            "6. 这是拟人喜剧小品，按剧本三拍演：起手蓄势要充分（假动作要做足真要做的样子）；"
            "转折要突然干脆（该停的稳稳停住、该缩的闪电缩回，转折点正是笑点）；表情戏贯穿"
            "全程（挑眉、瞪眼、贱笑、无辜脸、得意下巴），动作幅度大但身体与道具毫发无损。\n"
            "7. 编排结构：起手——0 秒从首帧姿态直接带出起手段；转折——中段严格按脚本描述"
            "完成假动作与反转；收尾——按脚本收在贱兮兮的对镜定格亮相，表情与尾巴留一点余韵。"
        )
    elif amplitude == "naughty":
        if not is_cat:
            raise ValueError("amplitude=naughty 目前只接猫版捣乱剧本（subject_kind=cat）")
        pose_rule = (
            "5. 按起始姿态合理设计：这只猫是四足着地的真猫，一切动作按真实猫科动物的身手与"
            "体型设计，不拟人直立、不说人话；首帧里的被捣乱物品（杯、碗、纸巾、袜子、玩具等）"
            "全程按脚本轨迹流转，其余环境物件保持原位，不凭空换位。\n"
        )
        amp_rules = (
            "6. 这是真实猫咪捣乱喜剧，按恶作剧三拍演：盯上——起手就带着明显的坏主意（眯眼"
            "斜视、悄悄挪近、缓缓抬爪蓄势）；得手——捣乱动作干脆利落一次到位（拍、推、扒、"
            "掀，不拖泥带水）；装无辜——得手后立刻切换无辜表情（扭头看镜头、舔爪、假装打"
            "呵欠、若无其事甩尾），表情反差就是笑点，眼神戏贯穿全程。\n"
            "7. 编排结构：盯上——0 秒从首帧姿态直接带出起手段；得手——中段按脚本完成捣乱"
            "动作，被捣乱的物品按脚本软着陆；装无辜——收尾按脚本收在装无辜的对镜定格，表情"
            "与尾巴留余韵。"
        )
    elif amplitude == "large":
        if is_cat:
            pose_rule = (
                "5. 按起始姿态合理设计：四足站定可以做该类型相关的动作，跃起、扑跳、转体都是"
                "自然可达的大位移；蹲伏、俯身这类蓄力姿态可以由该姿势直接发力衔接大动作"
                "（跃起、前冲、翻滚），动作从起势延续、不要先归位再重新开始；侧躺/仰卧类"
                "只做该姿态下可达的连贯动态（翻滚、蹬腿、甩头、小幅挪动），"
                "不要编排先站起来、归位摆好再开始的动作。\n"
            )
            amp_rules = (
                "6. 动作幅度大：允许并鼓励跃跳、扑跳、转体、甩头与大幅伸展，躯干、四肢与"
                "尾巴全程参与，表情随节奏自然变化；四肢结构连贯清楚，落地轻稳，动作连续"
                "合理，不做镜头前的怪异扭曲。\n"
                "7. 编排结构：起势——0 秒从首帧姿态直接带出；主段——2–3 组动作主题依次展开，"
                "幅度逐段升级，头部、肩背、前爪、腰胯、后腿与尾巴轮换参与，不整段重复同一动作；"
                "收尾——回到镜头前自然亮相定格，在动作余韵中收束。"
            )
        else:
            pose_rule = (
                "5. 按起始姿态合理设计：站姿类可以做该类型相关的节奏动作，跳跃、跨步、转体都是"
                "从站姿自然可达的大位移；蹲姿、俯身这类蓄力姿势可以由该姿势直接发力衔接大动作"
                "（起跳、前冲、爬行、翻滚），动作从起势延续、不要先归位再重新开始；躺/卧/倚靠类"
                "只做该姿态下可达的连贯动态（缓慢伸展、转头、重心挪动、手部局部、小幅翻滚挪动），"
                "不要编排先站起来、归位摆好再开始的动作。\n"
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
        if is_cat:
            pose_rule = (
                "5. 按起始姿态合理设计：四足站定可以做该类型相关的动作；蹲伏/侧躺/仰卧类"
                "只做该姿态下自然可达的连贯动态（伸展、转头、重心挪动、爪尾局部动作），"
                "不要编排跃起之类脱离起始姿态的大位移。\n"
            )
            amp_rules = "6. 动作幅度适中，四肢与尾巴结构连贯清楚，不做镜头前的怪异扭曲。"
        else:
            pose_rule = (
                "5. 按起始姿态合理设计：站姿类可以做该类型相关的节奏动作；坐/卧/俯身/倚靠类"
                "只做该姿态下自然可达的连贯动态（缓慢伸展、转头、重心挪动、手部局部动作），"
                "不要编排站起来、跳跃之类脱离起始姿态的大位移。\n"
            )
            amp_rules = "6. 动作幅度适中，四肢与手指结构连贯清楚，不做镜头前的怪异扭曲。"
    safety_rule = (
        f"{8 if amplitude in ('large', 'skit', 'naughty') else 7}. 安全红线：把【安全红线】里的每一条写进「避免出现」"
        + ("一节并贯穿动作、体态与表情的描写" if is_cat else "一节并贯穿动作、衣着、表情的描写")
        + "，脚本正文不得违反其中任何一条。\n"
    ) if safety_text else ""
    return (
        header
        + f"【首帧内容·动作描述】\n{pose_text}\n"
        + f"【动作】{pose_title}" + (f"（{category}）" if category else "") + "\n"
        + appearance_line
        + (f"【人物服饰要点】\n{costume_text}\n" if costume_text else "")
        + scene_line
        + style_line
        + safety_line
        + f"【本段视频类型】{clip_type}\n"
        + "\n输出要求（直接输出提示词正文，中文，不要标题、不要任何解释）：\n"
        + f"1. 开头一句：以输入首帧为唯一视觉依据，生成一段约{duration}秒的单只猫{clip_type}视频。\n"
        + "2. 分节成文，每节一小段落：画面连续性 / 镜头 / 动作整体 / 动作顺序 / 动作衔接 / "
        + "避免出现。「动作顺序」必须给分段时轴（0—X 秒…共"
        + f"{duration}秒），每段写清"
        + ("身体、四肢与尾巴" if is_cat else "身体与四肢")
        + "的连续动作，前一段自然带出后一段。\n"
        + (
            (
                "3. 硬规则：全程同一只猫，同毛色同花纹；背景与光线全程与首帧一致；基本固定"
                "机位一镜到底（最多轻微缓推）；猫始终留在画面内；结尾在自然的动作余韵中"
                "收束，不要突然僵住；"
                + (
                    "不出现第二只猫或其他动物；如有场景人物只允许出现在远景并整体虚焦，"
                    "与猫零互动（不走向猫、不触碰猫、不新增情节）、不切镜头、"
                    "不换背景、不出现文字水印。\n"
                    if (crowd or "solo").strip() == "backdrop"
                    else "不出现人类、不出现第二只猫或其他动物、不切镜头、"
                    "不换背景、不出现文字水印。\n"
                )
            )
            if is_cat
            else
            "3. 硬规则：全程同一名人物、同发型同服装；背景与光线全程与首帧一致；基本固定"
            "机位一镜到底（最多轻微缓推）；人物始终留在画面内；结尾在自然的动作余韵中"
            "收束，不要突然僵住；不出现第二个人或动物、不切镜头、不换背景、不出现文字水印。\n"
        )
        + connect_rule
        + pose_rule
        + amp_rules
        + safety_rule
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
    opening: str = "body",
    text_words: list[str] | None = None,
    style_text: str = "",
) -> str:
    """多镜 MV brief：编号镜头+分秒切点+机位运镜，正文后附【实录声场】【配乐】两段。

    opening="face" = 特写开场（第 1 镜面部大特写，第 2 镜起切全身）；text_words
    非空 = 两处背景大字（中段爆点 + 末镜亮相，词已掷定逐词编进对应镜头）；
    style_text = 全局质感句（写成【全局质感】材料段，全程一致）。"""
    scene_line = (
        f"【场景与光线（来自场景设计步，全程一致）】\n{scene_text}\n" if scene_text else ""
    )
    style_line = (
        f"【全局质感（来自配方 style_lock，全程一致）】\n{style_text}\n"
        "（正文开头一句之后按原样嵌入一句：「本支 MV 全片质感：" + style_text
        + "」——逐字采用，不得改写或拆散。）\n"
    ) if style_text else ""
    if image_attached and opening == "face":
        first_frame = "已附的这张图片——首席的面部大特写，第 1 镜的开场画面，特写本身就在图中"
    elif image_attached:
        first_frame = "已附的这张图片——第 1 镜的开场画面，0 秒就是图中的姿态"
    else:
        first_frame = "另一张已生成的首帧图（随提示词一起发给视频模型）——第 1 镜的开场画面"
    structure_line = (
        "，采用多镜头 MV 分镜结构（特写开场：第 1 镜保持面部大特写，第 2 镜起切到全身开始编舞）"
        if opening == "face"
        else "，采用多镜头 MV 分镜结构"
    )
    if opening == "face":
        first_shot_rule = (
            f"第 1 镜（0 秒起至切点）保持面部大特写（只露脸不露肩）：0 秒从附图特写画面直接"
            "接续，镜头固定不切出特写；先以柔和定妆光定住，随后一道暖金色光带从画面一侧"
            "平滑地横扫过整张脸（1–1.5 秒内单次扫完，不频闪、不往复），扫过处眼瞳亮起高光、"
            "脸颊被提亮、发丝边缘晕出金色逆光；光带扫完光线落回柔和定妆光，只写眼神流转、"
            "睫毛轻眨、嘴角逐渐绽成明亮微笑等表情微动作；切镜点安排在光带扫完、微笑绽到"
            "高点的那一拍。第 2 镜起切到全身/全景构图，编舞从第 2 镜开始，"
            "动作在切镜点前后保持衔接感。"
        )
        chorus_rule = (
            "6. 编舞按首席的起始姿态合理可达，结构上：起势（第 1 镜特写中以眼神与表情酝酿，"
            "切镜即爆发）→ 主段（从第 2 镜起贯穿各镜的 2–3 组动作主题，幅度逐段升级）→ 收尾"
            "（末镜回到镜头前亮相定格，动作余韵中收束）。"
        )
    else:
        first_shot_rule = (
            "第 1 镜 0 秒从首帧姿态直接接续，不要先摆好姿势再开始；后续镜头切换机位但仍在"
            "同一场景中，动作在切镜点前后保持衔接感。"
        )
        chorus_rule = (
            "6. 编舞按首席的起始姿态合理可达，结构上：起势（第 1 镜从首帧姿态带出）→ 主段"
            "（贯穿各镜的 2–3 组动作主题，幅度逐段升级）→ 收尾（末镜回到镜头前亮相定格，"
            "动作余韵中收束）。"
        )
    if ensemble:
        cast_rule = (
            "4. 伴舞安排：第 2 镜起伴舞入画（特写开场时第 3 镜起入画），人数 3–5 名，穿与首席"
            "同色系的简化服装、与首席保持明显身形区分；动作与首席同步呼应、阵型对称合理并"
            "始终让出首席的中心位置，不遮挡、不顶替首席。\n"
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
    words = [str(w or "").strip() for w in (text_words or []) if str(w or "").strip()]
    text_rule = (
        "7. 文字特效：中段爆点镜（第 2 镜或第 3 镜的最强拍处）与末镜亮相各编入一处背景大字，"
        f"依次写入「{words[0]}」「{words[1]}」（词已给定，直接采用、不改拼写）；"
        "大字为超大粗体无衬线大写、亮色闪粉/辉光质感，作为背景图形贴在布景墙面上，"
        "构图不遮挡首席面部与队形核心，整镜清晰可读；除这两处外画面中不出现任何其他文字。\n"
    ) if len(words) >= 2 else ""
    return (
        f"你是 MV 导演兼视频编剧。写一段 {duration} 秒、{shots_count} 个镜头的 AI 生成视频"
        f"「直出视频提示词」{structure_line}。\n"
        f"视频的首帧画面：{first_frame}；后续镜头是脚本切换出的新构图，由视频模型按分镜执行。\n\n"
        + f"【首席·动作描述】\n{pose_text}\n"
        + f"【首席·动作】{pose_title}" + (f"（{category}）" if category else "") + "\n"
        + (f"【首席·服饰要点】\n{costume_text}\n" if costume_text else "")
        + scene_line
        + style_line
        + f"【本支 MV 风格】{clip_type}\n"
        + "\n输出要求（直接输出提示词正文，中文，不要标题、不要任何解释）：\n"
        + f"1. 开头一句：以输入首帧为唯一视觉依据，生成一段约{duration}秒、{shots_count}个镜头的{clip_type}MV 视频。\n"
        + "2. 分镜编号成文（镜头一／镜头二……）：每镜写明起始秒（第几秒起，切点均匀排满"
        + f"{duration}秒）、镜头语言（从特写、中景、全景、低位仰拍、跟拍后退、横摇、推近、环绕"
        + "中选用）与该镜的画面内容及编舞细节；"
        + first_shot_rule
        + "\n"
        + "3. 硬规则：全程同一首席——同发型同服装同妆容，每个镜头里首席都是画面焦点，不混入"
        + "人群失去主体；切镜不换背景与光线；不出现文字水印；结尾在动作余韵中收束。\n"
        + cast_rule
        + amp_rule
        + chorus_rule
        + "\n"
        + text_rule
        + f"{'8' if text_rule else '7'}. 正文之后按以下两个小段收尾（各 2–3 句）：\n"
        + "【实录声场】写画内实录声音（脚步、呼吸、服装窸窣、现场混响等）；支持音频生成的"
        + "视频模型会按此出声，不支持的模型仅作氛围参考。\n"
        + "【配乐】写画外配乐：曲风、配器要素，以及节奏与编舞动作同步的关系。"
    )


def validate_script(
    text: str, multishot: bool = False, text_words: list[str] | None = None
) -> str:
    """校验脚本文本；不合格抛 ValueError。

    多镜模式另要求分镜结构词；text_words 非空时逐词要求出现在脚本里（防 LLM 丢词）。"""
    text = str(text or "").strip().strip("“”\"' ")
    if not _CJK_RE.search(text):
        raise ValueError("视频脚本没有中文内容")
    if not _MIN_LEN <= len(text) <= _MAX_LEN:
        raise ValueError(f"视频脚本长度不合适：{len(text)} 字（限 {_MIN_LEN}–{_MAX_LEN}）")
    if "首帧" not in text:
        raise ValueError("视频脚本没有提到「首帧」—— 连续性依据缺失")
    if multishot and "镜头" not in text:
        raise ValueError("多镜模式视频脚本缺少分镜结构（提到「镜头」）")
    missing = [w for w in (text_words or []) if w and w not in text]
    if missing:
        raise ValueError(f"视频脚本丢掉了文字特效词：{'、'.join(missing)}")
    return text


def call_llm(messages: list[dict], model: str | None, config: dict | None = None) -> str:
    """单次 LLM 调用（集中在此便于测试替换）；text 输出，不走 JSON。"""
    return llm.chat(messages, model=model, config=config, json_mode=False)


def pick_clip_type(seed: int, key: str, types: list[str] | None = None) -> str:
    """片段类型：池子可由配方 YAML types 覆盖；子种子抽取（可复现）。"""
    pool = [t for t in (types or []) if str(t or "").strip()] or list(DEFAULT_TYPES)
    return wildcard.rng_for(seed, key).choice(pool)


def pick_text_words(seed: int, key: str, pool: list[str] | None = None) -> list[str]:
    """文字特效词：池子里掷**两个不同**词（中段爆点 + 末镜亮相各一），可复现。

    池子可由配方 YAML text_pool 覆盖；缺省回退 DEFAULT_TEXT_POOL；池子不足两个
    有效词时同词复用两处（validate 允许同词出现两处条款）。"""
    candidates = [str(t or "").strip() for t in (pool or []) if str(t or "").strip()]
    candidates = candidates or list(DEFAULT_TEXT_POOL)
    rng = wildcard.rng_for(seed, key)
    if len(candidates) >= 2:
        first = rng.choice(candidates)
        rest = [w for w in candidates if w != first]
        return [first, rng.choice(rest or candidates)]
    return [candidates[0], candidates[0]]


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
    opening: str = "body",
    text_words: list[str] | None = None,
    style_text: str = "",
    safety_text: str = "",
    subject_kind: str = "person",
    appearance_text: str = "",
    crowd: str = "solo",
) -> dict:
    """写视频脚本；返回 {"script", "clip_type"}。LLM 失败/输出不合法抛 LLMError/ValueError。

    image_path 非空 = 看图模式：messages 附上首帧图（PIL 压到长边 ≤1280 再 base64），
    brief 换「以附图实际姿态接续」措辞 —— 配方步传 combo_image 的真实产物进来。
    amplitude="large" = 大幅度档：规则 5–7 换大幅版并追加编排结构规则；amplitude="skit"
    = 拟人贱剧档（只接猫）：规则 5–7 换「起手—转折—收尾」剧本三拍版；amplitude="naughty"
    = 真猫捣乱档（只接猫）：规则 5–7 换「盯上—得手—装无辜」恶作剧三拍版；缺省 normal
    保持原 brief 不变（向后兼容）。
    crowd="backdrop" = 猫版硬规则允许远景虚焦的背景人物（与 crowd 步档同一开关）。
    multishot=True = 多镜头 MV 分镜：编号镜头+分秒切点+机位运镜，正文后附【实录声场】
    【配乐】两段；ensemble 配合 multishot 写伴舞条款；shots 给定镜头数（缺省按时长推）。
    opening="face"（须 multishot）= 特写开场：第 1 镜保持面部特写，第 2 镜起切全身。
    text_words（掷骰词对）= 两处背景大字逐词编进脚本并锁进校验。
    style_text（配方 style_lock）= 全局质感句，写成【全局质感（全程一致）】材料段铆进 brief。
    safety_text（配方 safety_lock）= 安全红线句，写成【安全红线（全程一致）】材料段 +
    要求逐条落进「避免出现」节 —— 仅单镜脚本生效（多镜显式报错）。
    subject_kind = 主角物种（"person" 缺省 / "cat"）——猫版措辞（同一只猫/不出现人类…）；
    cat 仅单镜脚本；appearance_text = 猫外观描述（写成【主角·外观】材料段，与首帧同一只猫）。
    """
    if multishot and safety_text:
        raise ValueError("safety_lock 暂只支持单镜脚本（多镜 MV 分镜模式不接）")
    if multishot and str(subject_kind or "person") == "cat":
        raise ValueError("subject_kind=cat 暂只支持单镜脚本（多镜 MV 分镜模式不接）")
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
            opening=str(opening or "body"),
            text_words=[w for w in (text_words or []) if str(w or "").strip()],
            style_text=str(style_text or "").strip(),
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
            style_text=str(style_text or "").strip(),
            safety_text=str(safety_text or "").strip(),
            subject_kind=str(subject_kind or "person"),
            appearance_text=str(appearance_text or "").strip(),
            crowd=str(crowd or "solo"),
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
    return {
        "script": validate_script(text, multishot=multishot, text_words=text_words),
        "clip_type": clip_type,
    }