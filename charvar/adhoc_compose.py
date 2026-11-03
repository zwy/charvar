"""Playground 库组合工作流的提示词拼装（网页 ``POST /adhoc/compose`` 的编排层）。

只做编排与校验：**文案一律来自 ``pose_prompt.build_prompt`` /
``build_costume_prompt``** —— 与 CLI ``pose prompt`` / ``costume prompt`` 同源同
结构（待完善清单 B3/B4 红线：拼装不另造一条）。自由生图不经拼装（提示词就是
用户给的）；这里只管五种组合构成（单主体动作 / 双主体动作 / 人脸动作 / 换装）。

约束与 CLI 完全对齐（face 与主体档案互斥、双人只认 2人絡み、双人无服饰/人脸、
体形锚只在人脸流程手给、服饰条目必须有图）。返回的 references 是
``ReferenceSpec`` 序列 —— 顺序即提示词里 ``<image1>``…，出图时原样发
``/adhoc/generate``（服务端按 id 取文件，浏览器永远不给路径）。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from charvar import costumes as costumes_service
from charvar import faces as faces_service
from charvar import poses as poses_service
from charvar import pose_prompt, storage
from charvar.models import ReferenceSpec

WORKFLOWS = ("pose", "costume")


class ComposeError(ValueError):
    """构成不合法（条目不存在 / 约束冲突 / 没写动作描述）—— 端点层转 400。"""


class ComposeRequest(BaseModel):
    """拼装请求：与 CLI ``pose prompt`` / ``costume prompt`` 同一套语义。"""

    workflow: str  # pose（动作出图）/ costume（纯换装，无动作）
    subject_id: str | None = None  # 主体档案（单人或双人「甲」）
    subject2_id: str | None = None  # 双人「乙」（仅 2人絡み）
    face_id: str | None = None  # 人脸库身份锚（与主体档案互斥，不支持双人）
    face_source: str | None = None  # 人脸库 id（face_id 非空时）
    pose_id: str | None = None  # 动作 id（pose 工作流必填；costume 工作流不带）
    pose_angle: str | None = None  # 视角（缺省正面）
    pose_source: str | None = None  # 姿势库 id（线稿/深度）
    costume_id: str | None = None  # 服饰条目 id（换装必填；动作出图可选）
    costume_source: str | None = None  # 服饰库 id（item_id 跨库重叠，必须可追溯）
    body_lock: list[str] = Field(default_factory=list)  # 体形锚（仅人脸流程手给）


def _clean(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _costume_ref(source: str | None, item_id: str | None) -> str:
    """条目引用必须写全 ``source/item``（两站点 + 自建库 id 重叠，裸 id 会指错）。"""
    return f"{_clean(source) or costumes_service.SOURCE_ID}/{_clean(item_id)}"


def compose(conn, req: ComposeRequest) -> dict:
    """校验 + 拼装提示词 + 生成参考图规格；返回 {prompt, references, meta}。"""
    workflow = str(req.workflow or "").strip()
    if workflow not in WORKFLOWS:
        raise ComposeError(f"未知工作流：{workflow}（可选 {'/'.join(WORKFLOWS)}）")
    subject_id = _clean(req.subject_id)
    subject2_id = _clean(req.subject2_id)
    face_id = _clean(req.face_id)
    pose_id = _clean(req.pose_id)
    costume_id = _clean(req.costume_id)
    body_lock_text = [str(x).strip() for x in req.body_lock if str(x).strip()] or None

    if workflow == "pose" and not pose_id:
        raise ComposeError("动作工作流必须选择一个动作（含视角）")
    if workflow == "costume" and pose_id:
        raise ComposeError("换装工作流不接姿势参考 —— 要动作用动作工作流")
    if face_id and (subject_id or subject2_id):
        raise ComposeError(
            "人脸库条目本身就是身份锚，与主体档案互斥：两个身份来源的图会互相揉脸（要体形锚用 body_lock）"
        )
    if body_lock_text and not face_id:
        raise ComposeError("体形锚只在人脸库身份锚流程有效（主体档案时走档案的 body_lock）")
    if workflow == "costume" and not costume_id:
        raise ComposeError("换装工作流必须选择一条服饰（有参考图的条目）")
    if not subject_id and not face_id:
        raise ComposeError("缺少身份锚：主体档案或人脸库条目二选一")
    body_lock: list[str] | None = None

    pose_source = str(_clean(req.pose_source) or poses_service.SOURCE_ID)
    face_source = str(_clean(req.face_source) or faces_service.SOURCE_ID)
    costume_source = str(_clean(req.costume_source) or costumes_service.SOURCE_ID)

    # ---- 服饰（换装必填；动作出图可选） ----
    costume_display = "ghost"
    costume_note = ""
    costume_policy: dict | None = None
    c_has_image = False
    costume_ref = ""
    if costume_id:
        try:
            c_row = costumes_service.resolve_ref(conn, _costume_ref(req.costume_source, costume_id))
            costume_ref = _costume_ref(c_row["source_id"], c_row["item_id"])
            costume_note = costumes_service.note_text(conn, costume_ref)
            costume_display = str(c_row["display_type"] or "ghost")
            costume_policy = (
                json.loads(c_row["subject_policy"])
                if str(c_row["subject_policy"] or "").strip()
                else None
            )
            # 与 CLI 同判：参考图锚定要图，纯文本条目用不了
            c_has_image = bool(
                str(c_row["file_path"] or "").strip() or str(c_row["image_path"] or "").strip()
            )
        except costumes_service.CostumeError as exc:
            raise ComposeError(str(exc)) from exc

    # ---- 身份锚：主体档案（按线型选槽位）或人脸库条目 ----
    face_section: str | None = None
    slot_label, slot = "front", None
    slot2 = None
    line = ""
    kind = "full"
    if face_id:
        try:
            face_section = faces_service.item_section(conn, face_id, source=face_source)
        except faces_service.FaceError as exc:
            raise ComposeError(str(exc)) from exc
        body_lock = body_lock_text
    else:
        slot2 = storage.get_subject_image(conn, subject2_id, "front") if subject2_id else None
        profile = storage.get_subject(conn, subject_id)
        if profile is None:
            raise ComposeError(f"主体档案不存在：{subject_id}")

    # ---- 姿势（pose 工作流） ----
    pose_text_only = False
    if workflow == "pose":
        try:
            action = poses_service.pose_action(conn, pose_id, req.pose_angle or "", pose_source)
        except poses_service.PoseError as exc:
            raise ComposeError(str(exc)) from exc
        if not str(action["action_text"] or "").strip():
            angle_zh = poses_service.angle_zh(action["angle"])
            raise ComposeError(
                f"该动作视角还没有动作描述：{action['pose_id']} / {angle_zh}"
                "（先在动作库里选这条编辑描述，或 charvar pose action … --set）"
            )
        try:
            row = conn.execute(
                "SELECT line, file_path FROM pose_images WHERE source = ? AND pose_id = ? AND angle = ?",
                (pose_source, action["pose_id"], action["angle"]),
            ).fetchone()
        except Exception as exc:  # pragma: no cover - pose_action 已校验过组合存在
            raise ComposeError(str(exc)) from exc
        if row is None:
            raise ComposeError(f"姿势库没有这个动作视角：{pose_source}/{action['pose_id']}/{action['angle']}")
        line = str(row["line"] or "")
        kind = pose_prompt.kind_of_line(line)
        pose_display = str((poses_service.POSE_SOURCES.get(pose_source) or {}).get("display_type") or "line")
        # 自建文本库（display_type=text）的无图动作走文本锚：动作描述即锚，无姿势参考图槽位
        pose_text_only = not str(row["file_path"] or "").strip()

    if subject_id:
        # 槽位选择对齐 CLI：上半身动作取 headshot（缺则回退 front），全身/双人 front
        anchor_line = line if workflow == "pose" else ""
        try:
            slot_label, slot = poses_service.subject_slot_for_prompt(conn, subject_id, anchor_line)
        except poses_service.PoseError as exc:
            raise ComposeError(str(exc)) from exc
        if slot is None or not str(slot.get("file_path") or "").strip():
            raise ComposeError(f"主体档案没有 {slot_label} 槽位图：{subject_id}")
        # 换装才带档案 body_lock（对齐 CLI：subject and costume 才传；纯动作不传）
        if costume_id:
            body_lock = list(profile.body_lock) if profile.body_lock else None

    # ---- 构成约束（与 CLI 的话术一致） ----
    if workflow == "pose":
        if subject2_id and kind != "pair":
            raise ComposeError("「乙」主体只对双人（2人絡み）动作有效")
        if costume_id and kind == "pair":
            raise ComposeError("双人动作暂不支持服装参考（仅单人两类工作流接入服装）")
        if face_id and kind == "pair":
            raise ComposeError("双人动作暂不支持人脸身份锚（双人身份走两个主体档案）")
    if subject2_id and slot2 is None:
        raise ComposeError(f"主体档案没有 front 槽位图：{subject2_id}")

    # ---- 提示词拼装（同一份 pose_prompt 文案） ----
    # 纯文本条目（无参考图）走文本锚：服饰文本整段进提示词、无服装图槽位。
    # 只支持 replace —— add 类没有实测过的文本锚措辞，显式 400 不发明分叉。
    costume_text_only = bool(costume_id) and not c_has_image
    if costume_text_only:
        try:
            pose_prompt.costume_text_mode_ok(c_has_image, costume_policy)
        except ValueError as exc:
            raise ComposeError(str(exc)) from exc
        if not str(costume_note or "").strip():
            raise ComposeError(
                f"这条服饰条目没有文本层（prompt_text / keywords 都为空）"
                f"—— 文本锚定没有可锚的内容：{costume_ref}"
            )
    if workflow == "costume":
        prompt = pose_prompt.build_costume_prompt(
            body_lock=body_lock,
            costume_keywords=costume_note or None,
            costume_display=costume_display,
            costume_policy=costume_policy,
            face_section=face_section,
            costume_has_image=not costume_text_only,
        )
    else:
        prompt = pose_prompt.build_prompt(
            line,
            action["action_text"],
            pair_two_subjects=bool(subject2_id),
            costume=costume_id is not None,
            costume_has_image=not costume_text_only,
            pose_has_image=not pose_text_only,
            body_lock=body_lock,
            costume_keywords=costume_note or None,
            costume_display=costume_display,
            costume_policy=costume_policy,
            pose_display=pose_display,
            face_section=face_section,
        )

    # ---- 参考图规格（顺序即 <image1>…，与 CLI refs.append 次序一致） ----
    refs: list[ReferenceSpec] = []
    if face_id:
        refs.append(ReferenceSpec(face_id=face_id, source=face_source, role="face"))
    else:
        refs.append(ReferenceSpec(subject_id=subject_id, kind=slot_label, role=slot_label))
        if subject2_id:
            refs.append(ReferenceSpec(subject_id=subject2_id, kind="front", role="front"))
    if workflow == "pose" and not pose_text_only:
        refs.append(
            ReferenceSpec(pose_id=action["pose_id"], angle=action["angle"], source=pose_source, role="pose")
        )
    if costume_id and not costume_text_only:
        refs.append(ReferenceSpec(costume_id=_clean(costume_id), source=costume_source, role="costume"))

    meta = {
        "workflow": workflow,
        "line": line,
        "line_kind": kind,
        "slot_label": slot_label if not face_id else "",
        "pose_display": pose_display if workflow == "pose" else "",
        "face_section": face_section or "",
        "costume_display": costume_display if costume_id else "",
        "costume_mode": pose_prompt.costume_mode(costume_policy) if costume_id else "",
        "costume_text_only": costume_text_only,
        "pose_text_only": pose_text_only,
    }
    return {
        "prompt": prompt,
        "references": [r.model_dump() for r in refs],
        "meta": meta,
    }