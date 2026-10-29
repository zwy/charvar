"""姿势库动作描述 → 直通出图提示词（<image1> 主体槽位图 + <image2> 人偶线稿）。

结构 = 用户定稿的画面版 demo，分段按库线型给版本（全身 / 半身 / 双人）：
两段参考图锚定 →「让 image1 角色摆出 image2 姿势：{动作描述}」→ 构图句 →
背景句 → 禁令句。动作描述在 pose_actions 表（动作×视角 双键，写入用
``charvar pose action``）；这里的分段文本是自有内容、进 git。
服装层（单人生效）：``build_prompt(costume=True)`` 加 <image3> 服装锚 +
融合段 + 换装禁令（图像来自 costume-db-01，见 docs/服装库.md）；无动作的
「单主体 + 服装」用 build_costume_prompt（<image1> 主体 + <image2> 服装）。
背景/光照先固定一句，后续接 presets/wildcards 的 backgrounds/lighting 池
再参数化（届时随机一律走 charvar.wildcard.seed_for 派生）。
"""
from __future__ import annotations

# 库线 → 段落线型（2人絡み = 双人；上半身1人 = 半身；两个全身系 = 全身单人）
LINE_KIND: dict[str, str] = {
    "全身1人・セクシー系": "full",
    "全身1人・標準系": "full",
    "上半身1人": "upper",
    "2人絡み": "pair",
}

_SEGMENTS = {
    "full": {
        "identity": (
            "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、服装、配饰"
            "和整体美术风格，确保生成的是同一个角色，不要擅自更换造型。"
        ),
        "mannequin": (
            "<image2>为姿势参考，仅参考人物的身体动作、重心、四肢位置和整体姿态，"
            "不参考其人体模型外观、辅助线或线稿画风。"
        ),
        "framing": (
            "采用全身构图，完整呈现头顶、双手和双脚，不裁切肢体。"
            "保持角色原有的身体比例，让肩膀、手臂、腰胯和腿部衔接自然，"
            "服装随动作产生合理的褶皱与遮挡。"
        ),
        "background": "背景简洁，突出角色。",
        "guard": (
            "不要出现人体结构辅助线、关节圆圈或素体模型效果；"
            "避免多余手指、多余肢体、关节扭曲及手臂与头部粘连。"
        ),
    },
    "upper": {
        "identity": (
            "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、服装、配饰"
            "和整体美术风格，确保生成的是同一个角色，不要擅自更换造型。"
        ),
        "mannequin": (
            "<image2>为姿势参考，仅参考人物的身体动作、重心、四肢位置和整体姿态，"
            "不参考其人体模型外观、辅助线或线稿画风。"
        ),
        "framing": (
            "采用半身构图，画面取头顶到大腿之间，完整呈现头顶与双手，不裁切手指与手臂；"
            "保持角色原有的身体比例，让肩颈与手臂衔接自然，"
            "服装随动作产生合理的褶皱与遮挡。"
        ),
        "background": "背景简洁，突出角色。",
        "guard": (
            "不要出现人体结构辅助线、关节圆圈或素体模型效果；"
            "避免多余手指、关节扭曲及手臂与头部粘连。"
        ),
    },
    "pair": {
        "identity": (
            "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、服装、配饰"
            "和整体美术风格，双人姿势中的两位角色都沿用该外观与服装，不要擅自更换造型。"
        ),
        "mannequin": (
            "<image2>为姿势参考，仅参考两位人物的动作、重心、四肢位置和整体姿态，"
            "不参考其人体模型外观、辅助线或线稿画风。"
        ),
        "framing": (
            "采用双人全身构图，完整呈现两位角色的头顶、双手和双脚，不裁切肢体；"
            "两人保持原有身体比例，关节衔接自然，身体接触的部位贴合不悬空，"
            "服装随动作产生合理的褶皱与遮挡。"
        ),
        "background": "背景简洁，突出角色。",
        "guard": (
            "不要出现人体结构辅助线、关节圆圈或素体模型效果；"
            "画面固定为两个角色、不多不少，避免多余手指、多余肢体、关节扭曲，"
            "以及手臂与头部互相粘连。"
        ),
    },
}

# 「摆出姿势」引导句：双人版点名双人
_LEAD = {
    "full": "让<image1>图中的角色摆出<image2>图中的姿势：",
    "upper": "让<image1>图中的角色摆出<image2>图中的姿势：",
    "pair": "让<image1>图中的角色摆出<image2>图中的双人姿势（描述中的甲、乙两位都由该角色担任）：",
}


def kind_of_line(line: str) -> str:
    """线（系統原文）→ 线型；未知线回退全身。"""
    return LINE_KIND.get(str(line or ""), "full")


def build_prompt(
    line: str,
    action_text: str,
    *,
    pair_two_subjects: bool = False,
    costume: bool = False,
) -> str:
    """按线型拼 demo 结构；动作描述必须是已写好的视角级文本。

    pair_two_subjects（仅 2人絡み 生效）：双主体版 —— <image1>/<image2> 分别锚定
    描述里的「甲」「乙」，动作线稿顺移成 <image3>；单主体版保持两人同沿用一张图。
    costume（仅单人生效，双人显式报错）：<image3> = 服装参考图 —— image1 锚定句
    换服装版（锚身份不锚衣着），动作描述之后插「服装融合」段，禁令句追加换装禁令。
    """
    kind = LINE_KIND.get(str(line or ""), "full")
    seg = _SEGMENTS[kind]
    text = str(action_text or "").strip()
    if costume and kind == "pair":
        raise ValueError("双人动作暂不支持服装参考（仅单人两类工作流接入服装）")
    if kind == "pair" and pair_two_subjects:
        identity = (
            "<image1>为角色设定参考（担当描述中的「甲」），保留该角色的面部特征、发型、发色、"
            "服装、配饰和整体美术风格，不要擅自更换造型。\n\n"
            "<image2>为角色设定参考（担当描述中的「乙」），保留该角色的面部特征、发型、发色、"
            "服装、配饰和整体美术风格，不要擅自更换造型。"
        )
        mannequin = (
            "<image3>为姿势参考，仅参考两位人物的动作、重心、四肢位置和整体姿态，"
            "不参考其人体模型外观、辅助线或线稿画风。"
        )
        lead = (
            "让<image1>图中的角色担任描述里的「甲」、<image2>图中的角色担任「乙」，"
            "摆出<image3>图中的双人姿势："
        )
        parts = [identity, mannequin, lead + text, seg["framing"], seg["background"], seg["guard"]]
        return "\n\n".join(parts)
    if costume:
        parts = [
            (
                "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、肤色和整体"
                "美术风格，确保生成的是同一个角色，不要擅自更换造型；其原有服装不保留，"
                "配饰与<image3>服装参考冲突时，以服装参考为准。"
            ),
            seg["mannequin"],
            _COSTUME_ANCHOR.format(slot=3),
            _LEAD[kind] + text,
            _COSTUME_FUSION,
            seg["framing"],
            seg["background"],
            _guard_with_costume(seg["guard"]),
        ]
        return "\n\n".join(part for part in parts if part)
    parts = [
        seg["identity"],
        seg["mannequin"],
        _LEAD[kind] + text,
        seg["framing"],
        seg["background"],
        seg["guard"],
    ]
    return "\n\n".join(part for part in parts if part)


# ---------- 服装参考（costume-db-01 取图；role=costume 只锚服装） ----------

_COSTUME_ANCHOR = (
    "<image{slot}>为服装参考，仅参考其中服装的款式、轮廓、配色、面料质感、图案、"
    "领口、袖型、腰线、衣摆与层次结构，完整还原该套服装；若图中含人物，不采用其脸、"
    "发型、肤色、体型、姿势、背景或画风。未展示的服装部分按同一套结构合理补全，"
    "不额外添加无关装饰。"
)

_COSTUME_FUSION = (
    "把<image3>图中的服装穿在角色身上，替换其原有服装（不是叠穿在原服装外面），"
    "忠实保留服装可见的款式、配色、面料质感与图案细节；服装自然适配角色体型与动作，"
    "在抬臂、弯肘、叉腰与屈膝处产生合理的褶皱、拉伸和遮挡，叉腰或贴身的手位于衣物外侧；"
    "保留服装关键识别细节，不要为了展示服装而改变姿势。"
)

_COSTUME_FUSION_NOPOSE = (
    "把<image2>图中的服装穿在角色身上，替换其原有服装（不是叠穿在原服装外面），"
    "忠实保留服装可见的款式、配色、面料质感与图案细节；服装自然适配角色体型，"
    "自然站立时产生合理的褶皱与遮挡；保留服装关键识别细节。"
)

_COSTUME_GUARD_APPEND = "不出现原服装残留或两套服装混搭，服装关键细节不缺失。"


def _guard_with_costume(seg_guard: str) -> str:
    """基础禁令句 + 换装禁令（缝合句号，避免「。；」双标点）。"""
    base = seg_guard[:-1] if seg_guard.endswith("。") else seg_guard
    return base + "；" + _COSTUME_GUARD_APPEND


def build_costume_prompt() -> str:
    """单主体 + 服装（无动作参考）→ 全身换装图提示词。

    <image1> = 主体档案槽位图（角色锚定），<image2> = 服装参考图；姿态不锚
    （模型自选自然站姿）。服装锚与换装禁令与 build_prompt(costume=True) 同源。
    """
    identity = (
        "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、肤色和整体"
        "美术风格，确保生成的是同一个角色，不要擅自更换造型；其原有服装不保留，"
        "配饰与<image2>服装参考冲突时，以服装参考为准。"
    )
    seg = _SEGMENTS["full"]
    return "\n\n".join(
        [
            identity,
            _COSTUME_ANCHOR.format(slot=2),
            "让<image1>图中的角色穿上<image2>图中的服装，替换原有服装。",
            _COSTUME_FUSION_NOPOSE,
            seg["framing"],
            seg["background"],
            _guard_with_costume(seg["guard"]),
        ]
    )
