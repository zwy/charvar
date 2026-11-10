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

# 无图纯文本动作（自建库 studio）的引导句：没有姿势参考图槽位，动作描述即全部
# 动作锚（视角级文本自描述机位/朝向/入镜；双人动作需要两人线稿，显式报错）。
_POSE_TEXT_LEAD = "让<image1>图中的角色做出下面的动作：\n"

# depth 姿势库（pose-db-02）的 mannequin 段版本：深度图锚前后遮挡（白=近），
# 不是线稿 —— 禁令点名「别画成人偶/深度图」。slot 位随双人双主体换位（缺省 2）。
_MANNEQUIN_DEPTH: dict[str, str] = {
    "full": (
        "<image{slot}>为姿势参考（灰度深度图，越白越靠前），仅参考人物的身体动作、"
        "重心、四肢位置、前后遮挡关系和整体姿态；不要把成图画成人偶或深度图。"
    ),
    "upper": (
        "<image{slot}>为姿势参考（灰度深度图，越白越靠前），仅参考人物的身体动作、"
        "重心、四肢位置、前后遮挡关系和整体姿态；不要把成图画成人偶或深度图。"
    ),
    "pair": (
        "<image{slot}>为姿势参考（灰度深度图，越白越靠前），仅参考两位人物的动作、"
        "重心、四肢位置、前后遮挡关系和整体姿态；不要把成图画成人偶或深度图。"
    ),
}


def kind_of_line(line: str) -> str:
    """线（系統原文）→ 线型；未知线回退全身。"""
    return LINE_KIND.get(str(line or ""), "full")


_CLOSEUP_FRAMING = (
    "采用面部大特写构图：画面被脸部充满——从额头到下巴，额头贴近画面上缘、下巴贴近画面"
    "下缘，五官清晰、眼神有神采，肤质与发丝质感自然真实；构图比脸部参考照更紧：脖子、"
    "肩、锁骨一律不进入画面（不露肩膀，肩颈不入画，下巴以下没有任何身体部位）；浅景深，"
    "背景柔化虚化；只呈现这一名角色的面部，不出现第二个人。"
)


def build_closeup_prompt(
    face_section: str | None = "face",
    background_text: str | None = None,
    style_text: str | None = None,
) -> str:
    """MV 特写首帧：**纯脸**参考 → 面部大特写构图 + 场景背景（随机 MV 升级版配方即此用法）。

    不进动作段/服饰段 —— 特写只锚身份（脸库条目 = 唯一参考图），服饰与编舞由脚本
    的切镜后镜头展开；场景文本照常经 ``background_text`` 替换固定背景句；
    ``style_text``（配方 style_lock）= 全局质感段，插在构图句与背景段之间。
    仅支持 face 段：styling 段自带服装语义，与特写构图打架，显式报错。
    """
    if face_section != "face":
        raise ValueError(f"特写首帧只支持 face 段（当前 {face_section!r}；styling 自带服装语义）")
    seg = _SEGMENTS["full"]
    parts = [
        _FACE_IDENTITY_PLAIN,
        _CLOSEUP_FRAMING,
        str(style_text or "").strip(),
        _background_segment(seg, background_text),
        seg["guard"],
    ]
    return "\n\n".join(part for part in parts if part)


def _background_segment(seg: dict, background_text: str | None) -> str:
    """背景段：调用方给了 scene 文本（如配方 scene_design 步的产物）就用它，
    否则保持固定句「背景简洁，突出角色。」。空白串回退固定句（LLM 输出被清空不炸）。"""
    text = str(background_text or "").strip()
    return text or str(seg["background"])


def _framing_segment(seg: dict, framing_extra: str | None) -> str:
    """构图段：调用方给了追加段（如舞蹈首帧的余白/不持物要求）就换行拼在后面，
    空白串保持原段（配方步输出被清空不炸）。"""
    base = str(seg["framing"])
    extra = str(framing_extra or "").strip()
    return base + "\n" + extra if extra else base


def build_cat_prompt(
    appearance: str,
    action_text: str,
    *,
    background_text: str | None = None,
    framing_extra: str | None = None,
) -> str:
    """无参考图的**猫主角**首帧提示词（random_cat_video 配方专用）。

    与 build_prompt 的人像文案群平行：没有 <imageN> 槽位 —— 外观（随机猫描写）
    承担身份锚，动作描述（自建猫动作库文本，起势+蓄势句）承担动作锚。结构：
    外观段 → 引导句+动作 → 构图句(+追加) → 场景句 → 禁令句。appearance 或
    action_text 为空显式报错（外观池掷骰与文本动作描述不可能为空）。"""
    appearance = str(appearance or "").strip()
    action_text = str(action_text or "").strip()
    if not appearance or not action_text:
        raise ValueError("猫主角首帧的外观与动作描述都不能为空（外观池掷骰 / 动作描述必填）")
    framing = (
        "采用全身构图，完整呈现猫的头顶、耳朵和尾巴尖，不裁切身体任何部位；"
        "地面完整可见，猫的身体比例与关节衔接自然真实。"
    )
    extra = str(framing_extra or "").strip()
    if extra:
        framing = framing + "\n" + extra
    background = str(background_text or "").strip() or "背景简洁，突出主角。"
    parts = [
        # 外观池条目按约定自带量词（「一只…」）；不带时补「一只」，避免「一只一只」叠加
        (f"主角是{appearance}。" if appearance.startswith("一只") else f"主角是一只{appearance}。")
        + "以描述为准描绘这只猫，毛发质感与身体结构真实自然，写实的猫形态，不是卡通吉祥物。",
        "让主角猫做出下面的动作：\n" + action_text,
        framing,
        background,
        (
            "画面固定为一只猫，不要出现人类、不要出现第二只动物；"
            "避免多余腿、多余肢体、关节扭曲与肢体粘连；毛发不要塑料感或贴图感；"
            "画面中不出现文字水印。"
        ),
    ]
    return "\n\n".join(parts)


def build_prompt(
    line: str,
    action_text: str,
    *,
    pair_two_subjects: bool = False,
    costume: bool = False,
    costume_has_image: bool = True,
    pose_has_image: bool = True,
    body_lock: list[str] | None = None,
    costume_keywords: str | None = None,
    costume_display: str = "ghost",
    costume_policy: dict | None = None,
    pose_display: str = "line",
    face_section: str | None = None,
    background_text: str | None = None,
    accessory_strip: bool = False,
    framing_extra: str | None = None,
) -> str:
    """按线型拼 demo 结构；动作描述必须是已写好的视角级文本。

    pair_two_subjects（仅 2人絡み 生效）：双主体版 —— <image1>/<image2> 分别锚定
    描述里的「甲」「乙」，动作线稿顺移成 <image3>；单主体版保持两人同沿用一张图。
    costume（仅单人生效，双人显式报错）：<image3> = 服装参考图 —— image1 锚定句
    换服装版（锚身份不锚衣着），动作描述之后插「服装融合」段，禁令句追加换装禁令。 costume_display 按条目的
    展示形态选「这张图是什么」说明句（ghost = 隐形模特，flat_lay = 平铺展示）。
    costume_keywords（可选）：catalog 英文关键词拼「服装要点」注记行，插在服装锚后。
    costume_has_image（缺省 True）：False = 纯文本条目（无参考图）—— 服饰文本整段
    进提示词、提示词里无服装图槽位（只支持 replace，见「服饰文本锚」段说明）。
    pose_has_image（缺省 True）：False = 无图纯文本动作（自建库 studio）—— mannequin
    段整个去掉、lead 换「做出下面的动作」版，动作描述即锚（双人显式报错）。
    pose_display（可选）：姿势参考库的展示形态（line=人偶线稿 缺省 / depth=深度图，
    白=近 / text=无图文本库）—— mannequin 段换深度图版锚前后遮挡；未知形态显式报错。
    face_section（可选，人脸库身份锚）：face/styling 段条目当 <image1>（与主体档案
    互斥，调用方把门）。styling 段与主体档案完全同文本；face 段（正面胸像）换
    face 版身份句/身材锚/融合句 —— 见 face 分段家族注释。双人线显式报错。
    background_text（可选）：配方 scene_design 步设计的场景/光照段 —— 给了就替换
    固定的「背景简洁，突出角色。」句（_background_segment，空白回退固定句）。
    accessory_strip（缺省 False）：配件剥离版 —— 服饰图锚/要点注记/身材锚换「只锚
    服装主件与鞋袜，包/帽/墨镜/手持物一律不带入画面」的措辞组（舞蹈首帧等场景）；
    replace 模式锚句与身材锚都换，add（袜类）模式锚句本就不含配件、只换要点注记。
    framing_extra（可选）：框景追加段，非空时拼在构图段之后（仅单人全身/半身生效）。
    """
    kind = LINE_KIND.get(str(line or ""), "full")
    seg = _SEGMENTS[kind]
    pose_text = not pose_has_image
    if pose_display not in ("line", "depth", "text"):
        raise ValueError(f"未知姿势参考展示形态：{pose_display}（可选 line/depth/text）")
    if pose_display == "text" and pose_has_image:
        raise ValueError("display_type=text 的姿势库是无图文本库，没有姿势参考图可锚")
    if face_section is not None and face_section not in ("face", "styling"):
        raise ValueError(f"未知人脸条目形态：{face_section}（可选 face/styling）")
    if accessory_strip and (kind == "pair" or not costume or not costume_has_image):
        raise ValueError(
            "accessory_strip 仅适用于单人 + 带参考图的服饰条目"
            "（纯文本条目/不换装没有配饰可剥离）"
        )
    if framing_extra and kind == "pair":
        raise ValueError("framing_extra 仅支持单人全身/半身构图（双人无此段）")
    # 无图文本动作：mannequin 段整个去掉（join 过滤空段），lead 换文本版 ——
    # 动作描述（视角级文本）承担全部动作锚（见 _POSE_TEXT_LEAD 段说明）。
    if pose_text and kind == "pair":
        raise ValueError("双人动作需要姿势参考图（线稿/深度）—— 文本锚只接单人动作")
    mannequin = (
        ""
        if pose_text
        else (
            _MANNEQUIN_DEPTH[kind].format(slot=3 if (kind == "pair" and pair_two_subjects) else 2)
            if pose_display == "depth"
            else seg["mannequin"]
        )
    )
    _lead = _POSE_TEXT_LEAD if pose_text else _LEAD[kind]
    text = str(action_text or "").strip()
    if costume and kind == "pair":
        raise ValueError("双人动作暂不支持服装参考（仅单人两类工作流接入服装）")
    if face_section and kind == "pair":
        raise ValueError("双人动作暂不支持人脸身份锚（仅单人生效）")
    if face_section == "face" and costume:
        mode = costume_mode(costume_policy)
        m = _COSTUME_MODE_SEGMENTS[mode]
        identity = _FACE_IDENTITY_REPLACE if mode == "replace" else _FACE_IDENTITY_ADD
        fusion = _COSTUME_FUSION_FACE if mode == "replace" else m["fusion_pose"]
        guard = _FACE_GUARD_REPLACE if mode == "replace" else _FACE_GUARD_ADD
        strip = accessory_strip and mode == "replace"
        parts = [
            identity.format(slot=3),
            mannequin,
            _display_form(costume_display, 3),
            (_COSTUME_ANCHOR_STRIP if strip else m["anchor"]).format(slot=3),
            _costume_keywords(costume_keywords, 3, strip=accessory_strip),
            _figure_anchor_face(3, body_lock, strip=strip),
            _lead + text,
            fusion.format(slot=3) if mode == "add" else fusion,
            _framing_segment(seg, framing_extra),
            _background_segment(seg, background_text),
            _guard_with_costume(seg["guard"], guard),
        ]
        return "\n\n".join(part for part in parts if part)
    if face_section == "face" and not costume:
        parts = [
            _FACE_IDENTITY_PLAIN,
            mannequin,
            _lead + text,
            _framing_segment(seg, framing_extra),
            _background_segment(seg, background_text),
            seg["guard"],
        ]
        return "\n\n".join(part for part in parts if part)
    if kind == "pair" and pair_two_subjects:
        identity = (
            "<image1>为角色设定参考（担当描述中的「甲」），保留该角色的面部特征、发型、发色、"
            "服装、配饰和整体美术风格，不要擅自更换造型。\n\n"
            "<image2>为角色设定参考（担当描述中的「乙」），保留该角色的面部特征、发型、发色、"
            "服装、配饰和整体美术风格，不要擅自更换造型。"
        )
        mannequin = (
            _MANNEQUIN_DEPTH["pair"].format(slot=3)
            if pose_display == "depth"
            else (
                "<image3>为姿势参考，仅参考两位人物的动作、重心、四肢位置和整体姿态，"
                "不参考其人体模型外观、辅助线或线稿画风。"
            )
        )
        lead = (
            "让<image1>图中的角色担任描述里的「甲」、<image2>图中的角色担任「乙」，"
            "摆出<image3>图中的双人姿势："
        )
        parts = [identity, mannequin, lead + text, seg["framing"], _background_segment(seg, background_text), seg["guard"]]
        return "\n\n".join(parts)
    if costume:
        mode = costume_mode(costume_policy)
        m = _COSTUME_MODE_SEGMENTS[mode]
        if not costume_has_image:
            # 纯文本条目：服饰文本整段进提示词，无服装图槽位（见服饰文本锚说明）。
            costume_text_mode_ok(costume_has_image, costume_policy)
            if face_section == "face":
                identity = _COSTUME_TEXT_IDENTITY_FACE
                figure = _figure_anchor_text_face(body_lock)
            else:
                identity = _COSTUME_TEXT_IDENTITY
                figure = _figure_anchor_text(body_lock)
            parts = [
                identity,
                mannequin,
                _costume_text_segment(costume_keywords),
                figure,
                _lead + text,
                _COSTUME_TEXT_FUSION,
                _framing_segment(seg, framing_extra),
                _background_segment(seg, background_text),
                _guard_with_costume(seg["guard"]),
            ]
            return "\n\n".join(part for part in parts if part)
        strip = accessory_strip and mode == "replace"
        parts = [
            m["identity"].format(slot=3),
            mannequin,
            _display_form(costume_display, 3),
            (_COSTUME_ANCHOR_STRIP if strip else m["anchor"]).format(slot=3),
            _costume_keywords(costume_keywords, 3, strip=accessory_strip),
            _figure_anchor(3, body_lock, strip=strip),
            _lead + text,
            m["fusion_pose"].format(slot=3),
            _framing_segment(seg, framing_extra),
            _background_segment(seg, background_text),
            _guard_with_costume(seg["guard"], m["guard"]),
        ]
        return "\n\n".join(part for part in parts if part)
    parts = [
        seg["identity"],
        mannequin,
        _lead + text,
        _framing_segment(seg, framing_extra),
        _background_segment(seg, background_text),
        seg["guard"],
    ]
    return "\n\n".join(part for part in parts if part)


# ---------- 服装参考（costume-db-01 取图；role=costume 只锚服装） ----------

# 展示形态说明（对应 costume 库 display_type；进服装锚之前的一句「这张图是什么」）。
# ghost = 隐形模特：无头无身、服装保持穿着立体形态悬空陈列（悬空配件机制由
# 配件锚那一层点名）；flat_lay = 平铺：分开平放是陈列不是穿着，组合语义显式转移；
# mannequin = 假人台：服装穿在无头无脸的假模特身体上陈列（有身体形、无皮肤头脸四肢）。
_DISPLAY_FORM_CLAUSES = {
    "ghost": (
        "{image}是隐形模特（ghost mannequin）展示图：图中没有头和身体，"
        "服装保持穿上时的立体形态悬空陈列。"
    ),
    "flat_lay": (
        "{image}是服装平铺展示图（俯视平铺陈列）：衣物分开平放是展示方式，"
        "不是穿着效果；按穿搭关系在角色身上正确组合穿着，成图是正常人体视角。"
    ),
    "mannequin": (
        "{image}是服装人台展示图：服装穿在无头无脸的假模特身体上陈列，"
        "保持穿上时的立体形态；按穿搭关系在角色身上正确组合穿着，成图是正常人体视角。"
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

# 配件剥离（舞蹈首帧等场景）版组（accessory_strip=True）：默认版要求包/帽等配件全部
# 穿戴还原 —— 背着包跳舞不成画面，所以整套换图锚/要点注记/身材锚的剥离措辞，
# 只锚服装主件与鞋袜；围巾、手套保留不剥离。
_COSTUME_ANCHOR_STRIP = (
    "<image{slot}>为服装参考，仅参考其中的服装与鞋袜：服装参考其款式、轮廓、"
    "配色、面料质感、图案、领口、袖型、腰线、衣摆与层次结构，鞋袜参考鞋、袜的款式与穿着方式；"
    "图中若附有包、帽、墨镜或其他手持、头戴的配饰，一律不带入画面：人物双手不持物、"
    "头上不戴帽、身上不挎包。若图中含人物，不采用其脸、发型、肤色、体型、姿势、"
    "背景或画风。未展示的服装部分按同一套结构合理补全，不额外添加无关装饰。"
)

_COSTUME_KEYWORDS_LEAD_STRIP = (
    "<image{slot}>服装要点（英文关键词，与图一致）：{keywords}。"
    "按此清单核对服装主件（衣物与鞋袜）；清单中的包、帽、墨镜、手持物类配饰忽略不画。"
)


def _costume_keywords(keywords: str | None, slot: int, strip: bool = False) -> str:
    """服装要点注记：catalog 英文关键词原文整段进提示词（不译写，同服装段约定）。

    单靠图锚定模型会误读部件类型（实测 WIN-0001 长裙三次种子读成长裤，
    包类配件时有时无）——注记一行写出部件清单，锚定句有文本可依。
    strip=True 时注记换成主件版：清单里的包/帽/墨镜类配饰忽略不画。
    """
    text = str(keywords or "").strip()
    if not text:
        return ""
    lead = _COSTUME_KEYWORDS_LEAD_STRIP if strip else _COSTUME_KEYWORDS_LEAD
    return lead.format(slot=slot, keywords=text)


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

_FIGURE_ANCHOR_STRIP = (
    "角色身材、身高与四肢比例一律以<image1>为准；<image{slot}>图为服装参考，"
    "服装的款式、面料要按图中还原，衣物与鞋袜都要保留，包、帽、墨镜等配饰不计入；"
    "并按<image1>角色的身材重新合身。"
)


def _figure_anchor(costume_slot: int, body_lock: list[str] | None, strip: bool = False) -> str:
    """换装流的身材锚段：身材归 <image1>，服装图只管款式面料。

    实测背景：服装图是「穿在身上的衣服」，自带隐含身体模板 —— 不锚的话
    身材会随服装图走（胸部缩水/拉伸拉高）。主体档案 body_lock（可缺省，
    英文体形锚 1–2 条）原文拼接进来当第二层锚 —— 与服装段「语言不限、
    不译写」同一约定。strip=True 换配件剥离版（包/帽等不计入）。
    """
    parts = [(_FIGURE_ANCHOR_STRIP if strip else _FIGURE_ANCHOR).format(slot=costume_slot)]
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


# ---------- 服饰文本锚（无图纯文本条目，如 character_outfit_debug 系列） ----------
#
# 「仅内裤 / 无衣物 / 保留原衣物」这类策略测试条款没有实体可画 —— 无图纯文本是
# 合法状态，图锚定用不了（提示词里没有服装参考图槽位）。文本锚定分支：服饰文本
# （prompt_text 优先、回退 keywords，语言不限不译写 —— 与 gen 通路「text 层优先」
# 同一约定）整段逐字进提示词，提示词里**不再出现服装图槽位号**；身材锚保留
# <image1>（/face 版自洽句）+ body_lock。措辞只落 replace（整套替换）—— add 类
# 无图条目没有实测过的措辞，显式报错不发明。全部正向语句（无「图中」字样）。


def _costume_text_segment(costume_text: str | None) -> str:
    """服饰文本段：引出句 + 条目文本整段逐字（不译写；缺文本显式报错不空锚）。"""
    text = str(costume_text or "").strip()
    if not text:
        raise ValueError(
            "服饰条目既没有文本层也没有关键词 —— 文本锚定没有可锚的内容"
        )
    return (
        "服饰描述（这条服饰没有参考图，按此描述逐项执行，"
        "每件衣物都要体现在角色身上）：\n" + text
    )


_COSTUME_TEXT_IDENTITY = (
    "<image1>为角色设定参考，保留该角色的面部特征、发型、发色、肤色和整体"
    "美术风格，确保生成的是同一个角色，不要擅自更换造型；其原有服装不保留，"
    "整套替换为服饰描述中的服装。"
)

_COSTUME_TEXT_IDENTITY_FACE = (
    "<image1>为角色脸部参考，保留该角色的面部特征、发型、发色和整体美术风格，"
    "确保生成的是同一个角色，不要擅自更换造型；图中没有服装与身材信息，"
    "服装完全按服饰描述穿着。"
)

_COSTUME_TEXT_FUSION = (
    "角色身上穿的就是服饰描述中的服装，替换其原有服装（不是叠穿在原服装外面），"
    "描述列出的每件衣物完整呈现；服装自然适配角色体型与动作，"
    "在抬臂、弯肘、叉腰与屈膝处产生合理的褶皱、拉伸和遮挡，叉腰或贴身的手位于衣物外侧；"
    "保留描述中的服装关键识别细节，不要为了展示服装而改变姿势。"
)

_COSTUME_TEXT_FUSION_NOPOSE = (
    "角色身上穿的就是服饰描述中的服装，替换其原有服装（不是叠穿在原服装外面），"
    "描述列出的每件衣物完整呈现；服装自然适配角色体型，"
    "自然站立时产生合理的褶皱与遮挡；保留描述中的服装关键识别细节。"
)

# 文本锚版身材锚：服装没有图（没有隐含身体模板），去掉服装图那半句。
_FIGURE_ANCHOR_TEXT_SUBJECT = "角色身材、身高与四肢比例一律以<image1>为准。"

_FIGURE_ANCHOR_TEXT_FACE = (
    "角色身材、身高与四肢比例保持自然协调、整体匀称，与该角色的美术风格一致。"
)


def _figure_anchor_text(body_lock: list[str] | None) -> str:
    """文本锚版身材锚段：身材归 <image1>，无服装图身体模板可锚。"""
    parts = [_FIGURE_ANCHOR_TEXT_SUBJECT]
    parts.extend(str(x).strip() for x in (body_lock or []) if str(x).strip())
    return "\n".join(parts)


def _figure_anchor_text_face(body_lock: list[str] | None) -> str:
    """face 版文本锚身材段：没有 <image1> 身材可锚 → 正向自洽句 + body_lock 原文。"""
    parts = [_FIGURE_ANCHOR_TEXT_FACE]
    parts.extend(str(x).strip() for x in (body_lock or []) if str(x).strip())
    return "\n".join(parts)


def costume_text_mode_ok(costume_has_image: bool, costume_policy: dict | None) -> None:
    """纯文本条目只支持 replace（add 的文本锚措辞未实测，不发明分叉）。"""
    if costume_has_image:
        return
    mode = costume_mode(costume_policy)
    if mode != "replace":
        raise ValueError(
            f"纯文本服饰条目（无参考图）暂只支持整套替换（replace）策略，"
            f"现有条目策略是 {mode} —— add 类要画得出实体，请先补参考图"
        )


# ---------- 人脸身份锚（face-db-01 条目当 <image1>；与 --subject 互斥，见 CLI） ----------
#
# face 段条目（正面向き，胸像）只有脸/发型/发色 —— 没有服装与身材信息，所以：
# 身份句不锚（也不提）图中服装；身材锚换 face 版（模型按美术风格自洽 + body_lock 原文）；
# 换装融合句去掉「替换其原有服装」（没有原服装可替换 —— 提了反而召唤出底装残留）。
# styling 段条目（整体造型，全身）保留服装/身材信息，身份句/身材锚与主体档案版
# 完全同文本，不另设分叉。

_FACE_IDENTITY_PLAIN = (
    "<image1>为角色脸部参考，保留该角色的面部特征、发型、发色和整体美术风格，"
    "确保生成的是同一个角色，不要擅自更换造型。"
)

_FACE_IDENTITY_REPLACE = (
    "<image1>为角色脸部参考，保留该角色的面部特征、发型、发色和整体美术风格，"
    "确保生成的是同一个角色，不要擅自更换造型；图中没有服装与身材信息，"
    "服装完全按<image{slot}>服饰参考穿着。"
)

_FACE_IDENTITY_ADD = (
    "<image1>为角色脸部参考，保留该角色的面部特征、发型、发色和整体美术风格，"
    "确保生成的是同一个角色，不要擅自更换造型；角色的基础衣着按其美术风格自然搭配，"
    "腿部袜类与<image{slot}>服装参考不一致时，以服装参考为准。"
)

# 体形锚（face 段版）：没有 <image1> 身材可锚 → 正向自洽句 + body_lock 原文
# （第二层锚同 _figure_anchor 约定）。
_FIGURE_ANCHOR_FACE = (
    "角色身材、身高与四肢比例保持自然协调、整体匀称，与该角色的美术风格一致；"
    "<image{slot}>图为服装参考，服装的款式、面料与整套搭配（含鞋、袜、包、帽等配件）"
    "都要保留，并按角色自身的身材重新合身。"
)

_FIGURE_ANCHOR_FACE_STRIP = (
    "角色身材、身高与四肢比例保持自然协调、整体匀称，与该角色的美术风格一致；"
    "<image{slot}>图为服装参考，服装的款式、面料要按图中还原，衣物与鞋袜都要保留，"
    "包、帽、墨镜等配饰不计入，并按角色自身的身材重新合身。"
)

# face 段的换装融合句（replace 版）：照 _COSTUME_FUSION 但去掉「替换其原有服装（不是
# 叠穿在原服装外面）」—— face 图没有原服装，提它反而召唤底装残留。
_COSTUME_FUSION_FACE = (
    "把<image3>图中的服装穿在角色身上，服装按图中整套搭配完整还原，忠实保留可见的"
    "款式、配色、面料质感与图案细节；服装自然适配角色体型与动作，"
    "在抬臂、弯肘、叉腰与屈膝处产生合理的褶皱、拉伸和遮挡，叉腰或贴身的手位于衣物外侧；"
    "保留服装关键识别细节，不要为了展示服装而改变姿势。"
)

# 工作流②（无动作）的 face 版融合句（服装槽位 = <image2>）。
_COSTUME_FUSION_FACE_NOPOSE = (
    "把<image2>图中的服装穿在角色身上，服装按图中整套搭配完整还原，忠实保留可见的"
    "款式、配色、面料质感与图案细节；服装自然适配角色体型，"
    "自然站立时产生合理的褶皱与遮挡；保留服装关键识别细节。"
)

_FACE_GUARD_REPLACE = "不出现两套服装混搭，服装关键细节不缺失。"
_FACE_GUARD_ADD = "基础衣着自然稳定不中途更换，腿上只穿图中这一双袜类。"

# 工作流②（无动作）的 face 版穿上引导句（replace/add 各一）。
_FACE_WEAR_LEAD_REPLACE = "让<image1>图中的角色穿上<image{slot}>图中的服装，完整还原图中整套搭配。"
_FACE_WEAR_LEAD_ADD = "让<image1>图中的角色穿上<image{slot}>图中的袜类。"


def _figure_anchor_face(costume_slot: int, body_lock: list[str] | None, strip: bool = False) -> str:
    """face 版身材锚段：身材不来自图（正面向き胸像），自洽句 + body_lock 原文。"""
    parts = [(_FIGURE_ANCHOR_FACE_STRIP if strip else _FIGURE_ANCHOR_FACE).format(slot=costume_slot)]
    parts.extend(str(x).strip() for x in (body_lock or []) if str(x).strip())
    return "\n".join(parts)


def build_costume_prompt(
    body_lock: list[str] | None = None,
    costume_keywords: str | None = None,
    costume_display: str = "ghost",
    costume_policy: dict | None = None,
    face_section: str | None = None,
    background_text: str | None = None,
    costume_has_image: bool = True,
) -> str:
    """单主体 + 服装（无动作参考）→ 全身换装图提示词。

    <image1> = 主体档案槽位图（角色锚定），<image2> = 服装参考图；姿态不锚
    （模型自选自然站姿）。服装锚/身材锚/换装禁令与 build_prompt(costume=True) 同源。 costume_keywords 同 build_prompt。
    costume_policy 条目策略 → 措辞分叉（replace 整套替换 / add 叠加袜类）。
    face_section（可选，人脸库身份锚）：face/styling 条目当 <image1>（与主体档案
    互斥），同 build_prompt 的 face 分段家族 —— face 段换 face 版身份句/身材锚/
    穿上引导句/融合句。
    costume_has_image（缺省 True）：False = 纯文本条目（无参考图）—— 服饰文本整段
    进提示词、提示词里无服装图槽位（只支持 replace，见「服饰文本锚」段说明）。
    """
    mode = costume_mode(costume_policy)
    m = _COSTUME_MODE_SEGMENTS[mode]
    seg = _SEGMENTS["full"]
    if not costume_has_image:
        # 纯文本条目：服饰文本整段进提示词，无服装图槽位（见服饰文本锚说明）。
        costume_text_mode_ok(costume_has_image, costume_policy)
        if face_section == "face":
            return "\n\n".join(
                [
                    _COSTUME_TEXT_IDENTITY_FACE,
                    _costume_text_segment(costume_keywords),
                    _figure_anchor_text_face(body_lock),
                    _COSTUME_TEXT_FUSION_NOPOSE,
                    seg["framing"],
                    _background_segment(seg, background_text),
                    _guard_with_costume(seg["guard"]),
                ]
            )
        return "\n\n".join(
            [
                _COSTUME_TEXT_IDENTITY,
                _costume_text_segment(costume_keywords),
                _figure_anchor_text(body_lock),
                _COSTUME_TEXT_FUSION_NOPOSE,
                seg["framing"],
                _background_segment(seg, background_text),
                _guard_with_costume(seg["guard"]),
            ]
        )
    if face_section == "face":
        identity = _FACE_IDENTITY_REPLACE if mode == "replace" else _FACE_IDENTITY_ADD
        figure = _figure_anchor_face(2, body_lock)
        wear_lead = (
            _FACE_WEAR_LEAD_REPLACE if mode == "replace" else _FACE_WEAR_LEAD_ADD
        ).format(slot=2)
        fusion = _COSTUME_FUSION_FACE_NOPOSE if mode == "replace" else m["fusion_nopose"]
        guard = _FACE_GUARD_REPLACE if mode == "replace" else _FACE_GUARD_ADD
        return "\n\n".join(
            [
                identity.format(slot=2),
                _display_form(costume_display, 2),
                m["anchor"].format(slot=2),
                _costume_keywords(costume_keywords, 2),
                figure,
                wear_lead,
                fusion,
                seg["framing"],
                _background_segment(seg, background_text),
                _guard_with_costume(seg["guard"], guard),
            ]
        )
    fusion = m["fusion_nopose"]
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
            _background_segment(seg, background_text),
            _guard_with_costume(seg["guard"], m["guard"]),
        ]
    )
