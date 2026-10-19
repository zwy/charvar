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
}

REFERENCE_TAIL_SENTENCE: str = (
    "Take only the character's identity from the reference images and create a new pose, "
    "camera angle, background, and lighting. One character only, no text."
)


def reference_anchor_sentence(refs: list[dict]) -> str:
    """按勾选的参考图顺序生成逐图分工锚定句；无参考图 → 空串（改写省略锚定句）。"""
    clauses = []
    for ref in refs or []:
        clause = REFERENCE_ROLE_CLAUSES.get(str(ref.get("kind") or ""))
        if not clause:
            continue
        clauses.append(clause.format(tag=ref.get("tag") or "<image1>"))
    if not clauses:
        return ""
    body = "; ".join(clauses)
    body = body[0].upper() + body[1:]
    return (
        "Use the same art style, medium, and rendering as the reference images. "
        f"{body}. {REFERENCE_TAIL_SENTENCE}"
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


def draft_subject_image_prompt_messages(
    subject: SubjectProfile,
    kind: str,
    template_md: str,
) -> list[dict]:
    """为主体图起草提示词的 messages（chat_json → {"prompt": "<英文提示词>"}）。"""
    brief = _KIND_BRIEFS.get(kind)
    if brief is None:
        raise ValueError(f"未知图片类型：{kind}")
    system = (
        "你是 ComfyUI 图像生成提示词专家。根据给定的角色档案与「角色全场景通用提示词模板」，"
        f"为该角色的「{KIND_LABELS.get(kind, kind)}」（{kind}）撰写一条提示词。\n"
        f"- 画面要求：\n{brief}\n"
        "- 角色外观、画风与服饰锁定项以模板对应版本为准：模板存在多个版本（正常/精简、多套服装、"
        "双主体分段）时只选用与该主体匹配的一套，不混合；模板显式禁止的内容"
        "（文字、logo、图案、粗描边、3D 等）写成对应的显式 no ... 条款。\n"
        "- 提示词为英文，可多句自然衔接，直接用于 Stable Diffusion / Qwen-Image 类模型；"
        "不出现中文、小节标题（如【】）或 {{}} 占位符；提示词里只允许出现上面给出的 <image1> 标签"
        "（若有），不得出现 <image2> 等未提供的标签。\n"
        '只输出 JSON：{"prompt": "<英文提示词>"}'
    )
    user = (
        f"【角色档案】\n{_profile_payload(subject)}\n\n"
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（未提供模板）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_rewrite_messages(
    subject: SubjectProfile,
    template_md: str,
    refs: list[dict],
    intents: list[dict],
    instructions: str | None = None,
) -> list[dict]:
    """批量改写变体提示词的 messages（chat_json → {"variants": [{"id","prompt"}...]}）。

    refs: [{"kind": ..., "tag": "<imageN>"}, ...]（tag 按顺序分配）；
    intents: collect_intent 的输出（id/text/framing/angle/expression/background/lighting/framing_guard）。
    """
    ref_lines = "\n".join(
        f"- {r['tag']}：{KIND_LABELS.get(r['kind'], r['kind'])}（{r['kind']}）"
        for r in refs
    )
    system = (
        "你是角色图像提示词改写器。输入：角色档案、角色全场景通用提示词模板（权威人设依据）、"
        "若干参考图说明（每张有 <imageN> 标签与用途），以及一批变体的结构化意图。\n"
        "为每条变体重写一条完整英文提示词，句子顺序固定：\n"
        "1) 参考图锚定：逐字采用给定的【参考图分工锚定句】整句，不得改写、增删分句；"
        "没有参考图时省略该句；该锚定句在本批所有变体中逐字相同；\n"
        "2) 目标景别：采用意图 text 的景别/构图描述（full=全身 / chest=胸部以上 / head=头部特写）；\n"
        "3) 躯干/头部/视线：按【视角规范】里对应 angle 的英文语句写身体朝向、头部朝向与视线；"
        "意图 text 中的朝向与姿态语义必须保留，不得削弱或反转；\n"
        "4) 目标表情：按意图 expression 写；angle 为 back 时省略表情句；l135/r135 只保留面部部分可见"
        "的说明，不细写眉眼嘴；\n"
        "5) 背景/光照：逐字采用给定的 background 与 lighting，不改写。\n"
        "硬性规则：\n"
        "- 不在每条提示词里复述模板的整段角色设定，身份细节交给参考图；不混合模板的多个版本"
        "（正常/精简、多套服装）或双主体分段；\n"
        "- framing=full 时必须包含【视角规范】中的全身完整入镜句；framing 为 chest/head 时必须纳入"
        "给定的 framing_guard 句子以阻断下半身；\n"
        "- 被遮挡的细节按视角自然隐藏，不强行转体/错开肢体展示；不对称细节（痣、挑染、纹样）在"
        "左右朝向互换时保持贴身位置，不镜像；\n"
        "- 每条 prompt 为一个英文字符串，不出现中文、【】小节标题或 {{}} 占位符；只输出提示词本身；\n"
        "- 必须为输入列表中的每个 id 各输出一条，不得增删。\n"
        '只输出 JSON：{"variants": [{"id": "<variant_id>", "prompt": "<english prompt>"}]}'
    )
    user_parts = [
        f"【角色档案】\n{_profile_payload(subject)}",
        f"【角色全场景通用提示词模板】\n{_clip_template(template_md) or '（未提供模板）'}",
        f"【参考图说明】\n{ref_lines or '（无参考图）'}",
    ]
    anchor = reference_anchor_sentence(refs)
    if anchor:
        user_parts.append(f"【参考图分工锚定句】（逐字采用，全批相同）\n{anchor}")
    norms = _angle_norm_lines(intents)
    spec_lines = ["- full framing: " + FULL_BODY_CAMERA_SENTENCE]
    if norms:
        spec_lines.append(norms)
    spec_lines.append("- any view: " + OCCLUSION_SENTENCE)
    user_parts.append("【视角规范】（顺序固定的第 3) 句按对应 angle 采用；不改变朝向语义）\n" + "\n".join(spec_lines))
    if instructions and instructions.strip():
        user_parts.append(f"【用户附加要求】\n{instructions.strip()}")
    user_parts.append(
        "【变体列表】\n" + json.dumps(intents, ensure_ascii=False, indent=1)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]