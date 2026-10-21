"""主体档案图片（需求 §主体档案 1）：每个主体 5 种固定图片槽位。

- kind 枚举集中在此：character_sheet（半身像＋三视图）先行，其余 4 种基于它
  用 image-edit workflow 生成（两步流程，需求已确认）；headshot/front/side/behind
  也可以由用户直接上传。
- 状态机：empty（无行）→ queued（已提交 ComfyUI，等完成/超时补拉）→ ready / error。
- 不走 runs 表（单图生命周期，不污染历史记录），但完整复用 ComfyUI 管道：
  构造临时 GenerateResult（不落库）走 submit()，产物经 assets._download_output 固化。
- 所有随机（seed）由调用方显式传入，模块本身不引入随机源。
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel

from charvar import assets, paths, reference_images, storage
from charvar.exporters import comfyui as comfyui_exporter
from charvar.models import GenerateResult, PromptItem, SubjectProfile

IMAGE_KINDS: tuple[str, ...] = ("character_sheet", "headshot", "front", "side", "behind")
ImageKind = Literal["character_sheet", "headshot", "front", "side", "behind"]
# 两步流程：这 4 种需要 character_sheet 先 ready（生成时作为参考图上传）
SHEET_DEPENDENT_KINDS = frozenset({"headshot", "front", "side", "behind"})

KIND_LABELS: dict[str, str] = {
    "character_sheet": "人物半身像＋三视图",
    "headshot": "人物半身像",
    "front": "全身正面",
    "side": "全身侧面",
    "behind": "全身背面",
}

_UPLOAD_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_SHEET_FRAMING = None  # 三视图含全身，禁用 closeup negative 兜底
_KIND_FRAMING: dict[str, str | None] = {
    "character_sheet": _SHEET_FRAMING,
    "headshot": "chest",
    "front": "full",
    "side": "full",
    "behind": "full",
}


class SubjectImage(BaseModel):
    """subject_images 表的一行。"""

    subject_id: str
    kind: str
    status: Literal["empty", "queued", "ready", "error"] = "empty"
    source: Literal["generated", "uploaded"] | None = None
    prompt: str = ""
    file_path: str | None = None
    prompt_id: str | None = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class SubjectImageSlot(SubjectImage):
    """API 返回的槽位：补 URL 与默认尺寸。"""

    label: str = ""
    is_ready: bool = False
    original_url: str | None = None
    thumb_url: str | None = None
    aspect_ratio: str = "3:4"
    resolution: str = "1k"


class SubjectImageError(ValueError):
    """业务错误（api 层按 status 转 4xx）。"""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------- 目录与文件 ----------

def subject_dir(subject_id: str) -> Path:
    if not subject_id or not all(c.isalnum() or c in "_-" for c in subject_id):
        raise SubjectImageError(f"非法主体 id：{subject_id}")
    return paths.subjects_dir() / subject_id


def _original_file(subject_id: str, kind: str) -> Path | None:
    for suffix in _UPLOAD_SUFFIXES:
        candidate = subject_dir(subject_id) / f"{kind}{suffix}"
        if candidate.exists():
            return candidate
    return None


def find_reference_file(subject_id: str, kind: str) -> Path | None:
    """按 kind 找主体图原文件（OpenRouter 参考图通路用；找不到返 None）。"""
    if kind not in IMAGE_KINDS:
        return None
    return _original_file(subject_id, kind)


def thumb_file(subject_id: str, kind: str) -> Path:
    return subject_dir(subject_id) / "thumbs" / f"{kind}.webp"


def _remove_stale_kind_files(subject_id: str, kind: str, keep: Path) -> None:
    """清掉同 kind 其他后缀的旧文件。_original_file 按固定后缀序取图，
    残留旧后缀文件（如上次生成 .png 这次输出 .jpg）会导致展示错图。"""
    for suffix in _UPLOAD_SUFFIXES:
        candidate = subject_dir(subject_id) / f"{kind}{suffix}"
        if candidate != keep and candidate.exists():
            candidate.unlink()


def _validate_kind(kind: str) -> str:
    if kind not in IMAGE_KINDS:
        raise SubjectImageError(f"未知图片类型：{kind}（支持 {'/'.join(IMAGE_KINDS)}）")
    return kind


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(paths.data_dir()))
    except ValueError:  # data_dir 被重定向到别处时兜底
        return str(path)


# ---------- 行 <-> 槽位 ----------

def build_slot(
    subject_id: str,
    kind: str,
    row: dict[str, Any] | None,
    image_sizes: dict[str, Any] | None = None,
) -> SubjectImageSlot:
    """合成一个槽位：无行 = empty；有本地文件但状态 stale 时仍按 status 返回。"""
    _validate_kind(kind)
    size = (image_sizes or {}).get(kind)
    slot = SubjectImageSlot(
        subject_id=subject_id,
        kind=kind,
        label=KIND_LABELS.get(kind, kind),
        aspect_ratio=getattr(size, "aspect_ratio", "3:4"),
        resolution=getattr(size, "resolution", "1k"),
    )
    if row is not None:
        for field in ("status", "source", "prompt", "file_path", "prompt_id", "error", "created_at", "updated_at"):
            setattr(slot, field, row.get(field) or slot.__class__.model_fields[field].default)
    if slot.status == "ready" and _original_file(subject_id, kind) is None:
        slot.status = "empty"  # 文件丢失（如手动清理）→ 视为空槽
        slot.source = None
        slot.file_path = None
    original = _original_file(subject_id, kind)
    if original is not None:
        slot.original_url = f"/subjects/{subject_id}/images/{kind}/file"
        if thumb_file(subject_id, kind).exists():
            slot.thumb_url = f"/subjects/{subject_id}/images/{kind}/thumb"
    slot.is_ready = slot.status == "ready" and original is not None
    return slot


def list_slots(conn: sqlite3.Connection, subject_id: str, settings: Any = None) -> list[SubjectImageSlot]:
    settings = settings or _fallback_settings(conn)
    rows = {row["kind"]: row for row in storage.list_subject_images(conn, subject_id)}
    return [
        build_slot(subject_id, kind, rows.get(kind), settings.image_sizes)
        for kind in IMAGE_KINDS
    ]


def get_slot(conn: sqlite3.Connection, subject_id: str, kind: str, settings: Any = None) -> SubjectImageSlot:
    settings = settings or _fallback_settings(conn)
    return build_slot(subject_id, kind, storage.get_subject_image(conn, subject_id, kind), settings.image_sizes)


def _save_row(conn: sqlite3.Connection, slot: SubjectImageSlot) -> None:
    storage.upsert_subject_image(conn, slot.model_dump())


def save_subject_prompt(
    conn: sqlite3.Connection, subject_id: str, kind: str, prompt: str
) -> SubjectImageSlot:
    """只把 prompt 写进槽位行（其余字段与状态机保持不变），AI 起草与手工编辑共用。

    AI 起草必须经此落库：否则草稿只存在于前端 textarea，生成任一图片后
    重新拉取槽位就会把其余草稿冲掉。
    """
    _validate_kind(kind)
    slot = get_slot(conn, subject_id, kind)
    slot.prompt = (prompt or "").strip()
    _save_row(conn, slot)
    return slot


# ---------- ComfyUI 生成 ----------

def _synthetic_result(subject_id: str, seed: int, prompts: list[tuple[str, str]]) -> GenerateResult:
    """构造临时 GenerateResult（不落库 runs），复用 submit() 管道。

    prompts: [(kind, prompt), ...]；filename_prefix 由现有规则生成
    （{subject}_{index:03d}_{kind}）。
    """
    items = [
        PromptItem(
            index=i,
            variant_id=kind,
            prompt=prompt,
            master="",
            variant_text="",
            background=None,
            lighting=None,
            caption="",
            meta={"kind": kind, "framing": _KIND_FRAMING.get(kind), "subject_image": True},
        )
        for i, (kind, prompt) in enumerate(prompts)
    ]
    return GenerateResult(
        run_id=f"subject_{subject_id}_{seed}",
        subject_id=subject_id,
        preset="subject_image",
        seed=seed,
        items=items,
    )


def _require_ready_sheet(conn: sqlite3.Connection, subject_id: str) -> Path:
    row = storage.get_subject_image(conn, subject_id, "character_sheet")
    sheet_file = _original_file(subject_id, "character_sheet")
    if row is None or row.get("status") != "ready" or sheet_file is None:
        raise SubjectImageError(
            "需要先就绪 character_sheet（人物半身像＋三视图）：请先生成或上传它，再生成其余 4 种图片",
            status=409,
        )
    return sheet_file


def _download_and_finalize(
    conn: sqlite3.Connection,
    subject_id: str,
    kind: str,
    base: str,
    files: list[dict[str, str]],
) -> SubjectImageSlot:
    if not files:
        raise SubjectImageError(f"ComfyUI 未返回输出文件（{kind}）")
    destination = subject_dir(subject_id) / f"{kind}.png"
    with comfyui_exporter._httpx_client(timeout=30.0) as client:
        saved = assets._download_output(client, base, files[0], destination)
    assets.make_thumb(saved, thumb_file(subject_id, kind))
    _remove_stale_kind_files(subject_id, kind, keep=saved)
    previous = storage.get_subject_image(conn, subject_id, kind) or {}
    slot = SubjectImageSlot(
        subject_id=subject_id,
        kind=kind,
        status="ready",
        source="generated",
        prompt=previous.get("prompt", ""),
        file_path=_relative(saved),
        created_at=previous.get("created_at", ""),
    )
    _save_row(conn, slot)
    return slot


def generate_subject_image(
    conn: sqlite3.Connection,
    subject: SubjectProfile,
    kind: str,
    *,
    prompt: str,
    seed: int = 0,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    comfy_url: str | None = None,
    wait: bool = True,
    settings: Any = None,
) -> SubjectImageSlot:
    """生成一个槽位图：character_sheet 走 t2i workflow，其余走 edit workflow。"""
    _validate_kind(kind)
    if not prompt.strip():
        raise SubjectImageError("提示词为空：请先填写或用 AI 起草")
    if kind in SHEET_DEPENDENT_KINDS:
        _require_ready_sheet(conn, subject.id)

    settings = settings or _fallback_settings(conn)
    workflow_name = (
        settings.comfy_t2i_workflow if kind == "character_sheet" else settings.comfy_edit_workflow
    )
    workflow, mapping = comfyui_exporter.load_workflow_pair(workflow_name)

    # 槽位默认画幅对两类 workflow 都生效：edit workflow 的出图 latent 是
    # EmptyLatentImage（switch 恒选它，不随参考图尺寸），不注入 width/height
    # 就会落到 mapping 默认 1024×1024（1:1）。请求显式传参时优先于设置。
    size = settings.image_sizes.get(kind)
    overrides: dict[str, Any] = {}
    if aspect_ratio is None and size is not None:
        aspect_ratio = size.aspect_ratio
        resolution = resolution or size.resolution
    sheet_file = _require_ready_sheet(conn, subject.id) if kind in SHEET_DEPENDENT_KINDS else None
    if sheet_file is not None:
        prepared = reference_images.prepared_reference(
            sheet_file, subject_dir(subject.id), "character_sheet"
        )
        remote = comfyui_exporter.upload_image(
            paths.comfy_base_url(comfy_url),
            prepared,
            remote_name=f"{subject.id}_character_sheet{prepared.suffix}",
        )
        overrides["image"] = remote
    if settings.default_negative_prompt:
        overrides["negative_prompt"] = settings.default_negative_prompt

    result = _synthetic_result(subject.id, seed, [(kind, prompt.strip())])
    row = {
        "subject_id": subject.id,
        "kind": kind,
        "status": "queued",
        "source": "generated",
        "prompt": prompt.strip(),
    }
    slot = SubjectImageSlot(**{**row, "label": KIND_LABELS.get(kind, kind)})

    def _on_submitted(item: PromptItem, prompt_id: str, prefix: str) -> None:
        slot.prompt_id = prompt_id
        _save_row(conn, slot)  # 提交即落库：超时后可凭 prompt_id 补拉

    try:
        submissions = comfyui_exporter.submit(
            result,
            workflow,
            mapping,
            comfy_url=comfy_url,
            overrides=overrides,
            wait=wait,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            on_submitted=_on_submitted,
        )
    except TimeoutError:
        slot.status = "queued"  # prompt_id 已记录，稍后 /sync 补拉
        _save_row(conn, slot)
        return get_slot(conn, subject.id, kind, settings)
    except FileNotFoundError as exc:
        slot.status = "error"
        slot.error = str(exc)
        _save_row(conn, slot)
        raise SubjectImageError(str(exc)) from exc

    entry = submissions[0]
    if not entry.get("ok"):
        slot.status = "error"
        slot.error = str(entry.get("error") or "ComfyUI 提交失败")
        _save_row(conn, slot)
        raise SubjectImageError(slot.error)
    if not wait:
        return get_slot(conn, subject.id, kind, settings.image_sizes)  # 已入队，稍后 /sync 取图
    return _download_and_finalize(conn, subject.id, kind, paths.comfy_base_url(comfy_url), entry.get("files") or [])


def generate_subject_images_batch(
    conn: sqlite3.Connection,
    subject: SubjectProfile,
    kinds: list[str],
    *,
    seed: int = 0,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    comfy_url: str | None = None,
    wait: bool = True,
    settings: Any = None,
) -> list[SubjectImageSlot]:
    """批量 edit 生成：一次 submit 提交 N 个槽位（共享上传的 character_sheet）。"""
    kinds = [_validate_kind(k) for k in kinds]
    bad = [k for k in kinds if k not in SHEET_DEPENDENT_KINDS]
    if bad:
        raise SubjectImageError(f"批量生成只支持 edit 类型（{'/'.join(SHEET_DEPENDENT_KINDS)}），收到：{bad}")
    prompts: list[tuple[str, str]] = []
    for kind in kinds:
        row = storage.get_subject_image(conn, subject.id, kind)
        prompt = (row or {}).get("prompt", "")
        if not prompt.strip():
            raise SubjectImageError(f"{kind} 还没有提示词：请先填写或用 AI 起草")
        prompts.append((kind, prompt.strip()))
    _require_ready_sheet(conn, subject.id)

    settings = settings or _fallback_settings(conn)
    workflow, mapping = comfyui_exporter.load_workflow_pair(settings.comfy_edit_workflow)
    sheet_file = _require_ready_sheet(conn, subject.id)
    prepared = reference_images.prepared_reference(
        sheet_file, subject_dir(subject.id), "character_sheet"
    )
    remote = comfyui_exporter.upload_image(
        paths.comfy_base_url(comfy_url),
        prepared,
        remote_name=f"{subject.id}_character_sheet{prepared.suffix}",
    )
    overrides: dict[str, Any] = {"image": remote}
    if settings.default_negative_prompt:
        overrides["negative_prompt"] = settings.default_negative_prompt

    # 逐槽位提交：不同槽位可配不同画幅（单次 submit 的 aspect 全局统一），
    # 参考图只上传一次共享；item.index 用全局序号，保持旧实现的 seed 递增。
    base = paths.comfy_base_url(comfy_url)
    slots: list[SubjectImageSlot] = []
    for position, (kind, kind_prompt) in enumerate(prompts):
        size = settings.image_sizes.get(kind)
        kind_aspect = aspect_ratio if aspect_ratio is not None else getattr(size, "aspect_ratio", None)
        kind_resolution = resolution if resolution is not None else getattr(size, "resolution", None)
        _save_row(conn, SubjectImageSlot(
            subject_id=subject.id, kind=kind, status="queued", source="generated", prompt=kind_prompt,
            label=KIND_LABELS.get(kind, kind),
        ))
        result = _synthetic_result(subject.id, seed, [(kind, kind_prompt)])
        result.items[0].index = position
        try:
            submissions = comfyui_exporter.submit(
                result,
                workflow,
                mapping,
                comfy_url=comfy_url,
                overrides=overrides,
                wait=wait,
                aspect_ratio=kind_aspect,
                resolution=kind_resolution,
            )
        except TimeoutError:
            # 已入队的保持 queued（prompt_id 已落库），稍后 /sync 补拉；本轮不再提交后续槽位
            slots.append(get_slot(conn, subject.id, kind, settings.image_sizes))
            return slots
        except FileNotFoundError as exc:
            raise SubjectImageError(str(exc)) from exc

        entry = submissions[0]
        if not entry.get("ok"):
            row = storage.get_subject_image(conn, subject.id, kind) or {}
            _save_row(conn, SubjectImageSlot(
                subject_id=subject.id, kind=kind, status="error",
                prompt=row.get("prompt", ""), error=str(entry.get("error") or "ComfyUI 提交失败"),
            ))
            slots.append(get_slot(conn, subject.id, kind, settings.image_sizes))
            continue
        if not wait:
            row = storage.get_subject_image(conn, subject.id, kind) or {}
            _save_row(conn, SubjectImageSlot(
                subject_id=subject.id, kind=kind, status="queued", source="generated",
                prompt=row.get("prompt", ""), prompt_id=str(entry.get("prompt_id") or ""),
            ))
            slots.append(get_slot(conn, subject.id, kind, settings.image_sizes))  # 已入队，稍后 /sync
            continue
        slots.append(_download_and_finalize(conn, subject.id, kind, base, entry.get("files") or []))
    return slots


def sync_subject_image(
    conn: sqlite3.Connection,
    subject_id: str,
    kind: str,
    comfy_url: str | None = None,
    settings: Any = None,
) -> SubjectImageSlot:
    """补拉 queued 槽位：按 prompt_id 查 /history，有输出就固化到 ready。"""
    _validate_kind(kind)
    row = storage.get_subject_image(conn, subject_id, kind)
    prompt_id = (row or {}).get("prompt_id")
    if not prompt_id:
        raise SubjectImageError(f"{kind} 没有 ComfyUI prompt_id，无需同步")
    settings = settings or _fallback_settings(conn)
    name = settings.comfy_t2i_workflow if kind == "character_sheet" else settings.comfy_edit_workflow
    try:
        _, mapping = comfyui_exporter.load_workflow_pair(name)
        output_nodes = mapping.get("output_nodes")
    except (FileNotFoundError, ValueError):
        output_nodes = None
    base = paths.comfy_base_url(comfy_url)
    with comfyui_exporter._httpx_client(timeout=15.0) as client:
        entry = assets.history_entry(client, base, prompt_id, 15)
    files = comfyui_exporter._output_filenames(entry or {}, output_nodes)
    if not files:
        return get_slot(conn, subject_id, kind, settings.image_sizes)
    return _download_and_finalize(conn, subject_id, kind, base, files)


def _fallback_settings(conn: sqlite3.Connection | None):
    from charvar import settings_service

    if conn is None:
        with closing(storage.connect()) as real_conn:
            return settings_service.get_settings(real_conn)
    return settings_service.get_settings(conn)


# ---------- 上传 / 删除 ----------

def save_uploaded_image(
    conn: sqlite3.Connection,
    subject_id: str,
    kind: str,
    filename: str,
    data: bytes,
) -> SubjectImageSlot:
    """用户上传：Pillow 校验后按 kind 覆盖保存。"""
    _validate_kind(kind)
    suffix = Path(filename).suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise SubjectImageError(f"只支持 {'/'.join(_UPLOAD_SUFFIXES)} 格式，收到：{filename or '（无后缀）'}")
    import io

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001
        raise SubjectImageError(f"不是有效的图片文件：{exc}") from exc
    target = subject_dir(subject_id) / f"{kind}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    assets.make_thumb(target, thumb_file(subject_id, kind))
    _remove_stale_kind_files(subject_id, kind, keep=target)
    previous = storage.get_subject_image(conn, subject_id, kind) or {}
    _save_row(conn, SubjectImageSlot(
        subject_id=subject_id,
        kind=kind,
        status="ready",
        source="uploaded",
        prompt=previous.get("prompt", ""),
        file_path=_relative(target),
        created_at=previous.get("created_at", ""),
    ))
    return get_slot(conn, subject_id, kind)


def delete_subject_image_files(conn: sqlite3.Connection, subject_id: str, kind: str) -> bool:
    _validate_kind(kind)
    removed = False
    for suffix in _UPLOAD_SUFFIXES:
        candidate = subject_dir(subject_id) / f"{kind}{suffix}"
        if candidate.exists():
            candidate.unlink()
            removed = True
    thumb = thumb_file(subject_id, kind)
    if thumb.exists():
        thumb.unlink()
    row_deleted = storage.delete_subject_image(conn, subject_id, kind)
    return removed or row_deleted


def delete_subject_tree(subject_id: str) -> None:
    """主体删除时清理其图片目录（api_subject_delete 级联调用）。"""
    target = subject_dir(subject_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
