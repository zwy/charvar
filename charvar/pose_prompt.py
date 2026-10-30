"""姿势库动作描述 → 直通出图提示词（<image1> 主体槽位图 + <image2> 人偶线稿）。

结构 = 用户定稿的画面版 demo，分段按库线型给版本（全身 / 半身 / 双人）：
两段参考图锚定 →「让 image1 角色摆出 image2 姿势：{动作描述}」→ 构图句 →
背景句 → 禁令句。动作描述在 pose_actions 表（动作×视角 双键，写入用
``charvar pose action``）；这里的分段文本是自有内容、进 git。
服装层（单人生效）：``build_prompt(costume=True)`` 加 <image3> 服装锚 +
融合段 + 换装禁令（图像来自 costume-db-01，见 docs/服饰库.md）；无动作的
「单主体 + 服装」用 build_costume_prompt（<image1> 主体 + <image2> 服装）；
catalog 英文关键词拼成「服装要点」注记跟在服装锚后（部件清单，不译写）。
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
    body_lock: list[str] | None = None,
    costume_keywords: str | None = None,
    costume_display: str = "ghost",
    costume_policy: dict | None = None,
) -> str:
    """按线型拼 demo 结构；动作描述必须是已写好的视角级文本。

    pair_two_subjects（仅 2人絡み 生效）：双主体版 —— <image1>/<image2> 分别锚定
    描述里的「甲」「乙」，动作线稿顺移成 <image3>；单主体版保持两人同沿用一张图。
    costume（仅单人生效，双人显式报错）：<image3> = 服装参考图 —— image1 锚定句
    换服装版（锚身份不锚衣着），动作描述之后插「服装融合」段，禁令句追加换装禁令。 costume_display 按条目的
    展示形态选「这张图是什么」说明句（ghost = 隐形模特，flat_lay = 平铺展示）。
    costume_keywords（可选）：catalog 英文关键词拼「服装要点」注记行，插在服装锚后。
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
        mode = costume_mode(costume_policy)
        m = _COSTUME_MODE_SEGMENTS[mode]
        parts = [
            m["identity"].format(slot=3),
            seg["mannequin"],
            _display_form(costume_display, 3),
            m["anchor"].format(slot=3),
            _costume_keywords(costume_keywords, 3),
            _figure_anchor(3, body_lock),
            _LEAD[kind] + text,
            m["fusion_pose"].format(slot=3),
            seg["framing"],
            seg["background"],
            _guard_with_costume(seg["guard"], m["guard"]),
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

# 展示形态说明（对应 costume 库 display_type；进服装锚之前的一句「这张图是什么」）。
# ghost = 隐形模特：无头无身、服装保持穿着立体形态悬空陈列（悬空配件机制由
# 配件锚那一层点名）；flat_lay = 平铺：分开平放是陈列不是穿着，组合语义显式转移。
_DISPLAY_FORM_CLAUSES = {
    "ghost": (
        "{image}是隐形模特（ghost mannequin）展示图：图中没有头和身体，"
        "服装保持穿上时的立体形态悬空陈列。"
    ),
    "flat_lay": (
        "{image}是服装平铺展示图（俯视平铺陈列）：衣物分开平放是展示方式，"
        "不是穿着效果；按穿搭关系在角色身上正确组合穿着，成图是正常人体视角。"
    ),
}

_COSTUME_ANCHOR = (
    "<image{slot}>为服装参考，仅参考其中的服装与整套配件：服装参考其款式、轮廓、"
    "配色、面料质感、图案、领口、袖型、腰线、衣摆与层次结构；配件参考鞋、袜、帽、"
    "围巾、手套、包等随身物品的款式与用法；参考图中单独摆放或悬空放置的配件（如帽子、包）也是这套造型的组成部分，同样要装到角色身上：帽子戴在头上、围巾围在脖子上、包挎在肩上或拿在手里。服装与全部配件一起完整还原，清单上列出的每件都要出现在画面里；若图中含"
    "人物，不采用其脸、发型、肤色、体型、姿势、背景或画风。未展示的服装部分按"
    "同一套结构合理补全，不额外添加无关装饰。"
)

_COSTUME_KEYWORDS_LEAD = (
    "<image{slot}>服装要点（英文关键词，与图一致）：{keywords}。"
    "按此清单逐项核对服装部件，不缺件、不改部件类型。"
)


def _costume_keywords(keywords: str | None, slot: int) -> str:
    """服装要点注记：catalog 英文关键词原文整段进提示词（不译写，同服装段约定）。

    单靠图锚定模型会误读部件类型（实测 WIN-0001 长裙三次种子读成长裤，
    包类配件时有时无）——注记一行写出部件清单，锚定句有文本可依。
    """
    text = str(keywords or "").strip()
    if not text:
        return ""
    return _COSTUME_KEYWORDS_LEAD.format(slot=slot, keywords=text)


def _display_form(display: str, slot: int) -> str:
    """展示形态句；未知形态显式报错（typo 不能静默退化成 ghost）。"""
    value = str(display or "ghost")
    if value not in _DISPLAY_FORM_CLAUSES:
        raise ValueError(
            f"未知服装展示形态：{display}（可选 {'/'.join(_DISPLAY_FORM_CLAUSES)}）"
        )
    return _DISPLAY_FORM_CLAUSES[value].format(image=f"<image{slot}>")

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

_FIGURE_ANCHOR = (
    "角色身材、身高与四肢比例一律以<image1>为准；<image{slot}>图为服装参考，"
    "服装的款式、面料与整套搭配（含鞋、袜、包、帽等配件）都要保留，"
    "并按<image1>角色的身材重新合身。"
)


def _figure_anchor(costume_slot: int, body_lock: list[str] | None) -> str:
    """换装流的身材锚段：身材归 <image1>，服装图只管款式面料。

    实测背景：服装图是「穿在身上的衣服」，自带隐含身体模板 —— 不锚的话
    身材会随服装图走（胸部缩水/拉伸拉高）。主体档案 body_lock（可缺省，
    英文体形锚 1–2 条）原文拼接进来当第二层锚 —— 与服装段「语言不限、
    不译写」同一约定。
    """
    parts = [_FIGURE_ANCHOR.format(slot=costume_slot)]
    parts.extend(str(x).strip() for x in (body_lock or []) if str(x).strip())
    return "\n".join(parts)


def _guard_with_costume(seg_guard: str, guard_append: str = _COSTUME_GUARD_APPEND) -> str:
    """基础禁令句 + 换装禁令（缝合句号，避免「。；」双标点）。"""
    base = seg_guard[:-1] if seg_guard.endswith("。") else seg_guard
    return base + "；" + guard_append


# 服装参考的换装语义分叉（条目 subject_policy.clothing → 措辞）。
# replace = 整套替换（现有行为，缺省）；add = 叠加型（丝袜等单件：原衣物保留、
# 袜类换上）。两个模式逐段对应（身份锚/图锚/融合/禁令/穿上句），全部正向措辞
# —— 禁句按「显性召唤物」红线写成正向事实（如「只穿图中这一双」而不是「别出现两双」）。
COSTUME_MODES = ("replace", "add")

_COSTUME_MODE_SEGMENTS: dict[str, dict[str, str]] = {
    "replace": {
        "identity": (
            "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、肤色和整体"
            "美术风格，确保生成的是同一个角色，不要擅自更换造型；其原有服装不保留，"
            "配饰与<image{slot}>服装参考冲突时，以服装参考为准。"
        ),
        "anchor": _COSTUME_ANCHOR,
        "fusion_pose": _COSTUME_FUSION,
        "fusion_nopose": _COSTUME_FUSION_NOPOSE,
        "guard": _COSTUME_GUARD_APPEND,
        "wear_lead": "让<image1>图中的角色穿上<image{slot}>图中的服装，替换原有服装。",
    },
    "add": {
        "identity": (
            "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、肤色和整体"
            "美术风格，确保生成的是同一个角色，不要擅自更换造型；其原有服装保留不变，"
            "腿部袜类与<image{slot}>服装参考不一致时，以服装参考为准。"
        ),
        "anchor": (
            "<image{slot}>为袜类参考，仅参考这双袜子的款式：长度（连裤/过膝/及膝）、"
            "材质、透明度、颜色与光泽；把它完整穿到角色腿上，袜口位置与图中一致；"
            "若图中含人物，不采用其脸、发型、肤色、体型、姿势、背景或画风。"
        ),
        "fusion_pose": (
            "把<image{slot}>图中的袜类穿到角色腿上：角色原本穿着袜类时直接换成图中"
            "这一双，原本没穿袜类时就直接穿上；原有衣着与鞋帽、包袋等其它配件保持"
            "原样不更换；袜类自然贴合腿部，在屈膝、抬腿处产生合理的褶皱与拉伸。"
        ),
        "fusion_nopose": (
            "把<image{slot}>图中的袜类穿到角色腿上：角色原本穿着袜类时直接换成图中"
            "这一双，原本没穿袜类时就直接穿上；原有衣着与鞋帽、包袋等其它配件保持"
            "原样不更换；袜类自然贴合腿部，自然站立时产生合理的褶皱与拉伸。"
        ),
        "guard": "原有衣着完整保留不更换，腿上只穿图中这一双袜类。",
        "wear_lead": "让<image1>图中的角色穿上<image{slot}>图中的袜类，其余衣着保留不变。",
    },
}


def costume_mode(costume_policy: dict | None) -> str:
    """条目策略 → 措辞模式（缺省/未知 = 整套替换；add 以外不静默发明分叉）。"""
    clothing = str((costume_policy or {}).get("clothing") or "replace")
    if clothing not in COSTUME_MODES:
        raise ValueError(
            f"未知换装语义：{clothing}（可选 {'/'.join(COSTUME_MODES)}）"
        )
    return clothing


def build_costume_prompt(
    body_lock: list[str] | None = None,
    costume_keywords: str | None = None,
    costume_display: str = "ghost",
    costume_policy: dict | None = None,
) -> str:
    """单主体 + 服装（无动作参考）→ 全身换装图提示词。

    <image1> = 主体档案槽位图（角色锚定），<image2> = 服装参考图；姿态不锚
    （模型自选自然站姿）。服装锚/身材锚/换装禁令与 build_prompt(costume=True) 同源。 costume_keywords 同 build_prompt。
    costume_policy 条目策略 → 措辞分叉（replace 整套替换 / add 叠加袜类）。
    """
    mode = costume_mode(costume_policy)
    m = _COSTUME_MODE_SEGMENTS[mode]
    fusion = m["fusion_nopose"]
    seg = _SEGMENTS["full"]
    return "\n\n".join(
        [
            m["identity"].format(slot=2),
            _display_form(costume_display, 2),
            m["anchor"].format(slot=2),
            _costume_keywords(costume_keywords, 2),
            _figure_anchor(2, body_lock),
            m["wear_lead"].format(slot=2),
            fusion.format(slot=2),
            seg["framing"],
            seg["background"],
            _guard_with_costume(seg["guard"], m["guard"]),
        ]
    )
