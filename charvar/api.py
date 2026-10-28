"""HTTP API（设计文档 §7.2，默认 127.0.0.1:8710）。

build_app() 返回 FastAPI 应用：业务路由 + 可选静态管理页。
web/app.py 负责启动（uvicorn），与其他本机工具栈一致。
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from contextlib import closing

import httpx
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from charvar import __version__, assets, llm, paths, settings_service, storage, subject_images
from charvar import adhoc as adhoc_service
from charvar import adhoc_video as adhoc_video_service
from charvar import adhoc_tts as adhoc_tts_service
from charvar import dreamina_image
from charvar import openrouter_image
from charvar import openrouter_tts
from charvar import openrouter_video
from charvar import poses as poses_service
from charvar import reference_images
from charvar.assets import ensure_thumb, resolve_run_asset, sheet_path, thumb_path

from charvar.composer import expand_matrix, generate as compose_generate
from charvar.exporters import captions as captions_exporter
from charvar.exporters import comfyui as comfyui_exporter
from charvar.exporters import jsonl as jsonl_exporter
from charvar.exporters import txt as txt_exporter
from charvar.llm_compose import generate_llm
from charvar.llm_prompts import DRAFT_SCHEMA, draft_subject_image_prompt_messages
from charvar.models import GenerateRequest, GenerateResult, PromptItem, ReferenceSpec, SubjectProfile
from charvar.preset_loader import (
    ListPreset,
    MatrixPreset,
    PresetError,
    load,
    load_file,
    list_presets,
)
from charvar import sticker_policy
from charvar import outfit as outfit_service
from charvar import hairstyle as hairstyle_service
from charvar import overlay as overlay_service
from charvar import recipes as recipes_service
from charvar import video as video_service

EXPORT_FORMATS = ("txt", "jsonl", "captions_zip", "captions")

_TEMPLATE_MAX_BYTES = 200_000

# OpenRouter 批量生图的并发路数。实测单张 1K 约 14s、4K 约 45s；
# 30 条 4 路并发约 2 分钟（1K），比串行（8 分钟）可接受。
_OPENROUTER_CONCURRENCY = 4

# 即梦批量生图的并发路数。单张 2K 约 60s（即梦侧排队 + 生成），
# 2 路并发 30 条约 15 分钟；每张消耗即梦积分/权益，保守开 2（env 可调）。
_DREAMINA_CONCURRENCY = max(1, int(os.environ.get("DREAMINA_CONCURRENCY") or 2))


class ComfyQueueRequest(BaseModel):
    workload: str | None = Field(
        default=None,
        description="workflows 目录里的 workflow 名称；缺省时按参考图自动选（有→图片编辑，无→文生图）",
    )
    negative_prompt: str | None = None
    wait: bool = False
    comfy_url: str | None = None
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: Literal["1k", "2k"] | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    reference_kinds: list[str] = Field(default_factory=list, description="主体图参考（兼容旧调用），按序注入 image/image2/…")
    references: list[ReferenceSpec] = Field(default_factory=list, description="参考图（Playground 式来源 + role 分工）；给了它就不看 reference_kinds")


class OpenRouterQueueRequest(BaseModel):
    """推送 OpenRouter 在线生图（与 ComfyUI 入队同一套语义）。

    画幅换算成显式像素（``size``）——``size`` 是 OpenRouter 层的通用参数，
    比各模型自己的 ``aspect_ratio`` 枚举更可靠（枚举不含 charvar 支持的 21:9）。
    ``resolution`` / ``quality`` 只在模型清单声明支持时才真正生效。
    """

    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: str | None = Field(default=None, description="charvar 档位（1k/2k）或模型声明枚举值（1K/1.5K/2K/4K…）；空 = 用设置页/env 的模式默认")
    reference_kinds: list[str] = Field(default_factory=list, description="主体图参考（兼容旧调用），按序对应提示词里的 the first/second… reference image")
    references: list[ReferenceSpec] = Field(default_factory=list, description="参考图（Playground 式来源 + role 分工）；给了它就不看 reference_kinds")
    model: str | None = Field(default=None, description="覆盖默认模型（按模式取设置页/OP_IMAGE_T2I_MODEL、OP_IMAGE_I2I_MODEL）")
    seed: int | None = Field(default=None, description="覆盖种子；缺省用条目自带的稳定子种子")
    quality: str | None = Field(default=None, description="质量档；只在模型清单声明 quality 时生效，空 = 用模式默认")


class RunRegenerateRequest(BaseModel):
    """单条重新生成（图片墙卡片按钮）：同一 prompt、原出图设置，只换采样种子。"""

    item_index: int
    # None = 沿用原 run.seed（等价重推）；前端点按钮时传随机新种子（0–100000）
    seed: int | None = None
    comfy_url: str | None = None
    wait: bool = True


class SyncOutputsRequest(BaseModel):
    build_sheets: bool = True


class ContactSheetRequest(BaseModel):
    mode: Literal["contact", "face"] = "contact"


class VideoRenderRequest(BaseModel):
    preset: str = "q21_turnaround"
    # None/"" = 用预设内置音乐；"none" = 本次不加；其余 = presets/video/music/ 文件名
    music: str | None = None


class OverlayApplyRequest(BaseModel):
    """贴字后处理（presets/overlays/ 预设，PIL 渲染，不改原图）。"""
    overlay: str
    text_from: Literal["outfit_label", "pose_tag", "variant_id", "fixed"] = "outfit_label"
    fixed: str | None = None           # text_from=fixed 的固定文本
    items: list[int] | None = None     # 缺省 = 全部成品图条目


class RecipeRunRequest(BaseModel):
    seed: int | None = None            # 缺省 = 配方级 seed


class SubjectImageGenerateRequest(BaseModel):
    prompt: str | None = None
    seed: int = 0
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: Literal["1k", "2k"] | None = None
    comfy_url: str | None = None
    wait: bool = True


class SubjectImageOpenRouterRequest(BaseModel):
    """档案图片走 OpenRouter 出图（与 ComfyUI 通路同语义）。"""

    prompt: str | None = None
    seed: int = 0
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    # charvar 档位（1k/2k）或模型声明枚举值（1K/4K…）；空 = 用设置页/env 的模式默认
    resolution: str | None = None
    model: str | None = None
    quality: str | None = None


class SubjectImageDreaminaRequest(BaseModel):
    """档案图片走即梦（dreamina CLI）出图（与 OpenRouter 通路同语义）。

    即梦不区分文生图/图片编辑配置组（带参考图自动走 image2image）；
    不暴露 seed（仅存档）；画幅用 --ratio。
    """

    prompt: str | None = None
    seed: int = 0
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: str | None = Field(default=None, description="2k/4k（5.0Pro 另有 1.5k）；空 = 设置页/env（DREAMINA_IMAGE_RESOLUTION）默认")
    model: str | None = Field(default=None, description="即梦 model_version；空 = 设置页/env（DREAMINA_IMAGE_MODEL）默认 5.0")


class DreaminaQueueRequest(BaseModel):
    """推送即梦在线生图（与 OpenRouter 入队同一套语义）。

    即梦不区分 t2i/i2i 配置组：带参考图走 image2image、不带走 text2image，
    配置共用一组（设置页「即梦 Dreamina 生图」/ env DREAMINA_IMAGE_*）。
    画幅用 --ratio（charvar 8 档与即梦原生枚举一致）；不暴露 seed。
    """

    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: str | None = Field(default=None, description="2k/4k（5.0Pro 另有 1.5k）；空 = 用设置页/env 默认")
    reference_kinds: list[str] = Field(default_factory=list, description="主体图参考（兼容旧调用）：带 = image2image，不带 = text2image")
    references: list[ReferenceSpec] = Field(default_factory=list, description="参考图（Playground 式来源 + role 分工）；给了它就不看 reference_kinds")
    model: str | None = Field(default=None, description="即梦 model_version；空 = 设置页/env（DREAMINA_IMAGE_MODEL）默认 5.0")
    seed: int | None = Field(default=None, description="仅存档；即梦不暴露 seed")


class SubjectImageBatchRequest(BaseModel):
    kinds: list[str]
    seed: int = 0
    comfy_url: str | None = None
    wait: bool = True


class SubjectPromptPutRequest(BaseModel):
    prompt: str = ""


class TemplatePutRequest(BaseModel):
    template_md: str


class Message(BaseModel):
    detail: str


class AdhocReferenceSpec(ReferenceSpec):
    """一张参考图的来源：上传暂存（staged_id）、主体档案（subject_id + kind）

    或 Playground「最近生成」的原图（run_id，服务端从该 run 的 original/
    目录定文件名 —— 不接受任意路径）。
    顺序即提示词里 ``<image1>`` / ``<image2>`` 的顺序。
    """


# 参考图在提示词里的分工（role）：与主体档案的图片槽位同名，
# 决定 LLM 改写的锚定句里这张图管哪一摊；pose = 姿势库人偶线稿（只锚姿势构图）；
# 其余缺省按 front 处理，pose 来源缺省按 pose 处理。
REFERENCE_ROLES = ("character_sheet", "headshot", "front", "side", "behind", "pose")
DEFAULT_REFERENCE_ROLE = "front"


def _reference_paths_from_specs(
    specs: list[ReferenceSpec],
    db_path=None,
) -> tuple[list[Path], list[str], list[str]]:
    """参考图规格 → (本地文件路径, role 列表, 暂存 id 列表)。

    四种来源与 Playground 同源：上传暂存（staged_id）、主体档案槽位
    （subject_id + kind）、最近生成的原图（run_id；adhoc run 取唯一原图，
    charvar run 按 index 取第几张）、姿势库（pose_id + angle —— 灰人偶线稿，
    role 固定 pose）。role 缺省 front（pose 来源缺省 pose）。
    不接受任意路径 —— 那是把文件读权限开给了浏览器。
    """
    paths: list[Path] = []
    roles: list[str] = []
    staged_ids: list[str] = []
    for spec in specs:
        role = (spec.role or "").strip() or ("pose" if spec.pose_id else DEFAULT_REFERENCE_ROLE)
        if role not in REFERENCE_ROLES:
            raise HTTPException(
                400, f"参考图分工不合法：{role}（支持 {' / '.join(REFERENCE_ROLES)}）"
            )
        if spec.staged_id:
            try:
                paths.append(adhoc_service.staged_reference(spec.staged_id))
            except adhoc_service.AdhocError as exc:
                raise HTTPException(400, str(exc)) from exc
            staged_ids.append(spec.staged_id)
        elif spec.subject_id and spec.kind:
            if spec.kind not in subject_images.IMAGE_KINDS:
                raise HTTPException(
                    400, f"未知图片类型：{spec.kind}（支持 {'/'.join(subject_images.IMAGE_KINDS)}）"
                )
            local = subject_images.find_reference_file(spec.subject_id, spec.kind)
            if local is None:
                raise HTTPException(
                    400, f"参考图不存在：{spec.subject_id} / {spec.kind}（请先在主体档案里生成或上传）"
                )
            # 走预处理过的压缩版（长边 1536 / JPEG q88），与 ComfyUI 上传同一份
            subject_dir = subject_images.subject_dir(spec.subject_id)
            paths.append(reference_images.prepared_reference(local, subject_dir, spec.kind))
        elif spec.run_id:
            paths.append(_run_reference_file(spec.run_id, spec.index))
        elif spec.pose_id:
            try:
                paths.append(poses_service.prepared_reference_file(spec.pose_id, spec.angle, db_path))
            except poses_service.PoseError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            raise HTTPException(
                400,
                "参考图缺少来源：需要 staged_id / subject_id+kind / run_id / pose_id+angle 之一",
            )
        roles.append(role)
    return paths, roles, staged_ids


def _run_reference_file(run_id: str, index: int | None = None) -> Path:
    """最近生成的原图 → 本地文件。

    adhoc run（Playground）original/ 下只有一个文件；charvar run 按条目顺序
    取第 index 张（缺省第一张）。文件名由服务端从 run 目录定 —— 不收浏览器给的路径。
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        raise HTTPException(400, "参考图缺少 run_id")
    if run_id.startswith("adhoc_"):
        try:
            return adhoc_service.original_file(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        files = assets.list_originals(run_id)
    except ValueError as exc:
        raise HTTPException(400, f"非法 run_id：{run_id}") from exc
    if not files:
        raise HTTPException(400, f"这次生成没有原图可用：{run_id}")
    position = 0 if index is None else int(index)
    if position < 0 or position >= len(files):
        raise HTTPException(400, f"这次生成没有第 {position + 1} 张原图（共 {len(files)} 张）")
    return files[position]


class AdhocGenerateRequest(BaseModel):
    """网页「Playground」的请求（与 CLI ``charvar image`` 同一套参数）。"""

    prompt: str
    backend: Literal["comfyui", "openrouter", "dreamina"] = "comfyui"
    negative_prompt: str | None = None
    references: list[AdhocReferenceSpec] = Field(default_factory=list)
    aspect_ratio: str = "3:4"
    resolution: str = ""  # 空 = 用设置页/env 的模式默认；1k/2k 或模型声明枚举值（1K/4K…）
    seed: int = 0
    model: str | None = None
    quality: str | None = None
    comfy_url: str | None = None
    workflow: str | None = None
    wait: bool = True


class StagedVideoRefSpec(BaseModel):
    """一条参考视频的来源（v1 只认「上传暂存」；配方里的视频素材走 refs 通道）。"""

    staged_id: str


class VideoGenerateRequest(BaseModel):
    """网页「视频」页的请求（与 CLI ``charvar video`` 同一套参数；OpenRouter 作业通路）。

    视频是异步作业：本端点只提交（202 + job id），前端按 /videos/{run_id}/status 轮询。
    首帧图 = 图生视频（frame_images）；参考图 = 参考生成（input_references）；
    两者同给时首帧优先（官方语义）。都只认「上传暂存 / 主体档案就绪图 /
    Playground 最近生成原图」三种来源。参考视频 = input_references 的 video_url
    （Seedance gen2+ 才接，≤2 条，只认上传暂存）。
    """

    prompt: str
    model: str | None = None  # 空 = 设置页/env 默认（heygen/heygen-video-1）
    duration: int | None = None  # 秒；None = 不发（跟随 provider 默认）
    resolution: str | None = None  # 480p/768p/2K…；None = 不发
    aspect_ratio: str = "9:16"
    seed: int = 0
    first_frame: AdhocReferenceSpec | None = None
    references: list[AdhocReferenceSpec] = Field(default_factory=list)
    video_references: list[StagedVideoRefSpec] = Field(default_factory=list)


class TtsGenerateRequest(BaseModel):
    """网页「语音」页的请求(与 CLI ``charvar tts`` 同一套参数;OpenRouter 同步通路)。

    语音合成是**同步**的:本端点等 OpenRouter 返回音频字节后再响应(几十秒封顶),
    不需要视频那套 status 轮询。voice / response_format 空串 = 自动
    (枚举兜底第一项 / 格式自动协商)。
    """

    text: str
    model: str | None = None  # 空 = 设置页/env 默认(免费 turbo:free)
    voice: str = ""  # 空 = 不发(目录枚举非空时 client 兜底第一项)
    response_format: str = ""  # 空 = 自动(探测缓存 > 静态表 > API 默认 pcm)


def build_app(static_dir: Path | None = None, db_path=None) -> FastAPI:
    app = FastAPI(title="charvar 角色变体提示词生成器", version=__version__)

    def _db():
        return closing(storage.connect(db_path))

    def _preset(name: str):
        try:
            return load(name)
        except PresetError as exc:
            # 预设缺失 = 404；配置/格式错误（如无效 schema 2）= 明确的 400 配置错误
            raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc

    # ---- 页面 ----
    if static_dir is not None and Path(static_dir).exists():
        static_dir = Path(static_dir)
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    # ---- 预设 ----
    @app.get("/presets")
    def api_presets() -> list[dict[str, Any]]:
        return list_presets()

    @app.get("/outfit-presets")
    def api_outfit_presets() -> list[dict[str, Any]]:
        return outfit_service.list_outfit_presets()

    @app.get("/hairstyle-presets")
    def api_hairstyle_presets() -> list[dict[str, Any]]:
        return hairstyle_service.list_hairstyle_presets()

    @app.get("/presets/{name}")
    def api_preset_detail(name: str, expand: bool = Query(True, description="展开变体列表")) -> dict[str, Any]:
        preset = _preset(name)
        detail: dict[str, Any] = {
            "name": preset.name,
            "type": preset.type,
            "version": preset.version,
            "master_template": preset.master_template,
            "suffix_template": preset.suffix_template,
            "backgrounds": preset.backgrounds,
            "lighting": preset.lighting,
            "caption_tags": preset.caption_tags,
            "caption_background_suffix": preset.caption_background_suffix,
            "reference_order": preset.reference_order,
            "schema_version": preset.schema_version,
            "purpose": preset.purpose,
            "text_policy": preset.text_policy,
            "composition_policy": preset.composition_policy,
            "preferred_aspect_ratio": preset.preferred_aspect_ratio,
            "lettering": preset.lettering.model_dump() if preset.lettering is not None else None,
        }
        if isinstance(preset, ListPreset):
            detail["variants"] = [v.model_dump() for v in preset.variants]
            detail["count"] = len(preset.variants)
        elif isinstance(preset, MatrixPreset):
            specs = expand_matrix(preset) if expand else []
            detail["dimensions"] = {d: list(vals.keys()) for d, vals in preset.dimensions.items()}
            detail["rules"] = preset.rules
            detail["exclude"] = preset.exclude
            detail["join"] = preset.join
            detail["end"] = preset.end
            detail["variants"] = [v.model_dump() for v in specs]
            detail["count"] = len(specs)
        return detail

    # ---- ComfyUI ----
    @app.get("/comfy/workflows")
    def api_comfy_workflows(workflows_dir: str = Query("", description="覆盖 COMFYUI_WORKFLOWS_DIR")) -> dict[str, Any]:
        return {
            "workflows_dir": str(paths.comfy_workflows_dir() if workflows_dir == "" else workflows_dir),
            "base_url": paths.comfy_base_url(),
            "workflows": comfyui_exporter.list_workflows(workflows_dir or None),
        }

    @app.get("/comfy/status")
    def api_comfy_status(comfy_url: str = Query("")) -> dict[str, Any]:
        base = paths.comfy_base_url(comfy_url or None)
        try:
            with comfyui_exporter._httpx_client(timeout=6.0) as client:
                resp = client.get(f"{base}/system_stats", timeout=6)
                resp.raise_for_status()
                return {"ok": True, "base_url": base, "system_stats": resp.json()}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"无法连接 ComfyUI {base}：{exc}") from exc

    # ---- 主体档案 ----
    @app.get("/subjects")
    def api_subjects() -> list[SubjectProfile]:
        with _db() as conn:
            return storage.list_subjects(conn)

    @app.post("/subjects", status_code=201)
    def api_subject_create(profile: dict[str, Any]) -> SubjectProfile:
        try:
            subject = SubjectProfile.model_validate(profile)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc  # type: ignore[arg-type]
        with _db() as conn:
            if storage.get_subject(conn, subject.id) is not None:
                raise HTTPException(409, f"主体档案已存在：{subject.id}")
            storage.save_subject(conn, subject, replace=False)
        return subject

    @app.get("/subjects/{subject_id}")
    def api_subject_get(subject_id: str) -> SubjectProfile:
        with _db() as conn:
            subject = storage.get_subject(conn, subject_id)
        if subject is None:
            raise HTTPException(404, f"主体档案不存在：{subject_id}")
        return subject

    @app.put("/subjects/{subject_id}")
    def api_subject_update(subject_id: str, profile: dict[str, Any]) -> SubjectProfile:
        data = {**profile}
        try:
            data.setdefault("id", subject_id)
            subject = SubjectProfile.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        if subject.id != subject_id:
            raise HTTPException(400, "body.id 与 URL id 不一致")
        with _db() as conn:
            storage.save_subject(conn, subject, replace=True)
        return subject

    @app.delete("/subjects/{subject_id}")
    def api_subject_delete(subject_id: str) -> dict[str, Any]:
        with _db() as conn:
            ok = storage.delete_subject(conn, subject_id)
            if ok:
                storage.delete_subject_images(conn, subject_id)
                storage.delete_subject_template(conn, subject_id)
        if not ok:
            raise HTTPException(404, f"主体档案不存在：{subject_id}")
        subject_images.delete_subject_tree(subject_id)
        return {"ok": True, "deleted": subject_id}

    # ---- 主体档案图片（5 种固定槽位） ----
    def _require_subject(subject_id: str) -> SubjectProfile:
        with _db() as conn:
            subject = storage.get_subject(conn, subject_id)
        if subject is None:
            raise HTTPException(404, f"主体档案不存在：{subject_id}")
        return subject

    @app.get("/subjects/{subject_id}/images")
    def api_subject_images(subject_id: str) -> dict[str, Any]:
        _require_subject(subject_id)
        with _db() as conn:
            settings = settings_service.get_settings(conn)
            slots = subject_images.list_slots(conn, subject_id, settings)
        return {
            "subject_id": subject_id,
            "kinds": list(subject_images.IMAGE_KINDS),
            "slots": [slot.model_dump() for slot in slots],
        }

    @app.post("/subjects/{subject_id}/images/{kind}/suggest-prompt")
    def api_subject_suggest_prompt(subject_id: str, kind: str) -> dict[str, str]:
        subject = _require_subject(subject_id)
        try:
            subject_images._validate_kind(kind)
        except subject_images.SubjectImageError as exc:
            raise HTTPException(400, str(exc)) from exc
        with _db() as conn:
            template = storage.get_subject_template(conn, subject_id) or ""
            config = settings_service.llm_overrides(conn)
        messages = draft_subject_image_prompt_messages(subject, kind, template)
        try:
            data = llm.chat_json(
                messages,
                config=config,
                schema=DRAFT_SCHEMA,
                schema_name="subject_image_prompt",
                max_tokens=None,
            )
        except llm.LLMError as exc:
            raise HTTPException(502, f"LLM 起草提示词失败：{exc}") from exc
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(502, "LLM 未返回提示词")
        # 起草结果立即落库：只写 prompt，不动状态机（否则草稿只在前端，会丢）
        with _db() as conn:
            subject_images.save_subject_prompt(conn, subject_id, kind, prompt)
        return {"kind": kind, "prompt": prompt}

    @app.put("/subjects/{subject_id}/images/{kind}/prompt")
    def api_subject_prompt_put(subject_id: str, kind: str, req: SubjectPromptPutRequest) -> dict[str, Any]:
        """手工编辑提示词落库（textarea 失焦时前端调用）。"""
        _require_subject(subject_id)
        with _db() as conn:
            try:
                slot = subject_images.save_subject_prompt(conn, subject_id, kind, req.prompt)
            except subject_images.SubjectImageError as exc:
                raise HTTPException(400, str(exc)) from exc
        return slot.model_dump()

    @app.post("/subjects/{subject_id}/images/{kind}/generate")
    def api_subject_image_generate(subject_id: str, kind: str, req: SubjectImageGenerateRequest) -> dict[str, Any]:
        subject = _require_subject(subject_id)
        with _db() as conn:
            settings = settings_service.get_settings(conn)
            previous = storage.get_subject_image(conn, subject_id, kind) or {}
            prompt = (req.prompt if req.prompt is not None else previous.get("prompt", "")) or ""
            try:
                slot = subject_images.generate_subject_image(
                    conn,
                    subject,
                    kind,
                    prompt=prompt,
                    seed=req.seed,
                    aspect_ratio=req.aspect_ratio,
                    resolution=req.resolution,
                    comfy_url=req.comfy_url,
                    wait=req.wait,
                    settings=settings,
                )
            except subject_images.SubjectImageError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        return slot.model_dump()

    @app.post("/subjects/{subject_id}/images/{kind}/generate-openrouter")
    def api_subject_image_generate_openrouter(
        subject_id: str, kind: str, req: SubjectImageOpenRouterRequest
    ) -> dict[str, Any]:
        """档案图片走 OpenRouter 出图。

        与 ComfyUI 通路同语义：两步流程（character_sheet 先出，其余 4 种以它
        为参考图）、同槽位状态机、同落盘位置（``data/subjects/{id}/{kind}.{ext}``）。
        """
        subject = _require_subject(subject_id)
        with _db() as conn:
            settings = settings_service.get_settings(conn)
            # 模式对齐 ComfyUI：character_sheet 是文生图（t2i / t2i workflow），
            # 其余 4 槽以它为参考图 = 图片编辑（i2i / edit workflow）；
            # 参数默认链：请求显式 > 设置页/env 的模式默认
            mode = "t2i" if kind == "character_sheet" else "i2i"
            mcfg = settings_service.openrouter_mode_config(conn)[mode]
            previous = storage.get_subject_image(conn, subject_id, kind) or {}
            prompt = (req.prompt if req.prompt is not None else previous.get("prompt", "")) or ""
            try:
                slot = subject_images.generate_subject_image_openrouter(
                    conn,
                    subject,
                    kind,
                    prompt=prompt,
                    seed=req.seed,
                    aspect_ratio=req.aspect_ratio,
                    resolution=(req.resolution or "").strip() or mcfg["resolution"],
                    model=(req.model or "").strip() or mcfg["model"],
                    api_key=openrouter_image.resolve_api_key(settings.openrouter.api_key or None),
                    quality=(req.quality or "").strip() or mcfg["quality"],
                    settings=settings,
                )
            except subject_images.SubjectImageError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            except openrouter_image.OpenRouterImageError as exc:
                raise HTTPException(502, f"OpenRouter 生图失败：{exc}") from exc
        return slot.model_dump()

    @app.post("/subjects/{subject_id}/images/{kind}/generate-dreamina")
    def api_subject_image_generate_dreamina(
        subject_id: str, kind: str, req: SubjectImageDreaminaRequest
    ) -> dict[str, Any]:
        """档案图片走即梦出图（与 OpenRouter 通路同语义：两步流程 + 同槽位状态机）。

        即梦不分组：character_sheet 走 text2image，其余 4 槽以它为参考自动走
        image2image；不暴露 seed（仅存档）。
        """
        subject = _require_subject(subject_id)
        with _db() as conn:
            settings = settings_service.get_settings(conn)
            mcfg = settings_service.dreamina_config(conn)
            previous = storage.get_subject_image(conn, subject_id, kind) or {}
            prompt = (req.prompt if req.prompt is not None else previous.get("prompt", "")) or ""
            try:
                slot = subject_images.generate_subject_image_dreamina(
                    conn,
                    subject,
                    kind,
                    prompt=prompt,
                    seed=req.seed,
                    aspect_ratio=req.aspect_ratio,
                    resolution=(req.resolution or "").strip() or mcfg["resolution"],
                    model=(req.model or "").strip() or mcfg["model"],
                    settings=settings,
                )
            except subject_images.SubjectImageError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        return slot.model_dump()

    @app.post("/subjects/{subject_id}/images/{kind}/sync")
    def api_subject_image_sync(subject_id: str, kind: str, comfy_url: str = Query("")) -> dict[str, Any]:
        _require_subject(subject_id)
        with _db() as conn:
            try:
                slot = subject_images.sync_subject_image(conn, subject_id, kind, comfy_url or None)
            except subject_images.SubjectImageError as exc:
                raise HTTPException(400, str(exc)) from exc
        return slot.model_dump()

    @app.post("/subjects/{subject_id}/images/{kind}/upload")
    def api_subject_image_upload(subject_id: str, kind: str, file: UploadFile) -> dict[str, Any]:
        _require_subject(subject_id)
        data = file.file.read()
        with _db() as conn:
            try:
                slot = subject_images.save_uploaded_image(conn, subject_id, kind, file.filename or "", data)
            except subject_images.SubjectImageError as exc:
                raise HTTPException(400, str(exc)) from exc
        return slot.model_dump()

    @app.delete("/subjects/{subject_id}/images/{kind}")
    def api_subject_image_delete(subject_id: str, kind: str) -> dict[str, Any]:
        _require_subject(subject_id)
        with _db() as conn:
            try:
                removed = subject_images.delete_subject_image_files(conn, subject_id, kind)
            except subject_images.SubjectImageError as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "removed": removed, "kind": kind}

    @app.get("/subjects/{subject_id}/images/{kind}/file")
    def api_subject_image_file(subject_id: str, kind: str) -> FileResponse:
        _require_subject(subject_id)
        try:
            subject_images._validate_kind(kind)
        except subject_images.SubjectImageError as exc:
            raise HTTPException(400, str(exc)) from exc
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = subject_images.subject_dir(subject_id) / f"{kind}{suffix}"
            if candidate.exists():
                return FileResponse(candidate)
        raise HTTPException(404, f"该类型还没有图片：{kind}")

    @app.get("/subjects/{subject_id}/images/{kind}/thumb")
    def api_subject_image_thumb(subject_id: str, kind: str) -> FileResponse:
        _require_subject(subject_id)
        try:
            subject_images._validate_kind(kind)
        except subject_images.SubjectImageError as exc:
            raise HTTPException(400, str(exc)) from exc
        thumb = subject_images.ensure_thumb(subject_id, kind)
        if thumb is None:
            raise HTTPException(404, f"该类型还没有缩略图：{kind}")
        return FileResponse(thumb, media_type="image/webp")

    @app.post("/subjects/{subject_id}/images/generate-batch")
    def api_subject_image_batch(subject_id: str, req: SubjectImageBatchRequest) -> dict[str, Any]:
        subject = _require_subject(subject_id)
        with _db() as conn:
            settings = settings_service.get_settings(conn)
            try:
                slots = subject_images.generate_subject_images_batch(
                    conn,
                    subject,
                    req.kinds,
                    seed=req.seed,
                    comfy_url=req.comfy_url,
                    wait=req.wait,
                    settings=settings,
                )
            except subject_images.SubjectImageError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        return {"subject_id": subject_id, "slots": [slot.model_dump() for slot in slots]}

    # ---- 主体通用模板（角色全场景通用提示词模板 markdown） ----
    @app.get("/subjects/{subject_id}/template")
    def api_subject_template_get(subject_id: str) -> PlainTextResponse:
        _require_subject(subject_id)
        with _db() as conn:
            template = storage.get_subject_template(conn, subject_id)
        if template is None:
            raise HTTPException(404, "该主体还没有上传通用提示词模板")
        return PlainTextResponse(template, media_type="text/markdown; charset=utf-8")

    @app.put("/subjects/{subject_id}/template")
    def api_subject_template_put(subject_id: str, req: TemplatePutRequest) -> dict[str, Any]:
        _require_subject(subject_id)
        if len(req.template_md.encode("utf-8")) > _TEMPLATE_MAX_BYTES:
            raise HTTPException(413, f"模板过大（上限 {_TEMPLATE_MAX_BYTES // 1024}KB）")
        with _db() as conn:
            storage.save_subject_template(conn, subject_id, req.template_md)
        return {"ok": True, "subject_id": subject_id, "bytes": len(req.template_md.encode("utf-8"))}

    # ---- 姿势库（pose-db-01 人偶线稿） ----
    @app.get("/poses")
    def api_poses_list(
        line: str = Query("", description="按系統原文过滤"),
        category: str = Query("", description="按分類原文过滤"),
        q: str = Query("", max_length=60, description="按动作名/编号/id 过滤"),
    ) -> dict[str, Any]:
        """姿势库浏览：动作清单（按动作分组）+ 系統/分類元数据。

        视角全量返回（客户端过滤）；图片 URL 经 /poses/image/{source}/… 端点伺服，
        浏览器不给磁盘路径。
        """
        with _db() as conn:
            return poses_service.list_poses(conn, line=line, category=category, q=q)

    def _pose_image_response(
        source: str, pose_id: str, angle: str, variant: str
    ) -> FileResponse:
        if source != poses_service.SOURCE_ID:
            raise HTTPException(404, f"没有这个姿势库：{source}")
        try:
            path = (
                poses_service.thumb_image_file(pose_id, angle, db_path)
                if variant == "thumb"
                else poses_service.full_image_file(pose_id, angle, db_path)
            )
        except poses_service.PoseError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(
            path, media_type="image/jpeg" if variant == "thumb" else "image/png"
        )

    @app.get("/poses/image/{source}/{pose_id}/{angle}/thumb")
    def api_pose_image_thumb(source: str, pose_id: str, angle: str) -> FileResponse:
        return _pose_image_response(source, pose_id, angle, "thumb")

    @app.get("/poses/image/{source}/{pose_id}/{angle}/full")
    def api_pose_image_full(source: str, pose_id: str, angle: str) -> FileResponse:
        return _pose_image_response(source, pose_id, angle, "full")

    # ---- 全局设置 ----
    def _settings_payload(settings: settings_service.AppSettings) -> dict[str, Any]:
        data = settings.model_dump()
        data["llm"]["api_key"] = ""
        data["llm_configured"] = bool(settings.llm.api_key) or bool(llm.get_llm_config().get("api_key"))
        data["openrouter"]["api_key"] = ""
        data["openrouter_configured"] = bool(openrouter_image.resolve_api_key())
        # 模型默认按模式给（env 兜底链在 resolve_model 里），供设置页占位提示
        data["openrouter_default_t2i_model"] = openrouter_image.resolve_model(mode="t2i")
        data["openrouter_default_i2i_model"] = openrouter_image.resolve_model(mode="i2i")
        # 视频：默认链 DB > OP_VIDEO_MODEL > heygen，供设置页占位提示
        data["openrouter_video_default_model"] = openrouter_video.resolve_model(
            settings.openrouter_video.model
        )
        # 即梦：CLI 是否可用（登录态在 dreamina login 侧查，这里只探文件在不在）
        try:
            dreamina_image.resolve_binary()
            data["dreamina_cli_available"] = True
        except dreamina_image.DreaminaCliError:
            data["dreamina_cli_available"] = False
        return data

    @app.get("/settings")
    def api_settings_get() -> dict[str, Any]:
        with _db() as conn:
            settings = settings_service.get_settings(conn)
        return _settings_payload(settings)

    @app.put("/settings")
    def api_settings_put(patch: dict[str, Any]) -> dict[str, Any]:
        with _db() as conn:
            settings = settings_service.save_settings(conn, patch)
        return _settings_payload(settings)

    # ---- 生成 ----
    def _resolve_subject(req: GenerateRequest) -> SubjectProfile:
        if req.subject is not None and req.subject_id is None:
            return req.subject
        if req.subject is not None and req.subject_id is not None:
            if req.subject.id != req.subject_id:
                raise HTTPException(400, "subject.id 与 subject_id 不一致")
            return req.subject
        if req.subject_id:
            with _db() as conn:
                subject = storage.get_subject(conn, req.subject_id)
            if subject is None:
                raise HTTPException(404, f"主体档案不存在：{req.subject_id}")
            return subject
        raise HTTPException(400, "需要 subject（临时档案）或 subject_id（引用已存档案）")

    @app.post("/generate")
    def api_generate(req: GenerateRequest) -> GenerateResult:
        subject = _resolve_subject(req)
        preset = _preset(req.preset)
        if req.outfit_preset or req.hairstyle_preset:
            if sticker_policy.is_sticker_preset(preset):
                raise HTTPException(
                    400, "sticker 预设（带字表情包）不支持服装/发型组合：图片文案与服装段语义冲突"
                )
            try:
                # 层级固定 动作→发型→服装：发型层先并、服装层后并（outfit.combine
                # 把发型层产物当输入继续叠），id/text 层级由两层 combine 自然落位。
                if req.hairstyle_preset:
                    hairstyle_obj = hairstyle_service.load_hairstyle(req.hairstyle_preset)
                    preset = hairstyle_service.combine(preset, hairstyle_obj)
                if req.outfit_preset:
                    outfit_obj = outfit_service.load_outfit(req.outfit_preset)
                    preset = outfit_service.combine(preset, outfit_obj, req.outfit_order)
            except PresetError as exc:
                raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
        sticker_warnings: list[str] = []
        if sticker_policy.is_sticker_preset(preset):
            # 生成期冲突检查：主体档案锁定项为阻断错误；模板条款作用域无法机械
            # 判断 → 不阻断，作为 run 声明返回给前端提示人工确认。classic 不强制模板，
            # 有就顺带查一次。
            with _db() as conn:
                template = storage.get_subject_template(conn, subject.id)
            issues = sticker_policy.validate_sticker_conflicts(subject, template)
            if issues["errors"]:
                raise HTTPException(400, "\n".join(issues["errors"]))
            sticker_warnings = issues["warnings"]
        if req.mode == "llm":
            return _generate_llm_mode(subject, req, preset, sticker_warnings)
        # 参考图先校验（来源合法 / 文件在 / role 合法）：经典模式也要当场报错，
        # 别等到第 ③ 步推送生图才发现这张图选不出来。
        if req.references:
            _reference_paths_from_specs(req.references, db_path=db_path)
        try:
            result = compose_generate(subject, preset, req)
        except (ValueError, PresetError) as exc:
            raise HTTPException(400, str(exc)) from exc
        result.warnings = sticker_warnings
        # 参考图规格存进结果：推送生图时按它取图（页面刷新也不丢）
        result.references = [spec.model_dump() for spec in req.references]
        with _db() as conn:
            storage.save_run(conn, result)
        return result

    def _generate_llm_mode(
        subject: SubjectProfile, req: GenerateRequest, preset, sticker_warnings: list[str] | None = None
    ) -> GenerateResult:
        if not req.subject_id:
            raise HTTPException(400, "LLM 模式需要 subject_id：参考图来自主体档案的图片槽位")
        # 参考图：新流程给 references（Playground 式来源 + role 分工），
        # 提示词阶段只用 role 定锚定句分工；没给就回退旧的 reference_kinds。
        roles: list[str] = []
        if req.references:
            _paths, roles, _staged = _reference_paths_from_specs(req.references, db_path=db_path)
            if not 1 <= len(roles) <= 4:
                raise HTTPException(400, "LLM 模式需要 1–4 张参考图")
        else:
            for kind in req.reference_kinds:
                if kind not in subject_images.IMAGE_KINDS:
                    raise HTTPException(400, f"未知参考图类型：{kind}（支持 {'/'.join(subject_images.IMAGE_KINDS)}）")
                if kind not in roles:
                    roles.append(kind)
            if not 1 <= len(roles) <= 4:
                raise HTTPException(400, "LLM 模式需要 1–4 个参考图类型（reference_kinds）")
        with _db() as conn:
            if not req.references:
                for kind in roles:
                    slot = subject_images.get_slot(conn, subject.id, kind, settings_service.get_settings(conn))
                    if not slot.is_ready:
                        raise HTTPException(400, f"参考图未就绪：{kind}（请先在主体档案里生成或上传）")
            template = storage.get_subject_template(conn, subject.id)
            if not template:
                raise HTTPException(400, f"主体 {subject.id} 还没有上传「角色全场景通用提示词模板」")
            if sticker_warnings:
                result_warnings = list(sticker_warnings)
            else:
                issues = sticker_policy.validate_sticker_conflicts(subject, template) if sticker_policy.is_sticker_preset(preset) else {"errors": [], "warnings": []}
                if issues["errors"]:
                    raise HTTPException(400, "\n".join(issues["errors"]))
                result_warnings = issues["warnings"]
            try:
                result = generate_llm(
                    subject,
                    preset,
                    req,
                    template,
                    roles,
                    config=settings_service.llm_overrides(conn),
                )
            except llm.LLMError as exc:
                raise HTTPException(502, f"LLM 改写提示词失败：{exc}") from exc
            except (ValueError, PresetError) as exc:
                raise HTTPException(400, str(exc)) from exc
            # 合并而不是覆盖：generate_llm 自己也可能带声明（如 sticker 忽略「中文」）
            result.warnings = [*result_warnings, *(result.warnings or [])]
            # 参考图规格存进结果：推送生图时按它取图（页面刷新也不丢）
            result.references = [spec.model_dump() for spec in req.references]
            storage.save_run(conn, result)
        return result

    # ---- 历史记录 ----
    @app.get("/runs")
    def api_runs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        with _db() as conn:
            return storage.list_runs(conn, limit)

    @app.get("/runs/{run_id}")
    def api_run_get(run_id: str) -> GenerateResult:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
        if result is None:
            raise HTTPException(404, f"生成记录不存在：{run_id}")
        return result

    @app.delete("/runs/{run_id}")
    def api_run_delete(run_id: str) -> dict[str, Any]:
        with _db() as conn:
            ok = storage.delete_run(conn, run_id)
        if not ok:
            raise HTTPException(404, f"生成记录不存在：{run_id}")
        return {"ok": True, "deleted": run_id}

    @app.get("/runs/{run_id}/export")
    def api_run_export(
        run_id: str,
        format: str = Query("txt"),
        download: bool = Query(False),
    ) -> Response:
        if format not in EXPORT_FORMATS:
            raise HTTPException(400, f"format 只支持 {'/'.join(EXPORT_FORMATS)}")
        with _db() as conn:
            result = storage.get_run(conn, run_id)
        if result is None:
            raise HTTPException(404, f"生成记录不存在：{run_id}")
        disposition = f'attachment; filename="{run_id}.{ "zip" if format.endswith("zip") else ("jsonl" if format == "jsonl" else "txt")}"'
        headers = {"Content-Disposition": disposition} if download else None
        if format == "txt":
            return PlainTextResponse(txt_exporter.render(result), headers=headers)
        if format == "jsonl":
            return PlainTextResponse(jsonl_exporter.render(result), headers=headers, media_type="application/x-ndjson")
        data = captions_exporter.write_zip_bytes(result)
        return Response(data, media_type="application/zip", headers=headers)

    @app.get("/runs/{run_id}/comfyui")
    def api_run_comfy_submissions(run_id: str) -> list[dict[str, Any]]:
        with _db() as conn:
            if storage.get_run(conn, run_id) is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            return storage.list_comfy_submissions(conn, run_id)

    def _submit_to_comfy(
        conn,
        result: GenerateResult,
        *,
        workload: str | None,
        negative_prompt: str | None,
        reference_paths: list[Path],
        aspect_ratio: str | None,
        resolution: str | None,
        comfy_url: str | None,
        wait: bool,
        overrides_extra: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """批量入队 / 单条重新生成共用的提交流水线。

        workflow 解析（显式名 > 按参考图自动选，未配置 → 400）→ sticker 负向预检 →
        参考图上传注入 image1..N → submit → 记录落库。submit 的 on_submitted 回调
        提交即落库单条记录（prompt_id + 空 output_files）：批量 wait 遇到超时/中断
        时记录不丢，凭 prompt_id 可补拉。返回（解析后 workload 名, submissions）。
        """
        workload_name = workload
        if not workload_name:
            # 自动选择：入了参考图 = 图片编辑；否则文生图（选型依据统一在「设置」页）
            settings = settings_service.get_settings(conn)
            workload_name = (
                settings.comfy_edit_workflow if reference_paths else settings.comfy_t2i_workflow
            )
            if not workload_name:
                raise HTTPException(
                    400, "未指定 workflow：请先在「设置」里配置（文生图 / 图片编辑）workflow"
                )
        try:
            workflow, mapping = comfyui_exporter.load_workflow_pair(workload_name)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(400, f"workflow/mapping 无效：{exc}") from exc

        overrides = dict(overrides_extra or {})
        if negative_prompt is not None:
            overrides["negative_prompt"] = negative_prompt

        if any((item.meta or {}).get("purpose") == "sticker" for item in result.items):
            # 带字预设（按生成时 meta 快照识别；老记录无快照 → legacy，不走此预检）：
            # 校验最终生效的负向词；mapping 无注入字段且 workflow 自带禁字默认 → 阻断。
            negative_entry = next(
                (e for e in mapping.get("mappings", [])
                 if isinstance(e, dict) and str(e.get("field")) == "negative_prompt"),
                None,
            )
            default_bans = sticker_policy.workflow_default_negative_bans(workflow, mapping)
            if negative_entry is None:
                if default_bans:
                    raise HTTPException(400, (
                        "带字预设入队阻断：workflow 自带禁字默认（"
                        + "、".join(f"「{b}」" for b in default_bans[:4])
                        + "），而 mapping.json 没有 negative_prompt 注入字段，无法在提交时清空。"
                        "请给 mapping.json 补上实际负向节点的 negative_prompt 字段，"
                        "或去掉 workflow 里的禁字默认（不要硬编码节点 id）"
                    ))
            else:
                existing = overrides.get("negative_prompt")
                effective = existing if existing is not None else ""
                # 空串 = 明确清空内置禁字默认；None/缺失 = 继承（sticker 场景下按空串处理）
                bans = sticker_policy.find_negative_bans(effective)
                if bans:
                    raise HTTPException(400, (
                        "负向提示词与带字预设冲突（禁字 token："
                        + "、".join(f"「{b}」" for b in bans)
                        + "）。如只想禁止多余文字请写成 no extra text / no unrelated writing，"
                        "或清空负向输入（继承默认会注入空负向以清掉 workflow 内置禁字默认）"
                    ))
                overrides["negative_prompt"] = effective
        if reference_paths:
            # 把参考图上传到 ComfyUI input，按序注入 image/image2/…（对应 <image1>..）
            # remote_name 必须唯一：upload_image 发 overwrite: true，同名会互相覆盖
            base = paths.comfy_base_url(comfy_url)
            for index, prepared in enumerate(reference_paths):
                try:
                    remote = comfyui_exporter.upload_image(
                        base, prepared, remote_name=f"charvar_{result.run_id}_{index}{prepared.suffix}"
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    raise HTTPException(502, f"上传参考图到 ComfyUI 失败：{exc}") from exc
                overrides["image" if index == 0 else f"image{index + 1}"] = remote

        def _on_submitted(item, prompt_id: str, prefix: str) -> None:
            storage.save_comfy_submission(
                conn, result.run_id, item.index, item.variant_id, prompt_id, prefix, []
            )
            conn.commit()  # 落库要当场生效（超时/中断后凭 prompt_id 补拉）

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
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, f"mapping 注入失败：{exc}") from exc
        storage.save_comfy_submissions(conn, result.run_id, submissions)
        return workload_name, submissions

    @app.post("/runs/{run_id}/comfyui")
    def api_run_comfyui(run_id: str, req: ComfyQueueRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            reference_paths = _resolve_run_references(
                req.references, req.reference_kinds, result.subject_id
            )
            workload_name, submissions = _submit_to_comfy(
                conn,
                result,
                workload=req.workload,
                negative_prompt=req.negative_prompt,
                reference_paths=reference_paths,
                aspect_ratio=req.aspect_ratio,
                resolution=req.resolution,
                comfy_url=req.comfy_url,
                wait=req.wait,
                overrides_extra=dict(req.overrides),
            )
            # 快照本次最终生效的入队参数：图片墙单条「重新生成」原样重放（种子除外）
            storage.save_queue_settings(conn, run_id, {
                "provider": "comfyui",
                "workload": workload_name,
                "negative_prompt": req.negative_prompt,
                "aspect_ratio": req.aspect_ratio,
                "resolution": req.resolution,
                "reference_kinds": list(req.reference_kinds),
                "references": [spec.model_dump() for spec in req.references],
                "comfy_url": req.comfy_url,
            })
        ok_count = sum(1 for s in submissions if s.get("ok"))
        return {
            "run_id": run_id,
            "comfy_url": paths.comfy_base_url(req.comfy_url),
            "workload": workload_name,
            "wait": req.wait,
            "succeeded": ok_count,
            "failed": len(submissions) - ok_count,
            "submissions": submissions,
        }

    def _openrouter_reference_paths(
        subject_id: str, reference_kinds: list[str]
    ) -> list[Path]:
        """按勾选顺序取主体图（顺序 = 提示词里的 the first/second… reference image）。

        走预处理过的压缩版（长边 1536 / JPEG q88），与 ComfyUI 上传同一份 ——
        原始 PNG 有 2.4–3.5MB，转 data URL 会让请求体涨到十几 MB。
        """
        paths: list[Path] = []
        seen: set[str] = set()
        subject_dir = subject_images.subject_dir(subject_id)
        for kind in reference_kinds:
            if kind not in subject_images.IMAGE_KINDS or kind in seen:
                continue
            seen.add(kind)
            local = subject_images.find_reference_file(subject_id, kind)
            if local is None:
                raise HTTPException(400, f"参考图不存在：{kind}（请先在主体档案里生成或上传）")
            paths.append(reference_images.prepared_reference(local, subject_dir, kind))
        return paths

    def _resolve_run_references(
        references: list[ReferenceSpec],
        reference_kinds: list[str],
        subject_id: str,
    ) -> list[Path]:
        """推送生图时的参考图 → 本地文件列表。

        新流程（references，Playground 式来源）优先；没给就回退旧接口的
        主体档案槽位（reference_kinds），老调用不受影响。
        """
        if references:
            paths, _roles, _staged = _reference_paths_from_specs(references, db_path=db_path)
            return paths
        return _openrouter_reference_paths(subject_id, reference_kinds)

    def _openrouter_prompt(item: PromptItem, reference_paths: list[Path]) -> str:
        """条目提示词 → OpenRouter 提示词。

        OpenRouter 没有「标签→图片」绑定语义，参考图靠 input_references 的顺序传，
        所以提示词里的 ``<imageN>`` 要换成序数词（the first reference image…）。
        一张参考图都没有时，指向子句整段摘掉（留着会变成 "as ." 这种残句）。
        """
        if reference_paths:
            return openrouter_image.rewrite_reference_tags(item.prompt)
        return openrouter_image.strip_reference_tags(item.prompt)

    def _openrouter_generate_one(
        result: GenerateResult,
        item: PromptItem,
        *,
        aspect_ratio: str | None,
        resolution: str | None,
        reference_paths: list[Path],
        model: str,
        api_key: str | None,
        seed: int | None,
        quality: str | None = None,
    ) -> dict[str, Any]:
        """生成一条并落盘；返回 {index, variant_id, ok, status, error?, cost?, generation_id?}。"""
        entry: dict[str, Any] = {
            "index": item.index,
            "variant_id": item.variant_id,
            "ok": False,
            "status": "error",
        }
        try:
            ratio = aspect_ratio or str((item.meta or {}).get("preferred_aspect_ratio") or "")
            # resolution 是合并后的输入（charvar 档位或 provider 枚举值）；
            # 像素档位按模型枚举归一，provider 参数能不能发也由它定
            pixel_tier = openrouter_image.pixel_resolution_tier(model, resolution)
            width, height = comfyui_exporter.dimensions_for_item(
                ratio or "auto", pixel_tier, item, use_model_native=False
            )
            effective_seed = seed if seed is not None else (item.meta or {}).get("subseed")
            op_result = openrouter_image.generate_image(
                prompt=_openrouter_prompt(item, reference_paths),
                width=width,
                height=height,
                model=model,
                api_key=api_key,
                seed=int(effective_seed) if effective_seed is not None else None,
                reference_paths=reference_paths,
                aspect_ratio=ratio or None,
                resolution=resolution,
                quality=quality,
            )
            if not op_result.images:
                raise openrouter_image.OpenRouterImageError("OpenRouter 未返回图片")
            data = op_result.images[0]
            suffix = openrouter_image.sniff_image_ext(data)
            assets.save_run_image(result.run_id, item.index, data, suffix)
            entry.update(
                {
                    "ok": True,
                    "status": "done",
                    "cost": op_result.cost,
                    "generation_id": op_result.generation_id,
                    "model": model,
                }
            )
        except openrouter_image.OpenRouterImageError as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    def _submit_to_openrouter(
        conn,
        result: GenerateResult,
        *,
        aspect_ratio: str | None,
        resolution: str | None,
        reference_paths: list[Path],
        model: str | None,
        seed: int | None,
        quality: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """批量走 OpenRouter 生图：并发 4 路，逐条落盘 + 落库。

        单条失败不中断整批（与 ComfyUI 批量入队同一语义）。
        """
        mcfg = settings_service.openrouter_mode_config(conn)["t2i" if not reference_paths else "i2i"]
        # 模式对齐 ComfyUI：带参考图 = 图片编辑（i2i / edit workflow），否则文生图（t2i）；
        # 参数默认链：请求显式 > 设置页/env 的模式默认，枚举校验留给 openrouter_image
        effective_model = openrouter_image.resolve_model(
            (model or "").strip() or mcfg["model"], mode="i2i" if reference_paths else "t2i"
        )
        effective_resolution = (resolution or "").strip() or mcfg["resolution"]
        effective_quality = (quality or "").strip() or mcfg["quality"]
        api_key = openrouter_image.resolve_api_key(settings_service.get_settings(conn).openrouter.api_key or None)
        entries: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=_OPENROUTER_CONCURRENCY) as pool:
            futures = {
                pool.submit(
                    _openrouter_generate_one,
                    result,
                    item,
                    aspect_ratio=aspect_ratio,
                    resolution=effective_resolution,
                    reference_paths=reference_paths,
                    model=effective_model,
                    api_key=api_key,
                    seed=seed,
                    quality=effective_quality,
                ): item
                for item in result.items
            }
            for future in as_completed(futures):
                entries.append(future.result())
        entries.sort(key=lambda e: int(e["index"]))
        storage.save_openrouter_images(conn, result.run_id, entries)
        return effective_model, entries

    @app.post("/runs/{run_id}/openrouter")
    def api_run_openrouter(run_id: str, req: OpenRouterQueueRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            reference_paths = _resolve_run_references(
                req.references, req.reference_kinds, result.subject_id
            )
            model, entries = _submit_to_openrouter(
                conn,
                result,
                aspect_ratio=req.aspect_ratio,
                resolution=req.resolution,
                reference_paths=reference_paths,
                model=req.model,
                seed=req.seed,
                quality=req.quality,
            )
            # 快照本次最终生效的入队参数（含模式默认合并后的参数）：
            # 图片墙单条「重新生成」原样重放（种子除外）
            storage.save_queue_settings(conn, run_id, {
                "provider": "openrouter",
                "model": model,
                "aspect_ratio": req.aspect_ratio,
                "resolution": req.resolution,
                "reference_kinds": list(req.reference_kinds),
                "references": [spec.model_dump() for spec in req.references],
                "quality": req.quality,
            })
        ok_count = sum(1 for e in entries if e.get("ok"))
        return {
            "run_id": run_id,
            "provider": "openrouter",
            "model": model,
            "succeeded": ok_count,
            "failed": len(entries) - ok_count,
            "items": entries,
        }

    def _dreamina_generate_one(
        result: GenerateResult,
        item: PromptItem,
        *,
        aspect_ratio: str | None,
        resolution: str,
        reference_paths: list[Path],
        model: str,
    ) -> dict[str, Any]:
        """生成一条即梦图并落盘；返回 {index, variant_id, ok, status, error?, submit_id?}。"""
        entry: dict[str, Any] = {
            "index": item.index,
            "variant_id": item.variant_id,
            "ok": False,
            "status": "error",
        }
        import shutil

        try:
            ratio = aspect_ratio or str((item.meta or {}).get("preferred_aspect_ratio") or "")
            if ratio in ("", "auto"):
                ratio = "3:4"  # 即梦没有 auto：退到 charvar 默认档
            # 提示词处理与 OpenRouter 通路一致：<imageN> 有参考图换序数词，无参考图摘掉
            prompt = (
                openrouter_image.rewrite_reference_tags(item.prompt)
                if reference_paths
                else openrouter_image.strip_reference_tags(item.prompt)
            )
            download_dir = assets.original_dir(result.run_id).parent / f"_dreamina_dl_{item.index}"
            try:
                dm_result = dreamina_image.generate(
                    prompt=prompt,
                    download_dir=download_dir,
                    resolution=resolution,
                    model=model,
                    ratio=ratio,
                    reference_paths=reference_paths or None,
                )
                if not dm_result.images:
                    raise dreamina_image.DreaminaCliError("即梦任务成功但未落盘图片")
                # 先把产物字节拷出来，再清临时下载目录
                data = dm_result.images[0].read_bytes()
            finally:
                shutil.rmtree(download_dir, ignore_errors=True)
            suffix = openrouter_image.sniff_image_ext(data)
            if suffix == ".bin":
                raise dreamina_image.DreaminaCliError("即梦返回的不是可识别的图片格式")
            assets.save_run_image(result.run_id, item.index, data, suffix)
            entry.update(
                {
                    "ok": True,
                    "status": "done",
                    "submit_id": dm_result.submit_id,
                    "width": dm_result.width,
                    "height": dm_result.height,
                    "model": model,
                }
            )
        except dreamina_image.DreaminaImageError as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    def _submit_to_dreamina(
        conn,
        result: GenerateResult,
        *,
        aspect_ratio: str | None,
        resolution: str | None,
        reference_paths: list[Path],
        model: str | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """批量走即梦生图：小并发（_DREAMINA_CONCURRENCY），逐条落盘。

        单条失败不中断整批（与 OpenRouter 批量同一语义）；每张消耗即梦积分/权益。
        """
        mcfg = settings_service.dreamina_config(conn)
        effective_model = (model or "").strip() or mcfg["model"]
        effective_resolution = (resolution or "").strip() or mcfg["resolution"]
        entries: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=_DREAMINA_CONCURRENCY) as pool:
            futures = {
                pool.submit(
                    _dreamina_generate_one,
                    result,
                    item,
                    aspect_ratio=aspect_ratio,
                    resolution=effective_resolution,
                    reference_paths=reference_paths,
                    model=effective_model,
                ): item
                for item in result.items
            }
            for future in as_completed(futures):
                entries.append(future.result())
        entries.sort(key=lambda e: int(e["index"]))
        return effective_model, entries

    @app.post("/runs/{run_id}/dreamina")
    def api_run_dreamina(run_id: str, req: DreaminaQueueRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            reference_paths = _resolve_run_references(
                req.references, req.reference_kinds, result.subject_id
            )
            model, entries = _submit_to_dreamina(
                conn,
                result,
                aspect_ratio=req.aspect_ratio,
                resolution=req.resolution,
                reference_paths=reference_paths,
                model=req.model,
            )
            # 快照本次最终生效的入队参数（图片墙单条「重新生成」原样重放，种子除外）
            storage.save_queue_settings(conn, run_id, {
                "provider": "dreamina",
                "model": model,
                "aspect_ratio": req.aspect_ratio,
                "resolution": req.resolution,
                "reference_kinds": list(req.reference_kinds),
                "references": [spec.model_dump() for spec in req.references],
            })
        ok_count = sum(1 for e in entries if e.get("ok"))
        return {
            "run_id": run_id,
            "provider": "dreamina",
            "model": model,
            "succeeded": ok_count,
            "failed": len(entries) - ok_count,
            "items": entries,
        }

    @app.get("/openrouter/models")
    def api_openrouter_models(refresh: bool = False) -> dict[str, Any]:
        """模型清单。``refresh=true`` 强制重新拉取并覆盖本地缓存。

        默认读本地缓存（``data/openrouter/api-response.json``），模型换了才需要
        手动刷新一次 —— 清单是参数能力的唯一标准来源。
        """
        try:
            if refresh:
                openrouter_image.list_models(force_refresh=True)
            return {"models": openrouter_image.model_choices()}
        except openrouter_image.OpenRouterImageError as exc:
            raise HTTPException(502, f"读 OpenRouter 模型清单失败：{exc}") from exc

    @app.get("/openrouter/capabilities")
    def api_openrouter_capabilities(model: str) -> dict[str, Any]:
        """某模型的能力：清单声明 + 探测缓存。

        前端据此动态生成面板（画幅档位 / 质量档 / 参考图开关）——
        声明为空或缺项的模型（如 muse-image）走实际能力，而不是硬编码。
        """
        caps = openrouter_image.model_capabilities(model)
        return {
            "model": caps.model_id,
            "declared": sorted(caps.declared),
            "allowed": sorted(caps.allowed),
            "rejected": sorted(caps.rejected),
            "aspect_ratios": list(caps.aspect_ratios),
            "resolutions": list(caps.resolutions),
            "qualities": list(caps.qualities),
            "supports_references": caps.supports_refs,
        }

    # ---- Playground（跨项目调用同一通路的网页入口） ----

    def _adhoc_reference_paths(
        references: list[AdhocReferenceSpec],
    ) -> tuple[list[Path], list[str]]:
        """参考图 spec → 本地路径列表（+ 暂存 id 列表，出图成功后清理）。

        只认三种来源：上传暂存的（staged_id）、主体档案里的（subject_id + kind）、
        Playground「最近生成」的原图（run_id，文件名由服务端从该 run 目录取）。
        不接受任意路径 —— 那是把文件读权限开给了浏览器。
        """
        paths_found: list[Path] = []
        staged_ids: list[str] = []
        for spec in references:
            if spec.staged_id:
                try:
                    paths_found.append(adhoc_service.staged_reference(spec.staged_id))
                except adhoc_service.AdhocError as exc:
                    raise HTTPException(400, str(exc)) from exc
                staged_ids.append(spec.staged_id)
                continue
            if spec.subject_id and spec.kind:
                _require_subject(spec.subject_id)
                if spec.kind not in subject_images.IMAGE_KINDS:
                    raise HTTPException(
                        400, f"未知图片类型：{spec.kind}（支持 {'/'.join(subject_images.IMAGE_KINDS)}）"
                    )
                local = subject_images.find_reference_file(spec.subject_id, spec.kind)
                if local is None:
                    raise HTTPException(
                        400, f"参考图不存在：{spec.subject_id} / {spec.kind}（请先在主体档案里生成或上传）"
                    )
                # 走预处理过的压缩版（长边 1536 / JPEG q88），与 ComfyUI 上传同一份
                subject_dir = subject_images.subject_dir(spec.subject_id)
                paths_found.append(
                    reference_images.prepared_reference(local, subject_dir, spec.kind)
                )
                continue
            if spec.run_id:
                # 最近生成的原图：run_id 过 adhoc 自己的严格正则（挡 ".."），
                # 文件从该 run 的 original/ 目录按落盘布局定 —— 不收浏览器给的路径
                try:
                    paths_found.append(adhoc_service.original_file(spec.run_id))
                except FileNotFoundError as exc:
                    raise HTTPException(400, str(exc)) from exc
                except adhoc_service.AdhocError as exc:
                    raise HTTPException(400, str(exc)) from exc
                continue
            if spec.pose_id:
                # 姿势库的灰人偶线稿：预处理压缩版与两条生图通路共用
                try:
                    paths_found.append(poses_service.prepared_reference_file(spec.pose_id, spec.angle, db_path))
                except poses_service.PoseError as exc:
                    raise HTTPException(400, str(exc)) from exc
                continue
            raise HTTPException(
                400, "参考图缺少来源（staged_id、subject_id + kind、run_id 或 pose_id + angle）"
            )
        return paths_found, staged_ids

    @app.post("/adhoc/references")
    def api_adhoc_reference_upload(file: UploadFile) -> dict[str, Any]:
        """上传一张参考图 → 暂存并返回 id（出图时按 id 取）。"""
        data = file.file.read()
        try:
            path = adhoc_service.save_staged_reference(data, file.filename or "")
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc
        # 顺带清掉过期的暂存图（用户传了没出图就走了的那些）
        adhoc_service.prune_staged_references()
        return {"id": path.stem, "name": file.filename or path.name, "path": str(path)}

    @app.delete("/adhoc/references/{ref_id}")
    def api_adhoc_reference_delete(ref_id: str) -> dict[str, Any]:
        try:
            adhoc_service.staged_reference(ref_id).unlink(missing_ok=True)
        except adhoc_service.AdhocError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.get("/adhoc/reference/{ref_id}")
    def api_adhoc_reference_file(ref_id: str) -> FileResponse:
        """暂存参考图的预览（出图成功后会被清掉，404 是正常的）。"""
        try:
            return FileResponse(adhoc_service.staged_reference(ref_id))
        except adhoc_service.AdhocError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/adhoc/generate")
    def api_adhoc_generate(req: AdhocGenerateRequest) -> dict[str, Any]:
        """Playground：给一段提示词出一张图。

        走的是 CLI ``charvar image`` 同一套（``charvar.adhoc``），不落 runs 表。
        """
        reference_paths, staged_ids = _adhoc_reference_paths(req.references)
        adhoc_req = adhoc_service.AdhocRequest(
            prompt=req.prompt,
            backend=req.backend,
            negative_prompt=req.negative_prompt,
            reference_paths=reference_paths,
            aspect_ratio=req.aspect_ratio,
            resolution=req.resolution,
            seed=req.seed,
            model=req.model,
            quality=req.quality,
            comfy_url=req.comfy_url,
            workflow=req.workflow,
            wait=req.wait,
        )
        try:
            result = adhoc_service.generate(adhoc_req, db_path)
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 后端出图失败统一转 502
            raise HTTPException(502, f"生图失败：{exc}") from exc
        # 暂存参考图用完了就清掉（失败时保留，方便重试）
        if result.status == "ready":
            adhoc_service.clear_staged_references(staged_ids)
        payload = result.model_dump()
        run_id = Path(result.manifest_path).parent.name if result.manifest_path else ""
        payload["run_id"] = run_id
        payload["image_url"] = f"/adhoc/{run_id}/image" if run_id else ""
        payload["thumb_url"] = f"/adhoc/{run_id}/thumb" if run_id else ""
        return payload

    @app.get("/adhoc")
    def api_adhoc_list(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        """最近的 Playground 记录（读 manifest.json；不落 runs 表）。"""
        return adhoc_service.list_recent(limit)

    @app.get("/adhoc/{run_id}/image")
    def api_adhoc_image(run_id: str) -> FileResponse:
        try:
            return FileResponse(adhoc_service.original_file(run_id))
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/adhoc/{run_id}/thumb")
    def api_adhoc_thumb(run_id: str) -> FileResponse:
        try:
            return FileResponse(adhoc_service.thumb_file(run_id), media_type="image/webp")
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/adhoc/{run_id}/manifest")
    def api_adhoc_manifest(run_id: str) -> dict[str, Any]:
        try:
            path = adhoc_service.run_dir(run_id) / "manifest.json"
        except adhoc_service.AdhocError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"没有 manifest：{run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- Playground 视频（charvar.adhoc_video：web 与 CLI charvar video 同一通路） ----
    # 注意路由声明顺序：字面路径（/videos/models、/videos/references…）必须在
    # /videos/{run_id}/… 之前，否则 run_id 通配会把它们吞掉。

    def _video_reference_spec_paths(
        specs: list[AdhocReferenceSpec],
    ) -> tuple[list[Path], list[str]]:
        """参考图 spec → 本地路径列表（+ 暂存 id 列表，成片 ready 后清理）。

        与 /adhoc 的 `_adhoc_reference_paths` 同一套规则：只认上传暂存（staged_id）、
        主体档案就绪图（subject_id + kind）、Playground「最近生成」的原图（run_id），
        **不接受任意路径** —— 那是把文件读权限开给了浏览器。暂存池走 adhoc_video
        自己的（出图/出片各自清理，互不误删）。
        """
        paths_found: list[Path] = []
        staged_ids: list[str] = []
        for spec in specs:
            if spec.staged_id:
                try:
                    paths_found.append(adhoc_video_service.staged_reference(spec.staged_id))
                except adhoc_video_service.AdhocVideoError as exc:
                    raise HTTPException(400, str(exc)) from exc
                staged_ids.append(spec.staged_id)
                continue
            if spec.subject_id and spec.kind:
                _require_subject(spec.subject_id)
                if spec.kind not in subject_images.IMAGE_KINDS:
                    raise HTTPException(
                        400, f"未知图片类型：{spec.kind}（支持 {'/'.join(subject_images.IMAGE_KINDS)}）"
                    )
                local = subject_images.find_reference_file(spec.subject_id, spec.kind)
                if local is None:
                    raise HTTPException(
                        400,
                        f"参考图不存在：{spec.subject_id} / {spec.kind}（请先在主体档案里生成或上传）",
                    )
                # 走预处理过的压缩版（长边 1536 / JPEG q88），与出图通路同一份
                subject_dir = subject_images.subject_dir(spec.subject_id)
                paths_found.append(
                    reference_images.prepared_reference(local, subject_dir, spec.kind)
                )
                continue
            if spec.run_id:
                # Playground 最近生成的原图：run_id 过严格正则，文件名服务端定
                try:
                    paths_found.append(adhoc_service.original_file(spec.run_id))
                except FileNotFoundError as exc:
                    raise HTTPException(400, str(exc)) from exc
                except adhoc_service.AdhocError as exc:
                    raise HTTPException(400, str(exc)) from exc
                continue
            raise HTTPException(400, "参考图缺少来源（staged_id、subject_id + kind 或 run_id）")
        return paths_found, staged_ids

    @app.get("/videos/models")
    def api_video_models(refresh: bool = Query(False)) -> dict[str, Any]:
        """视频模型目录（每模型的时长/分辨率/画幅枚举 + 价格 SKU；缓存本地 JSON）。"""
        try:
            data = openrouter_video.list_models(force_refresh=refresh)
        except openrouter_video.OpenRouterVideoError as exc:
            raise HTTPException(502, f"读视频模型目录失败：{exc}") from exc
        models: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            models.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "supported_durations": item.get("supported_durations"),
                    "supported_resolutions": item.get("supported_resolutions"),
                    "supported_aspect_ratios": item.get("supported_aspect_ratios"),
                    "supported_frame_images": item.get("supported_frame_images"),
                    "seed": bool(item.get("seed")),
                    "pricing_skus": item.get("pricing_skus") or {},
                }
            )
        with _db() as conn:
            default_model = settings_service.openrouter_video_config(conn)["model"]
        return {"data": models, "default_model": default_model}

    @app.post("/videos/references")
    def api_video_reference_upload(file: UploadFile) -> dict[str, Any]:
        """上传一张参考图/首帧图 → 暂存并返回 id（提交作业时按 id 取）。"""
        data = file.file.read()
        try:
            path = adhoc_video_service.save_staged_reference(data, file.filename or "")
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc
        # 顺带清掉过期的暂存图（用户传了没出片就走了的那些）
        adhoc_video_service.prune_staged_references()
        return {"id": path.stem, "name": file.filename or path.name, "path": str(path)}

    @app.delete("/videos/references/{ref_id}")
    def api_video_reference_delete(ref_id: str) -> dict[str, Any]:
        try:
            adhoc_video_service.staged_reference(ref_id).unlink(missing_ok=True)
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.get("/videos/reference/{ref_id}")
    def api_video_reference_file(ref_id: str) -> FileResponse:
        """暂存参考图的预览（提交成功并成片后会被清掉，404 是正常的）。"""
        try:
            return FileResponse(adhoc_video_service.staged_reference(ref_id))
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/videos/video-references")
    def api_video_reference_upload_video(file: UploadFile) -> dict[str, Any]:
        """上传一条参考视频 → 暂存并返回 id（input_references 的 video_url；Seedance gen2+）。"""
        data = file.file.read()
        try:
            path = adhoc_video_service.save_staged_video_reference(data, file.filename or "")
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc
        adhoc_video_service.prune_staged_video_references()
        return {"id": path.stem, "name": file.filename or path.name, "path": str(path)}

    @app.delete("/videos/video-references/{ref_id}")
    def api_video_reference_delete_video(ref_id: str) -> dict[str, Any]:
        try:
            adhoc_video_service.staged_video_reference(ref_id).unlink(missing_ok=True)
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.get("/videos/video-reference/{ref_id}")
    def api_video_reference_file_video(ref_id: str) -> FileResponse:
        """暂存参考视频的预览/试放（提交成功并成片后会被清掉，404 是正常的）。"""
        try:
            return FileResponse(adhoc_video_service.staged_video_reference(ref_id))
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/videos/generate")
    def api_video_generate(req: VideoGenerateRequest) -> dict[str, Any]:
        """Playground 视频：给一段提示词出一段视频（异步作业，提交即返回）。

        走的是 CLI ``charvar video`` 同一套（``charvar.adhoc_video``），不落 runs 表。
        """
        reference_paths, staged_ids = _video_reference_spec_paths(req.references)
        first_frames, first_staged = (
            _video_reference_spec_paths([req.first_frame]) if req.first_frame else ([], [])
        )
        video_ref_paths: list[Path] = []
        video_staged_ids: list[str] = []
        for spec in req.video_references:
            try:
                video_ref_paths.append(adhoc_video_service.staged_video_reference(spec.staged_id))
            except adhoc_video_service.AdhocVideoError as exc:
                raise HTTPException(400, str(exc)) from exc
            video_staged_ids.append(spec.staged_id)
        video_req = adhoc_video_service.VideoRequest(
            prompt=req.prompt,
            model=(req.model or "").strip() or None,
            duration=req.duration,
            resolution=(req.resolution or "").strip() or None,
            aspect_ratio=req.aspect_ratio,
            seed=req.seed,
            first_frame=first_frames[0] if first_frames else None,
            reference_paths=reference_paths,
            reference_video_paths=video_ref_paths,
            wait=False,
            staged_ref_ids=staged_ids + first_staged,
            staged_video_ref_ids=video_staged_ids,
        )
        try:
            result = adhoc_video_service.generate(video_req, db_path)
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 提交失败统一转 502
            raise HTTPException(502, f"视频提交失败：{exc}") from exc
        payload = result.model_dump()
        payload["status_url"] = f"/videos/{result.run_id}/status"
        return payload

    @app.get("/videos/{run_id}/status")
    def api_video_status(run_id: str) -> dict[str, Any]:
        """查一次作业状态并把成片收下来（前端每 8s 轮询；ready 后不再打 OpenRouter）。"""
        try:
            summary = adhoc_video_service.refresh(run_id, db_path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc
        if summary.get("status") == "ready":
            summary["video_url"] = f"/videos/{run_id}/video"
            summary["poster_url"] = f"/videos/{run_id}/poster"
        return summary

    @app.get("/videos")
    def api_video_list(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        """最近的 Playground 视频记录（读 manifest.json；不落 runs 表）。"""
        return adhoc_video_service.list_recent(limit)

    @app.get("/videos/{run_id}/video")
    def api_video_file(run_id: str) -> FileResponse:
        try:
            return FileResponse(adhoc_video_service.video_file(run_id))
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/videos/{run_id}/poster")
    def api_video_poster(run_id: str) -> FileResponse:
        try:
            return FileResponse(
                adhoc_video_service.poster_file(run_id), media_type="image/jpeg"
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/videos/{run_id}/manifest")
    def api_video_manifest(run_id: str) -> dict[str, Any]:
        try:
            path = adhoc_video_service.run_dir(run_id) / "manifest.json"
        except adhoc_video_service.AdhocVideoError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"没有 manifest：{run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- Playground 语音（charvar.adhoc_tts：web 与 CLI charvar tts 同一通路） ----
    # 同步生成（与视频的异步作业不同）：/tts/generate 等音频字节返回后再响应，
    # 没有 status 轮询。与 /videos 同款路由声明纪律：字面路径（/tts/models、/tts）
    # 必须在 /tts/{run_id}/… 之前，否则 run_id 通配会把它们吞掉。

    _AUDIO_MEDIA_TYPES = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
    }

    @app.get("/tts/models")
    def api_tts_models(refresh: bool = Query(False)) -> dict[str, Any]:
        """语音模型目录（每模型的 voice 枚举 + 目录价；缓存本地 JSON）。"""
        try:
            data = openrouter_tts.list_models(force_refresh=refresh)
        except openrouter_tts.OpenRouterTtsError as exc:
            raise HTTPException(502, f"读语音模型目录失败：{exc}") from exc
        models: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            models.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "supported_voices": item.get("supported_voices") or [],
                    "pricing": item.get("pricing") or {},
                }
            )
        with _db() as conn:
            default_model = settings_service.openrouter_tts_config(conn)["model"]
        return {"data": models, "default_model": default_model}

    @app.post("/tts/generate")
    def api_tts_generate(req: TtsGenerateRequest) -> dict[str, Any]:
        """Playground 语音：给一段文本出一行语音（同步，等音频字节返回）。

        走的是 CLI ``charvar tts`` 同一套（``charvar.adhoc_tts``），不落 runs 表。
        """
        tts_req = adhoc_tts_service.TtsRequest(
            text=req.text,
            model=(req.model or "").strip() or None,
            voice=(req.voice or "").strip(),
            response_format=(req.response_format or "").strip(),
        )
        try:
            result = adhoc_tts_service.generate(tts_req, db_path)
        except adhoc_tts_service.AdhocTtsError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 网络/后端异常统一转 502
            raise HTTPException(502, f"语音合成失败：{exc}") from exc
        payload = result.model_dump()
        payload["audio_url"] = f"/tts/{result.run_id}/audio"
        return payload

    @app.get("/tts")
    def api_tts_list(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        """最近的 Playground 语音记录（读 manifest.json；不落 runs 表）。"""
        return adhoc_tts_service.list_recent(limit)

    @app.get("/tts/{run_id}/audio")
    def api_tts_audio(run_id: str) -> FileResponse:
        try:
            path = adhoc_tts_service.audio_file(run_id)
        except adhoc_tts_service.AdhocTtsError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        media_type = _AUDIO_MEDIA_TYPES.get(
            path.suffix.lower().lstrip("."), "application/octet-stream"
        )
        return FileResponse(path, media_type=media_type)

    @app.get("/tts/{run_id}/manifest")
    def api_tts_manifest(run_id: str) -> dict[str, Any]:
        try:
            path = adhoc_tts_service.run_dir(run_id) / "manifest.json"
        except adhoc_tts_service.AdhocTtsError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"没有 manifest：{run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/runs/{run_id}/regenerate")
    def api_run_regenerate(run_id: str, req: RunRegenerateRequest) -> dict[str, Any]:
        """单条重新生成：同一 prompt / 出图设置（重放上批入队快照），只换采样种子。

        filename_prefix 只由 subject/index/variant_id 组成，单条视图不变 → ComfyUI
        产物命名不漂移，同步仍按 item_index 归位；换种子经由单条视图的 result.seed
        （_injected_values → mapping fixed/increment/template 三种模式全部生效）。
        新图未落盘前旧原图照常显示（sync 的 mtime 刷新规则负责覆盖）。
        """
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            item = next((it for it in result.items if it.index == req.item_index), None)
            if item is None:
                raise HTTPException(404, f"生成记录没有条目：#{req.item_index}")
            seed = req.seed if req.seed is not None else result.seed
            single = result.model_copy(update={"seed": seed, "items": [item]})
            queued = storage.get_queue_settings(conn, run_id) or {}
            meta = item.meta or {}
            # 快照缺失（老 run）回退：LLM 模式生成时 meta 已带 reference_kinds
            reference_kinds = list(queued.get("reference_kinds") or []) or list(meta.get("reference_kinds") or [])
            # 参考图：优先用快照里的新规格（Playground 式来源），否则回退旧的主体槽位
            queued_references = [
                ReferenceSpec.model_validate(spec) for spec in (queued.get("references") or [])
            ]
            reference_paths = _resolve_run_references(
                queued_references, reference_kinds, result.subject_id
            )

            if queued.get("provider") == "dreamina":
                # 即梦通路：同一提示词 + 同一画幅/参考图重出一张。
                # 即梦不暴露 seed —— 请求里的 seed 仅记录，实际每次都是新图。
                mcfg = settings_service.dreamina_config(conn)
                entry = _dreamina_generate_one(
                    single,
                    item,
                    aspect_ratio=queued.get("aspect_ratio"),
                    resolution=(str(queued.get("resolution") or "")).strip() or mcfg["resolution"],
                    reference_paths=reference_paths,
                    model=(str(queued.get("model") or "")).strip() or mcfg["model"],
                )
                if not entry.get("ok"):
                    raise HTTPException(502, f"重新生成失败：{entry.get('error') or '未知错误'}")
                item.meta = {
                    **meta,
                    "regen": {
                        "provider": "dreamina",
                        "seed": seed,
                        "submit_id": entry.get("submit_id"),
                        "submitted_at": storage.utcnow(),
                    },
                }
                storage.save_run(conn, result)
                return {
                    "ok": True,
                    "run_id": run_id,
                    "item_index": item.index,
                    "variant_id": item.variant_id,
                    "seed": seed,
                    "provider": "dreamina",
                    "model": entry.get("model") or "",
                }

            if queued.get("provider") == "openrouter":
                # OpenRouter 通路：同一提示词 + 同一画幅/参考图，只换种子重出一张。
                # provider 不保证同种子出同图（实测两次同参请求返回不同图），
                # 种子只是尽量可复现。快照里的是当时的请求参数，模式默认在此重放一遍。
                mode = "i2i" if reference_paths else "t2i"
                mcfg = settings_service.openrouter_mode_config(conn)[mode]
                model = openrouter_image.resolve_model(
                    (str(queued.get("model") or "")).strip() or mcfg["model"], mode=mode
                )
                entry = _openrouter_generate_one(
                    single,
                    item,
                    aspect_ratio=queued.get("aspect_ratio"),
                    resolution=(str(queued.get("resolution") or "")).strip() or mcfg["resolution"],
                    reference_paths=reference_paths,
                    model=model,
                    api_key=openrouter_image.resolve_api_key(
                        settings_service.get_settings(conn).openrouter.api_key or None
                    ),
                    seed=seed,
                    quality=(str(queued.get("quality") or "")).strip() or mcfg["quality"],
                )
                if not entry.get("ok"):
                    raise HTTPException(502, f"重新生成失败：{entry.get('error') or '未知错误'}")
                item.meta = {
                    **meta,
                    "regen": {
                        "provider": "openrouter",
                        "seed": seed,
                        "generation_id": entry.get("generation_id"),
                        "submitted_at": storage.utcnow(),
                    },
                }
                storage.save_run(conn, result)
                return {
                    "ok": True,
                    "run_id": run_id,
                    "item_index": item.index,
                    "variant_id": item.variant_id,
                    "seed": seed,
                    "provider": "openrouter",
                    "model": model,
                    "cost": entry.get("cost"),
                }

            try:
                workload_name, submissions = _submit_to_comfy(
                    conn,
                    single,
                    workload=queued.get("workload"),
                    negative_prompt=queued.get("negative_prompt"),
                    reference_paths=reference_paths,
                    aspect_ratio=queued.get("aspect_ratio"),
                    resolution=queued.get("resolution"),
                    comfy_url=req.comfy_url,
                    wait=req.wait,
                )
            except TimeoutError as exc:
                # 已入队（on_submitted 已落库），只是等待出图超时：凭 prompt_id 可补拉
                records = storage.list_comfy_submissions(conn, run_id)
                record = next((r for r in records if r["item_index"] == req.item_index), {})
                return {
                    "ok": False,
                    "timed_out": True,
                    "run_id": run_id,
                    "item_index": req.item_index,
                    "variant_id": item.variant_id,
                    "seed": seed,
                    "prompt_id": record.get("prompt_id"),
                    "error": f"已入队但等待出图超时：{exc}。稍后点「同步图片」取图。",
                }
            submission = submissions[0] if submissions else {}
            if not submission.get("ok"):
                # 提交失败（旧记录/旧图未动）：原样返回提交错误
                raise HTTPException(502, f"重新提交失败：{submission.get('error') or '未知错误'}")
            # provenance：把本次种子与 prompt_id 打回 item.meta（历史可查）
            item.meta = {
                **meta,
                "regen": {
                    "seed": seed,
                    "prompt_id": submission.get("prompt_id"),
                    "submitted_at": storage.utcnow(),
                },
            }
            storage.save_run(conn, result)
        return {
            "ok": True,
            "run_id": run_id,
            "item_index": req.item_index,
            "variant_id": item.variant_id,
            "seed": seed,
            "workload": workload_name,
            "prompt_id": submission.get("prompt_id"),
        }

    # ---- 图片资产：历史回看 + contact sheet ----
    @app.post("/runs/{run_id}/sync-outputs")
    def api_run_sync_outputs(run_id: str, req: SyncOutputsRequest | None = None) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            records = storage.list_comfy_submissions(conn, run_id)
            try:
                summary = assets.sync_run_outputs(
                    conn,
                    result,
                    paths.comfy_base_url(),
                    records=records,
                    build_sheets=(req.build_sheets if req else True),
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"同步 ComfyUI outputs 失败：{exc}") from exc
            refreshed = storage.list_comfy_submissions(conn, run_id)
            items = [assets.image_entry(run_id, item.index, item.variant_id, refreshed, item.prompt) for item in result.items]
        completed = sum(1 for entry in items if entry["status"] == "done")
        queued = sum(1 for entry in items if entry["status"] == "queued")
        errors = sum(1 for entry in items if entry["status"] == "error")
        return {
            "run_id": run_id,
            "summary": summary,
            "items": items,
            "completed": completed,
            "queued": queued,
            "errors": errors,
        }

    @app.get("/runs/{run_id}/images")
    def api_run_images(run_id: str) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            records = storage.list_comfy_submissions(conn, run_id)
            items = []
            for item in result.items:
                ensure_thumb(run_id, item.index)
                items.append(assets.image_entry(run_id, item.index, item.variant_id, records, item.prompt))
        contact_sheet = sheet_path(run_id, "contact")
        face_sheet = sheet_path(run_id, "face")
        return {
            "run_id": run_id,
            "completed": sum(1 for entry in items if entry["status"] == "done"),
            "queued": sum(1 for entry in items if entry["status"] == "queued"),
            "errors": sum(1 for entry in items if entry["status"] == "error"),
            "contact_sheet": contact_sheet.name if contact_sheet.exists() else None,
            "face_sheet": face_sheet.name if face_sheet.exists() else None,
            "videos": video_service.list_videos(run_id),
            "items": items,
        }

    @app.get("/runs/{run_id}/comfy-image/{item_index}")
    def api_run_comfy_image(run_id: str, item_index: int) -> Response:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            records = storage.list_comfy_submissions(conn, run_id)
        entry = assets.image_entry(run_id, item_index, None, records)
        if not entry["primary_url"]:
            raise HTTPException(404, "该条目还没有输出文件")
        for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
            try:
                original = assets.resolve_run_asset(run_id, "original", f"{item_index:06d}{suffix}")
                return FileResponse(original)
            except FileNotFoundError:
                continue
        file = next(iter(entry["files"]), None)
        if not file:
            raise HTTPException(404, "缺少输出文件")
        base = paths.comfy_base_url()
        params = {
            "filename": file.get("filename", ""),
            "subfolder": file.get("subfolder", ""),
            "type": file.get("type", "output"),
        }
        with comfyui_exporter._httpx_client(timeout=10.0) as client:
            resp = client.get(f"{base}/view", params=params, timeout=10)
            resp.raise_for_status()
        media_type = resp.headers.get("content-type") or "image/png"
        if not media_type.startswith("image/"):
            media_type = "image/png"
        return Response(resp.content, media_type=media_type)

    @app.get("/runs/{run_id}/assets/{scene}/{filename}")
    def api_run_asset(run_id: str, scene: str, filename: str) -> FileResponse:
        try:
            return FileResponse(resolve_run_asset(run_id, scene, filename))
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/runs/{run_id}/sheets/{kind}")
    def api_run_sheet(run_id: str, kind: str) -> FileResponse:
        try:
            target = sheet_path(run_id, kind)
            if not target.exists():
                raise FileNotFoundError(target.name)
            return FileResponse(target)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/runs/{run_id}/contact-sheet")
    def api_run_contact_sheet(run_id: str, req: ContactSheetRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            records = storage.list_comfy_submissions(conn, run_id)
            if not records:
                raise HTTPException(404, "run 还没有提交到 ComfyUI")
            try:
                path = assets.make_contact_sheet(run_id, result, records, mode=req.mode)
            except Exception as exc:
                raise HTTPException(400, f"生成 contact sheet 失败：{exc}") from exc
        return {"ok": True, "mode": req.mode, "path": str(path)}

    # ---- 视频合成：预设文件（presets/video/*.yaml）+ 本地 ffmpeg ----

    @app.get("/video-presets")
    def api_video_presets() -> list[dict[str, Any]]:
        presets = []
        for preset in video_service.list_video_presets():
            # aspect: auto 的尺寸取决于渲染时本组图片的实际比例 → size 置 None，前端显示"自适应"
            size = (
                list(video_service.resolution_size(preset.aspect, preset.resolution))
                if preset.aspect != "auto"
                else None
            )
            presets.append(
                {
                    "name": preset.name,
                    "description": preset.description,
                    "aspect": preset.aspect,
                    "resolution": preset.resolution,
                    "size": size,
                    "fps": preset.fps,
                    "seconds_per_image": preset.seconds_per_image,
                    "include_contact_sheet": preset.include_contact_sheet,
                    "contact_sheet_seconds": preset.contact_sheet_seconds,
                    "order_mode": preset.order_mode,
                    "direction": preset.direction,
                    "fit": preset.fit,
                    "music": preset.music,
                }
            )
        return presets

    @app.get("/video-music")
    def api_video_music() -> dict[str, Any]:
        """presets/video/music/ 里的可用音乐文件（视频配置弹窗下拉用）。"""
        return {"files": video_service.music_files()}

    @app.post("/runs/{run_id}/video")
    def api_run_video(run_id: str, req: VideoRenderRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
            if result is None:
                raise HTTPException(404, f"生成记录不存在：{run_id}")
            records = storage.list_comfy_submissions(conn, run_id)
            try:
                preset = video_service.load_video_preset(req.preset)
            except video_service.VideoPresetError as exc:
                raise HTTPException(400, str(exc)) from exc
            try:
                # req.music：None/"" = 预设内置音乐；"none" = 不加；文件名 = 覆盖（文件名
                # 合法性与存在性在 render_video 里校验，报错走下方 VideoPresetError → 400）
                info = video_service.render_video(
                    run_id, result, records, preset, music_override=req.music
                )
            except video_service.VideoPresetError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, f"合成视频失败：{exc}") from exc
        info["run_id"] = run_id
        info["url"] = f"/runs/{run_id}/assets/videos/{info['name']}"
        return info

    # ---- 贴字（presets/overlays/*.yaml，PIL 后处理，不改原图） ----

    @app.get("/overlay-presets")
    def api_overlay_presets() -> list[dict[str, Any]]:
        return overlay_service.list_overlay_presets()

    @app.post("/runs/{run_id}/overlay")
    def api_run_overlay(run_id: str, req: OverlayApplyRequest) -> dict[str, Any]:
        with _db() as conn:
            try:
                return overlay_service.apply_overlay(
                    conn,
                    run_id,
                    overlay=req.overlay,
                    text_from=req.text_from,
                    fixed=req.fixed,
                    items=req.items,
                )
            except overlay_service.PresetError as exc:
                raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, f"贴字失败：{exc}") from exc

    # ---- 配方（presets/recipes/*.yaml + 线性执行器，状态落盘不建表） ----

    @app.get("/recipes")
    def api_recipes() -> list[dict[str, Any]]:
        return recipes_service.list_recipes()

    @app.get("/recipes/{name}")
    def api_recipe_detail(name: str) -> dict[str, Any]:
        try:
            recipe = recipes_service.load_recipe(name)
        except recipes_service.PresetError as exc:
            raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
        try:
            doc = recipes_service.read_doc(name)
        except recipes_service.PresetError as exc:
            raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
        return {
            "name": recipe.name,
            "type": recipe.type,
            "version": recipe.version,
            "title": recipe.title,
            "category": recipes_service.category_of(recipe),
            "subject": recipe.subject,
            "seed": recipe.seed,
            "doc": doc,
            "steps": [
                {
                    "index": i,
                    "label": s.label,
                    "kind": s.kind,
                    "manual": recipes_service.manual_default(s),
                    "presets": s.presets,
                    "push": s.push,
                    "overlay": s.overlay,
                    "video_preset": s.video_preset,
                    "text_from": s.text_from,
                    "run_id": s.run_id,
                    "backend": s.backend,
                    "model": s.model,
                    "duration": s.duration,
                    "resolution": s.resolution,
                    "frame_from": s.frame_from,
                    "frame_index": s.frame_index,
                    "frame_ref": s.frame_ref,
                    "reference_labels": s.reference_labels,
                    "video_ref_labels": s.video_ref_labels,
                    "video_from": s.video_from,
                }
                for i, s in enumerate(recipe.steps)
            ],
        }

    @app.post("/recipes/{name}/run")
    def api_recipe_run(name: str, req: RecipeRunRequest) -> dict[str, Any]:
        # 同步执行（auto 步里 push 可能阻塞几分钟 —— 与 /runs/{id}/video 的 ffmpeg 合成同类）
        with _db() as conn:
            try:
                return recipes_service.start_run(conn, name, seed=req.seed).model_dump()
            except recipes_service.PresetError as exc:
                raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, f"配方执行失败：{exc}") from exc

    @app.get("/recipe-runs")
    def api_recipe_runs() -> list[dict[str, Any]]:
        return recipes_service.list_recipe_runs()

    @app.get("/recipe-runs/{run_id}")
    def api_recipe_run_state(run_id: str) -> dict[str, Any]:
        try:
            return recipes_service.read_state(run_id).model_dump()
        except recipes_service.PresetError as exc:
            raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc

    @app.post("/recipe-runs/{run_id}/continue")
    def api_recipe_run_continue(run_id: str) -> dict[str, Any]:
        with _db() as conn:
            try:
                return recipes_service.advance_state(conn, run_id).model_dump()
            except recipes_service.PresetError as exc:
                raise HTTPException(getattr(exc, "status", 400), str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, f"配方继续失败：{exc}") from exc

    @app.get("/api/version")
    def api_version() -> dict[str, str]:
        return {"app": "charvar", "version": __version__}

    return app


app = build_app()
