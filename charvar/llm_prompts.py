"""LLM 提示词模板（需求：生成优化 + 主体图起草）。

两组调用：
- draft_subject_image_prompt_messages：为主体档案 5 种图片起草提示词
  （character_sheet = 文生图；其余 4 种 = 以 <image1>（character_sheet 参考图）为基准的编辑指令）；
- build_rewrite_messages：批量改写变体提示词，句序固定：
  参考图用途 → 目标景别 → 躯干/头部/视线 → 目标表情 → 背景/光照。

视角规范取自角色模板文档（乒乓小子 V2.0 定妆视角库、银白女巫 V2.1 多视角规则）：
- 全身图头顶到鞋底完整入镜（含指尖）、居中留白；平视近似正交、无广角变形、无俯仰；
- 侧面 = 严格 90 度侧面、鼻尖朝向明确、头身同步转向、不转头看镜头；
- 背面不回头、不露出正面五官；正面四分之三 / 背面四分之三约 45°、头身同向；
- 被遮挡细节按视角自然隐藏，不强行转动或错开肢体展示；
- 左右朝向互换时不对称细节（痣、挑染、纹样）保持贴身位置，不随机镜像；
- 模板显式禁止项（文字/logo/图案/粗描边/3D 等）输出为显式 no 句。
"""
from __future__ import annotations

import json

from charvar.models import SubjectProfile
from charvar.subject_images import KIND_LABELS

_TEMPLATE_MAX_CHARS = 24_000

# ---- 视角规范语句（英文，起草/改写共用；改写时逐字采用，不许改变朝向语义） ----

ANGLE_VIEW_SENTENCES: dict[str, str] = {
    "front": (
        "The figure faces the camera directly at eye level, head and body in one direction, "
        "eyes looking straight at the camera."
    ),
    "l45": (
        "Three-quarter front view: the whole figure turns about 45 degrees toward the left side of "
        "the frame, so both the front of the face and one side of the body are visible; the head turns "
        "with the body and never alone toward the camera, eyes looking straight ahead in the direction "
        "the face is pointing."
    ),
    "r45": (
        "Three-quarter front view: the whole figure turns about 45 degrees toward the right side of "
        "the frame, so both the front of the face and one side of the body are visible; the head turns "
        "with the body and never alone toward the camera, eyes looking straight ahead in the direction "
        "the face is pointing."
    ),
    "l90": (
        "Strict 90-degree side profile view with the nose tip facing the left side of the frame; head, "
        "chest, hips and feet all point the same direction with no turning the head toward the camera, "
        "eyes looking straight ahead; the far-side limbs may be naturally occluded, never staggered to "
        "expose hidden limbs."
    ),
    "r90": (
        "Strict 90-degree side profile view with the nose tip facing the right side of the frame; head, "
        "chest, hips and feet all point the same direction with no turning the head toward the camera, "
        "eyes looking straight ahead; the far-side limbs may be naturally occluded, never staggered to "
        "expose hidden limbs."
    ),
    "l135": (
        "Three-quarter rear view: the figure stands mostly with its back to the camera and turns about "
        "45 degrees toward the left side of the frame, so the back of the head and of the body dominate "
        "with only one side contour exposed; the head turns with the body and never looks back toward "
        "the camera, the full face not visible."
    ),
    "r135": (
        "Three-quarter rear view: the figure stands mostly with its back to the camera and turns about "
        "45 degrees toward the right side of the frame, so the back of the head and of the body dominate "
        "with only one side contour exposed; the head turns with the body and never looks back toward "
        "the camera, the full face not visible."
    ),
    "back": (
        "Seen directly from behind: the back of the head, the back and the heels face the camera; head "
        "and body share one direction, never turning around, the frontal face not visible."
    ),
}

# framing=full 的构图规范句（chest/head 由 framing_guard 阻断下半身）。
FULL_BODY_CAMERA_SENTENCE: str = (
    "The entire figure from the top of the head to the soles of the shoes is fully inside the frame "
    "(fingertips included), centered with clean margins around it, at eye level and near-orthographic "
    "with no wide-angle distortion, no looking up or down, and no exaggerating the near-side hands or feet."
)

# 赤足任务的构图规范句：服装段写了「赤足 / barefoot」时用这一句——
# 原句的 "soles of the shoes" 跟赤足打架（模型两边都要就画出一双鞋）。
FULL_BODY_CAMERA_SENTENCE_BAREFOOT: str = (
    "The entire figure from the top of the head to the soles of the feet is fully inside the frame "
    "(fingertips included), centered with clean margins around it, at eye level and near-orthographic "
    "with no wide-angle distortion, no looking up or down, and no exaggerating the near-side hands or feet."
)

# 遮挡与镜像规范句（所有视角共用的附加条款）。
OCCLUSION_SENTENCE: str = (
    "Details hidden by this view stay hidden naturally, never rotated, bent or staggered into view; "
    "asymmetric details keep their own side relative to the body across left- and right-facing views "
    "and are never mirrored."
)

# 各 kind 的起草要求（system prompt 片段）。
_KIND_BRIEFS: dict[str, str] = {
    "character_sheet": (
        "文生图。画面为单页横版角色设定图，同页展示同一主体共 4 个视图：\n"
        "- 左侧约占画面宽度 1/3：放大的正面半身像，从头顶完整展示至腰部，露出双肩、胸部与腰部，"
        "下方在腰部裁切，脸部明显大于右侧全身人物的脸部；\n"
        "- 右侧约占 2/3：三个等大全身视图横向并排，从左到右为正面、主体自身左侧朝向观众的 90 度侧面"
        "（鼻尖朝画面左方，头部与身体同步转向）、完整背面（不回头）；三人从头顶到鞋底完整入镜，"
        "大小相同，头顶与鞋底分别对齐，统一自然站姿，双臂垂下、双手放松；\n"
        "- 四个视图同一身份、五官比例、发型（毛色）、身体比例与服装版本，仅景别和观察方向不同；"
        "各视图之间自然留白、轮廓分开；\n"
        "- 纯白背景，光照均匀无强烈阴影，绘画肌理集中在主体内部；不添加文字、标注、分格线、其他角色或道具。\n"
        "若模板给出了另一种设定图布局条款（例如动物采用正面/侧面/背面三个等大全身的纯三视图），"
        "以模板布局为准并完整采用其条款。"
    ),
    "headshot": (
        "图像编辑指令：角色设定图参考图以 <image1> 提供，身份、服饰与画风以它为准，只改构图和镜头。"
        "画面：同一主体的半身像，从头顶完整展示至腰部，露出双肩、胸部与腰部，下方在腰部裁切；"
        "正面朝向镜头、平视，眼平视镜头，五官与发型清楚，表情可用模板默认神情；"
        "平视、近似正交，无广角变形、无俯视仰视；主体居中，背景为中性纯色、柔和均匀光照。"
    ),
    "front": (
        "图像编辑指令：角色设定图参考图以 <image1> 提供，身份、服饰与画风以它为准，只改构图和镜头。"
        "画面：同一主体的全身正面定妆：正面朝向镜头平视，自然站立，双臂垂下与身体留少量间隙，"
        "双手放松，双脚舒适分开；从头顶到鞋底完整入镜（含指尖），主体居中、四周留白；"
        "平视、近似正交，无广角变形、无俯视仰视，不夸大近侧手脚，保持原身体比例；"
        "背景为中性纯色、柔和均匀光照。"
    ),
    "side": (
        "图像编辑指令：角色设定图参考图以 <image1> 提供，身份、服饰与画风以它为准，只改构图和镜头。"
        "画面：同一主体的全身侧面定妆：严格 90 度侧面，鼻尖朝画面左方（与参考图侧面视图同方向），"
        "头、胸、骨盆与脚尖朝向一致，不转头看镜头；双臂自然垂下，双脚自然站稳；"
        "远侧手脚允许自然遮挡，不强行转体或错开肢体来展示被遮部位；"
        "从头顶到鞋底完整入镜（含指尖），平视、近似正交，无广角变形、无俯仰，保持原身体比例；"
        "主体居中，背景为中性纯色、柔和均匀光照。"
    ),
    "behind": (
        "图像编辑指令：角色设定图参考图以 <image1> 提供，身份、服饰与画风以它为准，只改构图和镜头。"
        "画面：同一主体的全身背面定妆：完全背对镜头，后脑、后背与鞋跟朝向镜头，头部与身体朝向一致，"
        "不回头，不露出正面五官；正面细节（五官、表情、胸前纹样等）不得画到背面，背面细节"
        "（后脑发型、背后衣料等）按角色原样自然呈现；双臂自然垂下，双脚舒适分开；"
        "从头顶到鞋底完整入镜（含指尖），平视、近似正交，无广角变形、无俯仰，主体居中、四周留白；"
        "背景为中性纯色、柔和均匀光照。"
    ),
}


# ---- 参考图分工锚定句（逐图分工，语义对齐新版 Q21 工作流） ----
# <imageN> 按勾选顺序各管一摊：headshot=脸/发型/头饰、front=体型/服装/鞋、
# side=侧面朝向与贴身细节、behind=后脑/背部、character_sheet=整套设定图。
# 句子由勾选组合动态生成：选几张生成几条分工，未选的类型不会悬空。
# depth 分句（四个组合表里语义一致，各表挂一份）：深度图不是线稿 —— 锚
# 姿势 + 前后遮挡关系（白=近）；选表后按参考图 display_type 二选一。
POSE_DEPTH_CLAUSE: str = (
    "{tag} is a grayscale depth map of a faceless mannequin, brighter means nearer to "
    "the camera, used only as the pose and spatial-depth reference: follow the stance, "
    "body posture, limb placement, occlusion, foreshortening, and camera angle and "
    "framing it shows exactly, with the same number of figures it depicts; copy no "
    "face, hair, clothing, footwear, accessory, color, or art style from it"
)
# 人脸库分句（四个组合表按 display_type=section 二选一；AI 架构虚构人物，
# 「not a real person」句同时挡住把它当真人画像参照的漂移）：face = 正面向き，
# 锚这个人的脸（含发色/发型）；styling = 整体造型，锚脸 + 发型 + 成套服装。
FACE_CLAUSE: str = (
    "{tag} is a front-facing portrait of one fictional person (AI-generated, not a real "
    "person): adopt that same person as the character, with the same face, feature "
    "proportions, skin tone, hair color, and hairstyle exactly as shown in {tag}; take "
    "no clothing, pose, framing, or background from it"
)
FACE_STYLING_CLAUSE: str = (
    "{tag} is a stylized full-look portrait of one fictional person (AI-generated, not a "
    "real person): adopt that same person as the character, with the same face, feature "
    "proportions, skin tone, hair color, hairstyle, and the complete outfit exactly as "
    "shown in {tag}; take no pose, framing, or background from it"
)
# 发型组合表里的 face 分句：发型结构归发型预设 —— 锚定句点名不抄图里的发型结构
# （脸/肤色/发色是身份照抄；cut/length/styling 与发型预设会互相打架）。
FACE_CLAUSE_HAIRSTYLE: str = (
    "{tag} is a front-facing portrait of one fictional person (AI-generated, not a real "
    "person): adopt that same person's face, feature proportions, skin tone, and hair "
    "color exactly as shown in {tag}; the hair's cut, length, and styling follow the "
    "prescribed hairstyle instead of {tag}; take no clothing, pose, framing, or "
    "background from it"
)
# 服装组合表（含发型+服装组合）里的 styling 分句：服装归服装预设 —— 锚定句明确
# 点名不抄 styling 图里的服装（图自带成套造型，不点名必抄，实测会跟着图穿，
# 服装预设等于没生效）。
FACE_STYLING_CLAUSE_OUTFIT: str = (
    "{tag} is a stylized full-look portrait of one fictional person (AI-generated, not a "
    "real person): adopt that same person's face, feature proportions, skin tone, hair "
    "color, and hairstyle exactly as shown in {tag}; take no clothing, pose, framing, or "
    "background from it — the outfit follows the prescribed outfit instead of {tag}"
)
# 发型组合表里的 styling 分句：脸/发色/服装照图，发型结构归发型预设。
FACE_STYLING_CLAUSE_HAIRSTYLE: str = (
    "{tag} is a stylized full-look portrait of one fictional person (AI-generated, not a "
    "real person): adopt that same person as the character, with the same face, feature "
    "proportions, skin tone, hair color, and the complete outfit exactly as shown in "
    "{tag}; the hair's cut, length, and styling follow the prescribed hairstyle instead "
    "of {tag}; take no pose, framing, or background from it"
)
REFERENCE_ROLE_CLAUSES: dict[str, str] = {
    "character_sheet": (
        "keep the whole identity, outfit version, and art style exactly as shown in {tag}"
    ),
    "headshot": (
        "copy the face, feature proportions, hair, skin tone, and headwear exactly as "
        "they appear in {tag}"
    ),
    "front": (
        "copy the body shape, proportions, complete outfit, and footwear, including "
        "their colors, materials, and details, from {tag}"
    ),
    "side": (
        "use {tag} as the direction reference for the true side profile and for "
        "side-of-body details such as hair flow and one-sided markings"
    ),
    "behind": (
        "use {tag} for the back of the head and the rear outfit details"
    ),
    # 姿势库人偶线稿：只锚姿势构图，不锚任何身份外观 —— 四张分句表里语义一致，
    # 与服装/发型组合正交（人偶没有服装发色可抄）。
    "pose": (
        "{tag} is a grayscale, faceless mannequin line drawing used only as the pose "
        "and composition reference: follow the stance, body posture, limb placement, "
        "and camera angle and framing it shows exactly, with the same number of "
        "figures it depicts; copy no face, hair, clothing, footwear, accessory, "
        "color, or art style from it"
    ),
    "pose_depth": POSE_DEPTH_CLAUSE,
    # 人脸库（display_type=section）：face=正面向き / styling=整体造型（face_styling）。
    "face": FACE_CLAUSE,
    "face_styling": FACE_STYLING_CLAUSE,
}

# 服装组合（outfit 预设）专用：参考图**不再**提供服装——服装由服装预设给定。
# 参考图里那套衣服（主体自己的白裙丝袜/西装等）与服装预设必须二选一，
# 两边都写会让模型跟着参考图穿，服装预设等于没生效（实测：整批 24/24 条丢服装）。
# front 分句必须点名体量锚（bust/hip）：换装时服装 token 权重远大于泛指的
# "copy body shape"，模型遇到遮盖性强的服装（针织裙/西装/风衣）按先验重画躯干，
# 实测整批胸部缩水（mustard_cap_girl_outfit_front_full…s42622）。
REFERENCE_ROLE_CLAUSES_OUTFIT: dict[str, str] = {
    "character_sheet": (
        "keep the whole identity and art style exactly as shown in {tag}"
    ),
    "headshot": (
        "copy the face, feature proportions, hair, skin tone, and headwear exactly as "
        "they appear in {tag}"
    ),
    "front": (
        "copy the body shape, proportions, bust volume and hip width from {tag} exactly "
        "as shown; the clothing change must not reshape the figure"
    ),
    "side": (
        "use {tag} as the direction reference for the true side profile and for "
        "side-of-body details such as hair flow and one-sided markings"
    ),
    "behind": (
        "use {tag} for the back of the head and the rear silhouette"
    ),
    "pose": (
        "{tag} is a grayscale, faceless mannequin line drawing used only as the pose "
        "and composition reference: follow the stance, body posture, limb placement, "
        "and camera angle and framing it shows exactly, with the same number of "
        "figures it depicts; copy no face, hair, clothing, footwear, accessory, "
        "color, or art style from it"
    ),
    "pose_depth": POSE_DEPTH_CLAUSE,
    # 人脸库：styling 图自带成套造型 —— 服装归服装预设，点名不抄图里的服装。
    "face": FACE_CLAUSE,
    "face_styling": FACE_STYLING_CLAUSE_OUTFIT,
}

# 发型组合（hairstyle 预设）专用：参考图**不再**提供头发结构——发型由发型预设给定，
# 参考图只提供脸部/肤色/发色。headshot 原句的 "copy ... hair" 会让模型照抄参考图发型，
# 发型预设等于没生效；发色与脸是身份，保留；长度/刘海/分缝/层次/卷度是结构，归发型预设。
# side 原句的 hair flow、behind 原句的 back of the head 同样按新发型画，分句里去掉。
# headshot/behind 分句带裸头声明（BARE_HEAD_SENTENCE 的压缩版）：参考图里的帽子/
# 头饰会盖住刘海与发顶，预设描述落进被遮挡区时模型连头饰一起保留或画回原发型
# （2026-10-27 实测：autumn_birch_girl 针织帽批 4/4 未落实）。
# 裸头声明（发型组合的固定正向句）：发型预设条目默认裸头——帽子/发箍/发卡等头部
# 佩戴物会盖住发顶与刘海，预设描述落进「被遮挡区」时模型要么连头饰一起保留、
# 要么摘了头饰又画回参考图原发型（2026-10-27 实测：autumn_birch_girl 针织帽，
# 4 条变体全部没有落实预设发型）。这句进锚定句（批间逐字相同）+ 系统硬规则
# （改写 LLM 逐字采用）+ 程序兜底补写（与发型句同组）。若条目本身要带头饰
# （贝雷帽发型等），把头饰描述写进该条 text，本句会被其覆盖。
BARE_HEAD_SENTENCE: str = (
    "Her head is bare, with no hat, no headwear, and no hair accessories, "
    "the whole hairstyle fully exposed from the hairline to the hair ends."
)

REFERENCE_ROLE_CLAUSES_HAIRSTYLE: dict[str, str] = {
    "character_sheet": (
        "keep the whole identity, hair color, and art style exactly as shown in {tag}"
    ),
    "headshot": (
        "copy the face, feature proportions, skin tone, and hair color exactly as they "
        "appear in {tag}; her head is bare, with no hat, no headwear, and no hair "
        "accessories, the whole hairstyle fully exposed; the hair's cut, length, and "
        "styling follow the prescribed hairstyle instead of {tag}"
    ),
    "front": (
        "copy the body shape, proportions, complete outfit, and footwear, including "
        "their colors, materials, and details, from {tag}"
    ),
    "side": (
        "use {tag} as the direction reference for the true side profile and for "
        "one-sided markings"
    ),
    "behind": (
        "use {tag} for the rear outfit details; the back of the head follows the "
        "prescribed hairstyle instead of {tag}, the head bare with no headwear"
    ),
    "pose": (
        "{tag} is a grayscale, faceless mannequin line drawing used only as the pose "
        "and composition reference: follow the stance, body posture, limb placement, "
        "and camera angle and framing it shows exactly, with the same number of "
        "figures it depicts; copy no face, hair, clothing, footwear, accessory, "
        "color, or art style from it"
    ),
    "pose_depth": POSE_DEPTH_CLAUSE,
    # 人脸库：脸/发色照图，发型结构归发型预设；styling 图的服装照图（无服装组合）。
    "face": FACE_CLAUSE_HAIRSTYLE,
    "face_styling": FACE_STYLING_CLAUSE_HAIRSTYLE,
}

# 发型 + 服装同时组合：headshot/side 取发型版（头发归发型预设），
# front 取服装版（体量锚），character_sheet/behind 取发型版并保留服装侧信息。
REFERENCE_ROLE_CLAUSES_OUTFIT_HAIRSTYLE: dict[str, str] = {
    "character_sheet": (
        "keep the whole identity, hair color, and art style exactly as shown in {tag}"
    ),
    "headshot": (
        "copy the face, feature proportions, skin tone, and hair color exactly as they "
        "appear in {tag}; her head is bare, with no hat, no headwear, and no hair "
        "accessories, the whole hairstyle fully exposed; the hair's cut, length, and "
        "styling follow the prescribed hairstyle instead of {tag}"
    ),
    "front": (
        "copy the body shape, proportions, bust volume and hip width from {tag} exactly "
        "as shown; the clothing change must not reshape the figure"
    ),
    "side": (
        "use {tag} as the direction reference for the true side profile and for "
        "one-sided markings"
    ),
    "behind": (
        "use {tag} for the rear silhouette and outfit details; the back of the head "
        "follows the prescribed hairstyle instead of {tag}, the head bare with no headwear"
    ),
    "pose": (
        "{tag} is a grayscale, faceless mannequin line drawing used only as the pose "
        "and composition reference: follow the stance, body posture, limb placement, "
        "and camera angle and framing it shows exactly, with the same number of "
        "figures it depicts; copy no face, hair, clothing, footwear, accessory, "
        "color, or art style from it"
    ),
    "pose_depth": POSE_DEPTH_CLAUSE,
    # 人脸库：发型结构归发型预设、服装归服装预设，锚定句只留脸部身份。
    "face": FACE_CLAUSE_HAIRSTYLE,
    "face_styling": FACE_STYLING_CLAUSE_OUTFIT,
}


def reference_clause_table(outfit_mode: bool, hairstyle_mode: bool) -> dict[str, str]:
    """按组合形态选参考图分工分句表（四种组合，各自语义独立成表）。"""
    if outfit_mode and hairstyle_mode:
        return REFERENCE_ROLE_CLAUSES_OUTFIT_HAIRSTYLE
    if hairstyle_mode:
        return REFERENCE_ROLE_CLAUSES_HAIRSTYLE
    if outfit_mode:
        return REFERENCE_ROLE_CLAUSES_OUTFIT
    return REFERENCE_ROLE_CLAUSES


REFERENCE_TAIL_SENTENCE: str = (
    "Take only the character's identity from the reference images and create a new pose, "
    "camera angle, background, and lighting. One character only, no text."
)


def body_lock_clauses(body_lock: list[str] | None, refs: list[dict]) -> list[str]:
    """把主体的 body_lock（可选体形锁定）编成锚定块里的附加分句。

    - 每条 body_lock item 一个分句，格式 keep the figure exactly as shown in {tag}: <item>；
      item 断言体形本身（如 very large, full, round bust …），tag 指向第一张参考图
      ——体形在每张参考图里都可见，选哪张开场都不悬空。
    - 这里**不加**任何「视角护栏」（不要画第二人/不照搬设定板版式之类的措辞）：
      出图模型对概念词是显性召唤物，「设定板分格/第二个人物/多视图」写进它读到的
      提示词反而把版式复现出来（2026-10-27 实测：护栏写进锚定句后，正面/r45 变体
      从单人变 3 人设定板版式）。视角裁剪走 build_rewrite_messages 的系统硬规则，
      只给改写 LLM 看，不落进出图提示词。
    - 无 body_lock 或无参考图 → 空列表（锚定块不变，存量行为逐字不变）。
    """
    items = [str(x).strip() for x in (body_lock or []) if str(x).strip()]
    if not items or not refs:
        return []
    # 体形锚指向第一张非 pose 参考图：人偶线稿没有可核对的体形，别把锚挂到它上。
    anchor_refs = [r for r in refs if str(r.get("kind") or "") != "pose"] or refs
    tag = str(anchor_refs[0].get("tag") or "<image1>")
    return [f"keep the figure exactly as shown in {tag}: {item}" for item in items]

# required_label（带字表情包）专用尾句：中文文案由程序按原文追加，这里只禁「额外」文字。
# 不要全局替换旧尾句——旧预设仍逐字使用 REFERENCE_TAIL_SENTENCE。
REFERENCE_TAIL_SENTENCE_REQUIRED_LABEL: str = (
    "Take only the character's identity from the reference images and create a new pose, "
    "camera angle, background, and lighting. One character only. The prescribed Chinese "
    "lettering is appended verbatim after this description and is the only text allowed "
    "in the image; render no other text, captions, logos, or watermarks."
)

# 姿势库（role=pose）专用尾句：旧尾句的「create a new pose … One character only」
# 与姿势参考正相反 —— 有姿势参考时姿势是权威、人数跟随参考图（2人絡み 线是双人位，
# 写死 one character 会被砍成单人）。人偶线稿的风格污染也在这里排掉
# （锚定句开头的"same art style"会被灰线稿拉偏）。
# sticker（required_label）场景不配姿势库参考，保持 sticker 尾句不动。
REFERENCE_TAIL_SENTENCE_POSE: str = (
    "Follow the pose, body posture, limb placement, and camera framing of the mannequin "
    "pose reference image(s) exactly, with the same number of figures as the pose "
    "reference shows. Take the character's identity, face, hairstyle, and outfit only "
    "from the other reference images and the subject profile, and create the background "
    "and lighting. Never render a gray mannequin or copy its line-drawing style. "
    "No text."
)

# depth 姿势库（pose-db-02）专用尾句：深度图锚的是身体动作 + 前后遮挡关系
# （白=近），禁令里同时点名线稿与深度图样式，防止灰阶底色被照搬进成图。
REFERENCE_TAIL_SENTENCE_POSE_DEPTH: str = (
    "Follow the pose, body posture, limb placement, spatial depth, occlusion, and "
    "camera framing of the mannequin depth-map reference image(s) exactly, with the "
    "same number of figures as the pose reference shows. Take the character's identity, "
    "face, hairstyle, and outfit only from the other reference images and the subject "
    "profile, and create the background and lighting. Never render a gray mannequin, "
    "its line-drawing style, or its depth-map shading. No text."
)

# 人脸库专用尾句（无姿势参考时）：旧尾句的「Take only the character's identity
# from the reference images」对 face 参考依然成立，但要点名「同一个虚构人物 +
# 禁真人画像」——AI 架构人物图的锚定是身份级的，说轻了模型会自由变脸。
REFERENCE_TAIL_SENTENCE_FACE: str = (
    "Render the very same fictional person the face reference image(s) show — the same "
    "facial identity, and with a styling reference also the same outfit as shown "
    "there — while the pose, camera angle, background, and lighting are created anew. "
    "One character only. Never depict a real person. No text."
)


# ---- structured outputs（response_format: json_schema） ----
# 只用 type/properties/required/additionalProperties 兼容 strict 模式（OpenAI/OpenRouter
# 拒绝 minItems 等扩展关键字）；条目数量不靠 schema 保证，由缺条补调兜底。
REWRITE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["id", "prompt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["variants"],
    "additionalProperties": False,
}

DRAFT_SCHEMA: dict = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
    "additionalProperties": False,
}


def reference_anchor_sentence(
    refs: list[dict],
    *,
    text_policy: str = "forbid",
    outfit_mode: bool = False,
    hairstyle_mode: bool = False,
    body_lock: list[str] | None = None,
) -> str:
    """按勾选的参考图顺序生成逐图分工锚定句；无参考图 → 空串（改写省略锚定句）。

    text_policy 旧默认 forbid → 旧尾句（One character only, no text）；
    required_label（sticker）→ 尾句换成「唯一允许的文字是程序附加的指定文案」。

    outfit_mode（服装组合）→ 分句换成「参考图不管服装」的版本：服装由服装预设给定，
    参考图只提供身份/体型/画风。两套都要会给模型两个互相矛盾的服装指令。

    hairstyle_mode（发型组合）→ 分句换成「参考图不管头发结构」的版本（发色/脸仍由
    参考图提供）：发型结构由发型预设给定，headshot/side/behind 分句里所有「照抄/沿用
    头发」的表述去掉。与服装组合同时开时再用一套融合分句表（reference_clause_table）。

    body_lock（主体档案的可选体形锁定）→ 追加成锚定块的附加分句（紧跟逐图分工句）：
    换装时体量锚的主要文本来源。走锚定块而不是新开材料块，是为了继承现有硬规则
    （逐字采用、全批相同、不得增删分句），不用动冻结的相关规则句。
    按视角裁剪这批体量句的授权在 build_rewrite_messages 的系统硬规则里（只进
    改写 LLM 的输入，不进锚定句与出图提示词）。
    """
    clauses = []
    table = reference_clause_table(outfit_mode, hairstyle_mode)
    for ref in refs or []:
        kind = str(ref.get("kind") or "")
        clause = table.get(kind)
        # 姿势库参考按展示形态二选一：depth 库（pose-db-02）用深度图分句（不是线稿）
        if kind == "pose" and str(ref.get("display_type") or "") == "depth":
            clause = table.get("pose_depth") or clause
        # 人脸库参考按展示形态二选一：styling 造型图锚脸+发型+成套服装（face 只锚脸）
        if kind == "face" and str(ref.get("display_type") or "") == "styling":
            clause = table.get("face_styling") or clause
        if not clause:
            continue
        clauses.append(clause.format(tag=ref.get("tag") or "<image1>"))
    clauses.extend(body_lock_clauses(body_lock, refs))
    if not clauses:
        return ""
    # 尾句五选一：sticker（不配姿势库参考）→ sticker 版；引用了 depth 库 → depth 版：
    # 深度图锚的是前后关系（越白越近），禁令也要点名深度图样式；引用了姿势库 →
    # pose 版（旧尾句的 create a new pose / One character only 与姿势参考正相反）；
    # 引用了人脸库（无姿势参考时）→ face 版（同一个虚构人物 + 禁真人画像）；其余 →
    # 旧尾句（存量行为逐字不变）。
    has_pose_ref = any(str(r.get("kind") or "") == "pose" for r in refs or [])
    has_depth_pose = any(
        str(r.get("kind") or "") == "pose" and str(r.get("display_type") or "") == "depth"
        for r in refs or []
    )
    has_face_ref = any(str(r.get("kind") or "") == "face" for r in refs or [])
    if text_policy == "required_label":
        tail = REFERENCE_TAIL_SENTENCE_REQUIRED_LABEL
    elif has_depth_pose:
        tail = REFERENCE_TAIL_SENTENCE_POSE_DEPTH
    elif has_pose_ref:
        tail = REFERENCE_TAIL_SENTENCE_POSE
    elif has_face_ref:
        tail = REFERENCE_TAIL_SENTENCE_FACE
    else:
        tail = REFERENCE_TAIL_SENTENCE
    body = "; ".join(clauses)
    body = body[0].upper() + body[1:]
    return (
        "Use the same art style, medium, and rendering as the reference images. "
        f"{body}. {tail}"
    )


def _clip_template(template_md: str) -> str:
    text = (template_md or "").strip()
    if len(text) > _TEMPLATE_MAX_CHARS:
        return text[:_TEMPLATE_MAX_CHARS] + "\n…（模板过长已截断）"
    return text


def _profile_payload(subject: SubjectProfile) -> str:
    return json.dumps(subject.model_dump(), ensure_ascii=False, indent=1)


def _angle_norm_lines(intents: list[dict]) -> str:
    """仅注入这批变体实际出现的视角语句（保序去重）。"""
    angles: list[str] = []
    for intent in intents:
        angle = str(intent.get("angle") or "")
        if angle and angle not in angles:
            angles.append(angle)
    return "\n".join(f"- {a}: {ANGLE_VIEW_SENTENCES[a]}" for a in angles if a in ANGLE_VIEW_SENTENCES)


def _language_rule(language: str) -> tuple[str, str]:
    """起草提示词的语言规定与 JSON 示例值（en 短语保持旧文案逐字不变）。

    any = 不做语言强制（黑不提）：不写语言规定，输出语言由模型按材料自行决定；
    zh/en 分支给出对应规定，措辞风格对齐 _prompt_string_rule。
    """
    if language == "en":
        return (
            "提示词为英文，可多句自然衔接，直接用于 Stable Diffusion / Qwen-Image 类模型；"
            "不出现中文、小节标题（如【】）或 {{}} 占位符；",
            "<英文提示词>",
        )
    if language == "zh":
        return (
            "提示词为中文（专有名词、标签可保留原文），可多句自然衔接，"
            "直接用于 Stable Diffusion / Qwen-Image 类模型；"
            "不出现英文整句、小节标题（如【】）或 {{}} 占位符；",
            "<提示词>",
        )
    return (
        "提示词可多句自然衔接，直接用于 Stable Diffusion / Qwen-Image 类模型；"
        "不出现小节标题（如【】）或 {{}} 占位符；",
        "<提示词>",
    )


def draft_subject_image_prompt_messages(
    subject: SubjectProfile | None,
    kind: str,
    template_md: str | None = None,
    *,
    material: str | None = None,
    language: str = "any",
) -> list[dict]:
    """为主体图起草提示词的 messages（chat_json → {"prompt": "<提示词>"}）。

    材料三选任意组合（至少一项，全缺报 ValueError）：
    - subject：结构化主体档案（锁项加权锚点）；
    - template_md：角色全场景通用提示词模板（超上限截断，与入库行为一致）；
    - material：自定义参考资料原文（一段角色描述、character_sheet 文生图提示词、任何资料）——
      没有档案/模板时也能起草。

    language：zh=强制中文 / en=强制英文 / any=不写语言规定（缺省，输出语言跟随材料）。
    """
    brief = _KIND_BRIEFS.get(kind)
    if brief is None:
        raise ValueError(f"未知图片类型：{kind}")
    if language not in LANGUAGE_CHOICES:
        raise ValueError(f"未知语言选项：{language}（支持 {'/'.join(LANGUAGE_CHOICES)}）")
    subject_provided = subject is not None
    template_text = _clip_template(template_md or "").strip()
    material_text = (material or "").strip()

    sections: list[str] = []
    if subject_provided:
        sections.append(f"【角色档案】\n{_profile_payload(subject)}")
    if template_text:
        sections.append(f"【角色全场景通用提示词模板】\n{template_text}")
    if material_text:
        sections.append(f"【自定义参考资料】\n{material_text}")
    if not sections:
        raise ValueError("缺少起草材料：角色档案、模板、自定义参考资料至少给一项")

    user = "\n\n".join(sections)

    source_parts: list[str] = []
    if subject_provided:
        source_parts.append("角色档案")
    if template_text:
        source_parts.append("「角色全场景通用提示词模板」")
    if material_text:
        source_parts.append("自定义参考资料")
    source_desc = source_parts[0] if len(source_parts) == 1 else "、".join(source_parts[:-1]) + "与" + source_parts[-1]

    system = (
        "你是图像生成提示词专家。根据给定的"
        f"{source_desc}，为对应的角色撰写「{KIND_LABELS.get(kind, kind)}」（{kind}）提示词。\n"
        f"- 画面要求：\n{brief}\n"
    )
    if template_text:
        system += (
            "- 角色外观、画风与服饰锁定项以模板对应版本为准：模板存在多个版本（正常/精简、多套服装、"
            "双主体分段）时只选用与该主体匹配的一套，不混合；模板显式禁止的内容"
            "（文字、logo、图案、粗描边、3D 等）写成对应的显式 no ... 条款。\n"
        )
    else:
        system += (
            "- 资料里显式禁止的内容（文字、logo、图案、粗描边、3D 等）写成对应的显式 no ... 条款。\n"
        )
    lang_rule, example_value = _language_rule(language)
    system += (
        f"- {lang_rule}"
        "提示词里只允许出现上面给出的 <image1> 标签（若有），不得出现 <image2> 等未提供的标签。\n"
        f'只输出 JSON：{{"prompt": "{example_value}"}}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---- LLM 改写的输出语言 ----
# zh=强制中文 / en=强制英文 / any=不做强制性限制（提示词里根本不写语言规定，
# 输出语言由模型自行决定，实际会跟随模板与意图的语言）。
LANGUAGE_CHOICES = ("zh", "en", "any")
LANGUAGE_LABELS = {"zh": "中文", "en": "英文", "any": "不限语言"}


def _rewrite_lead(language: str) -> str:
    """「为每条变体重写一条完整…提示词」的定语（any → 不带定语）。"""
    adj = {"zh": "中文", "en": "英文"}.get(language, "")
    return f"为每条变体重写一条完整{adj}提示词"


def _prompt_string_rule(language: str, *, sticker: bool, outfit: bool = False, hairstyle: bool = False) -> str:
    """「每条 prompt 为一个…字符串」这条硬规则。

    en 分支逐字保持旧文案（不含中文 / 不出现中文的措辞不动），只是把
    「不出现中文」这句在 sticker 下省掉——sticker 的禁中文字句已单独规定。

    outfit=True（服装组合）/ hairstyle=True（发型组合）：描述段逐字保留英文原文
    （换装/换发描述译写容易走样，且程序兜底补写用的也是英文原文），
    所以中文分支要开例外；两者同时开时例外合并成一条。
    """
    if language == "zh":
        if outfit and hairstyle:
            return (
                "- 每条 prompt 为一个中文字符串（专有名词、标签可保留原文），"
                "不出现英文整句、【】小节标题或 {{}} 占位符；只输出提示词本身；"
                "唯一的例外是 outfit 与 hairstyle 字段的描述——它们逐字保留原文；\n"
            )
        if outfit:
            return (
                "- 每条 prompt 为一个中文字符串（专有名词、标签可保留原文），"
                "不出现英文整句、【】小节标题或 {{}} 占位符；只输出提示词本身；"
                "唯一的例外是 outfit 字段的服装描述——它逐字保留原文"
                "（服装预设里写的是中文就保持中文，不译写）；\n"
            )
        if hairstyle:
            return (
                "- 每条 prompt 为一个中文字符串（专有名词、标签可保留原文），"
                "不出现英文整句、【】小节标题或 {{}} 占位符；只输出提示词本身；"
                "唯一的例外是 hairstyle 字段的发型描述——它逐字保留英文原文；\n"
            )
        return (
            "- 每条 prompt 为一个中文字符串（专有名词、标签可保留原文），"
            "不出现英文整句、【】小节标题或 {{}} 占位符；只输出提示词本身；\n"
        )
    if language == "en":
        if sticker:
            return (
                "- 每条 prompt 为一个英文字符串，不出现【】小节标题或 {{}} 占位符；"
                "只输出提示词本身；\n"
            )
        if outfit:
            # 服装段逐字保留原文（服装预设里写什么语言就是什么语言）。
            # 预设写中文时整条 prompt 会出现中文服装句——这是已知的取舍，
            # 运行时会在 run 的 warnings 里声明（纯英文模型读不懂中文服装描述）。
            return (
                "- 每条 prompt 为一个英文字符串，不出现中文、【】小节标题或 {{}} 占位符；"
                "只输出提示词本身；"
                "唯一的例外是 outfit 字段的服装描述——它逐字保留原文；\n"
            )
        return (
            "- 每条 prompt 为一个英文字符串，不出现中文、【】小节标题或 {{}} 占位符；"
            "只输出提示词本身；\n"
        )
    return "- 每条 prompt 为一个提示词字符串，不出现【】小节标题或 {{}} 占位符；只输出提示词本身；\n"


def _json_example(language: str) -> str:
    placeholder = {"zh": "chinese prompt", "en": "english prompt"}.get(language, "prompt")
    return f'只输出 JSON：{{"variants": [{{"id": "<variant_id>", "prompt": "<{placeholder}>"}}]}}'


# 改写管线注入的材料全是英文（参考图锚定句、视角规范、framing_guard、
# 背景/光照），所以「强制中文」= 让 LLM 把这些英文材料译写成中文再采用，
# 而不是逐字抄英文。三种模式的措辞差异集中在这里：
#   en  = 逐字抄英文（旧行为，逐字不变）
#   zh  = 译写成中文后采用，语义不得改变
#   any = 不限定语言，逐字或译写均可
#   锚定句分句对应不得增删这一条三种模式都一样。
_LANG_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "anchor": "逐字采用给定的【参考图分工锚定句】整句，不得改写、增删分句；",
        "anchor_same": "该锚定句在本批所有变体中逐字相同；",
        "angle": "按【视角规范】里对应 angle 的英文语句写身体朝向、头部朝向与视线；",
        "full_body": "必须包含【视角规范】中的全身完整入镜句；",
        "guard": "必须纳入给定的 framing_guard 句子以阻断下半身；",
        "bg": "逐字采用给定的 background 与 lighting，不改写。",
    },
    "zh": {
        "anchor": "采用给定【参考图分工锚定句】的语义写成中文，分句对应，不得增删分句；",
        "anchor_same": "该锚定句在本批所有变体中语义一致；",
        "angle": "按【视角规范】里对应 angle 的语句写身体朝向、头部朝向与视线"
        "（规范句是英文，请译写成中文，语义不得改变）；",
        "full_body": "必须包含【视角规范】中全身完整入镜句的中文译文；",
        "guard": "必须纳入给定 framing_guard 句子的中文译文以阻断下半身；",
        "bg": "把给定的 background 与 lighting 译成中文后采用，语义不改写。",
    },
    "any": {
        "anchor": "采用给定【参考图分工锚定句】的语义（逐字采用或译写均可），不得增删分句；",
        "anchor_same": "该锚定句在本批所有变体中语义一致；",
        "angle": "按【视角规范】里对应 angle 的语句写身体朝向、头部朝向与视线"
        "（逐字采用或译写均可，语义不得改变）；",
        "full_body": "必须包含【视角规范】中全身完整入镜句的等价表述（逐字或译写均可）；",
        "guard": "必须纳入给定 framing_guard 句子的等价表述以阻断下半身；",
        "bg": "采用给定的 background 与 lighting，语义不改写（逐字或译写均可）。",
    },
}


def build_rewrite_messages(
    subject: SubjectProfile,
    template_md: str,
    refs: list[dict],
    intents: list[dict],
    instructions: str | None = None,
    *,
    text_policy: str = "forbid",
    composition_policy: str = "legacy",
    language: str = "any",
    barefoot: bool = False,
) -> list[dict]:
    """批量改写变体提示词的 messages（chat_json → {"variants": [{"id","prompt"}...]}）。

    refs: [{"kind": ..., "tag": "<imageN>"}, ...]（tag 按顺序分配）；
    intents: collect_intent 的输出（id/text/framing/angle/expression/background/lighting/framing_guard；
    服装组合另有 outfit/outfit_label、发型组合另有 hairstyle/hairstyle_label；
    sticker 时另有 purpose/text_policy/composition_policy 与各条 display_text）。

    policy=forbid（默认）：旧行为逐字不变——英文且不含中文，尾句 One character only, no text。
    policy=required_label（sticker）：LLM 只写英文画面描述（身份/动作/构图/绘字留白），
    最终中文文案由程序按 display_text 原文附加，因此意图里的 display_text/content 不进 LLM 输入；
    composition_policy=variant 时不注入全身站姿/固定正面头身同步等通用规范（构图以各条 text 为准）。

    language：改写输出的语言。zh=强制中文 / en=强制英文 / any=不做强制性限制（缺省，
    提示词里不出现任何语言规定，输出语言由模型自行决定）。sticker 强制 en——中文文案由
    程序按 display_text 原文追加，LLM 段一旦输出中文就会被 sticker 的禁字规则判为不合格。

    主体档案的 body_lock（可选体形锁定）自动进锚定块：换装重绘躯干时给胸/腰/臀
    体量留显式文本锚（见 reference_anchor_sentence）。锚定句里的体形分句全批逐字
    相同；但内容按各变体视角裁剪——授权与措辞禁忌在系统硬规则里（视角看不到的
    部位描述省略，且改写结果不得出现设定板/分格/第二个人物等版式词汇）。
    """
    sticker = text_policy == "required_label"
    if sticker:
        # 中文绘字由程序追加，LLM 段输出中文会被 sticker 禁字规则判为不合格 → 强制英文
        language = "en"
    # 服装/发型组合：意图里带 outfit / hairstyle 字段（预设的英文描述段）→
    # 锚定句与规则都要变（参考图不再提供服装/头发结构）。
    outfit_mode = any(str(i.get("outfit") or "").strip() for i in intents)
    hairstyle_mode = any(str(i.get("hairstyle") or "").strip() for i in intents)
    ref_lines = "\n".join(
        f"- {r['tag']}：{KIND_LABELS.get(r['kind'], r['kind'])}（{r['kind']}）"
        for r in refs
    )
    if sticker:
        system = (
            "你是角色图像提示词改写器（带字表情包预设）。输入：角色档案、角色全场景通用提示词模板（权威人设依据）、"
            "若干参考图说明（每张有 <imageN> 标签与用途），以及一批变体的结构化意图。\n"
            f"{_rewrite_lead(language)}，句子顺序固定：\n"
            "1) 参考图锚定：逐字采用给定的【参考图分工锚定句】整句，不得改写、增删分句；"
            "该锚定句在本批所有变体中逐字相同；\n"
            "2) 姿态与构图：意图 text 是本条构图的唯一权威（趴倒/探头/跳跃等都照写），"
            "按它写身体动作与范围、遮挡、视线与绘字留白，不要套用全身站姿、"
            "正面平视、头身同步或完整入镜等通用构图规范；\n"
            "3) 目标表情：按意图 expression 写；\n"
            "4) 背景/光照：逐字采用给定的 background 与 lighting，不改写。\n"
            "硬性规则：\n"
            "- 中文绘字由程序统一追加：不要输出任何中文字符，不要描述文字内容，"
            "不要写绘字指令（如 render the text…），也不要添加 no text / no writing 之类的"
            "文字禁句（程序负责追加指定文案与对应的禁令）；\n"
            "- 不在每条提示词里复述模板的整段角色设定，身份细节交给参考图；不混合模板的多个版本"
            "（正常/精简、多套服装）或双主体分段；\n"
            "- 保留自然解剖与身份一致性：被遮挡的细节按视角自然隐藏，不强行转体/错开肢体展示；"
            "不对称细节（痣、挑染、纹样）在左右朝向互换时保持贴身位置，不镜像；\n"
            + _prompt_string_rule(language, sticker=True)
            + "- 必须为输入列表中的每个 id 各输出一条，不得增删。\n"
            + _json_example(language)
        )
    else:
        phrase = _LANG_PHRASES.get(language, _LANG_PHRASES["any"])
        system = (
            "你是角色图像提示词改写器。输入：角色档案、角色全场景通用提示词模板（权威人设依据）、"
            "若干参考图说明（每张有 <imageN> 标签与用途），以及一批变体的结构化意图。\n"
            f"{_rewrite_lead(language)}，句子顺序固定：\n"
            "1) 参考图锚定：" + phrase["anchor"] + "没有参考图时省略该句；"
            + phrase["anchor_same"] + "\n"
            "2) 目标景别：采用意图 text 的景别/构图描述（full=全身 / chest=胸部以上 / head=头部特写）；\n"
            "3) 躯干/头部/视线：" + phrase["angle"] + "意图 text 中的朝向与姿态语义必须保留，不得削弱或反转；\n"
            "4) 目标表情：按意图 expression 写；angle 为 back 时省略表情句；l135/r135 只保留面部部分可见"
            "的说明，不细写眉眼嘴；\n"
            "5) 背景/光照：" + phrase["bg"] + "\n"
            "硬性规则：\n"
            "- 不在每条提示词里复述模板的整段角色设定，身份细节交给参考图；不混合模板的多个版本"
            "（正常/精简、多套服装）或双主体分段；\n"
            "- framing=full 时" + phrase["full_body"] + "framing 为 chest/head 时"
            + phrase["guard"] + "\n"
            "- 被遮挡的细节按视角自然隐藏，不强行转体/错开肢体展示；不对称细节（痣、挑染、纹样）在"
            "左右朝向互换时保持贴身位置，不镜像；\n"
            + ("- 体形锁定句的视角裁剪（唯一例外）：锚定句里的体形锁定分句"
               "（keep the figure exactly…）保持全批逐字相同，但它描述的体形按该变体的视角裁剪后写进 "
               "prompt：该视角能看到的部位描述必须采用（语义不改写）；看不到的部位描述省略，"
               "不要写成「虽然不可见但依然存在」一类的转述句，也不要为它增加任何额外构图内容；"
               "改写结果里绝不出现设定板、分格、多视图并排、第二个人物、复现面板这类词汇"
               "（这些词是出图模型的显性召唤物，写了就会被画出来）；\n"
               if (not sticker and subject.body_lock) else "")
            + _prompt_string_rule(language, sticker=False, outfit=outfit_mode, hairstyle=hairstyle_mode)
            + ("- 发型：每条意图的 hairstyle 字段是该变体的发型（英文原文），是头发结构的唯一权威："
               "档案与参考图中的原发型结构描述不再采用——头发长度、刘海、分缝、层次与卷度全部按 "
               "hairstyle 原文逐字写出，发色仍按参考图与档案保留；档案与参考图里的帽子、发箍、"
               "发卡等头部佩戴物不保留（发型预设默认裸头），每条 prompt 在发型句之后逐字写出裸头声明："
               f"“{BARE_HEAD_SENTENCE}”；不得出现「摘掉帽子/卸下头饰」这类把原图内容当主角的措辞；"
               "两组都完整写出，不改写、不省略；发型句与裸头声明写在参考图锚定句之后、目标景别句之前"
               "（贴住锚定句，压过参考图里原发型的先验；服装段排在这句之前的第一位）；\n"
               if hairstyle_mode else "")
            + ("- 服装：每条意图的 outfit 字段是该变体必须穿着的服装（原文语言即该段语言，"
               "服装预设里写的是中文就用中文写、是英文就用英文写，不译写）。"
               "必须在该条 prompt 里完整写出这套服装，逐字采用 outfit 原文，"
               "不改写、不省略、不换用参考图里那套衣服；"
               "写成该条 prompt 的第一句——排在参考图锚定句、目标景别句、姿态/表情句与"
               "背景/光照句之前（换装图里服装是主角）；"
               "若该条意图带 outfit_policy_note，按它执行：其中声明不得出现的内容"
               "（角色原本那套衣服、配件）不要写进该条提示词；\n"
               if outfit_mode else "")
            + "- 必须为输入列表中的每个 id 各输出一条，不得增删。\n"
            + _json_example(language)
        )
    user_parts = [
        f"【角色档案】\n{_profile_payload(subject)}",
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（未提供模板）'}",
        f"【参考图说明】\n{ref_lines or '（无参考图）'}",
    ]
    anchor = reference_anchor_sentence(
        refs, text_policy=text_policy, outfit_mode=outfit_mode,
        hairstyle_mode=hairstyle_mode, body_lock=subject.body_lock,
    )
    if anchor:
        user_parts.append(f"【参考图分工锚定句】（逐字采用，全批相同）\n{anchor}")
    # display_text / lettering 是绘字段来源，不进 LLM 输入（防止改写或复制中文文案）
    shown = intents if not sticker else [
        {k: v for k, v in intent.items() if k not in ("display_text", "lettering")}
        for intent in intents
    ]
    if sticker:
        user_parts.append(
            "【一致性规范】（构图按各条意图 text；不套用通用站姿规范）\n- any view: " + OCCLUSION_SENTENCE
        )
    else:
        norms = _angle_norm_lines(intents)
        spec_lines = [
            "- full framing: "
            + (FULL_BODY_CAMERA_SENTENCE_BAREFOOT if barefoot else FULL_BODY_CAMERA_SENTENCE)
        ]
        if norms:
            spec_lines.append(norms)
        spec_lines.append("- any view: " + OCCLUSION_SENTENCE)
        user_parts.append("【视角规范】（顺序固定的第 3) 句按对应 angle 采用；不改变朝向语义）\n" + "\n".join(spec_lines))
    if instructions and instructions.strip():
        user_parts.append(f"【用户附加要求】\n{instructions.strip()}")
    user_parts.append(
        "【变体列表】\n" + json.dumps(shown, ensure_ascii=False, indent=1)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]