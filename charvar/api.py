"""HTTP API（设计文档 §7.2，默认 127.0.0.1:8710）。

build_app() 返回 FastAPI 应用：业务路由 + 可选静态管理页。
web/app.py 负责启动（uvicorn），与其他本机工具栈一致。
"""
from __future__ import annotations

import json
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
from charvar.assets import ensure_thumb, resolve_run_asset, sheet_path, thumb_path

from charvar.composer import expand_matrix, generate as compose_generate
from charvar.exporters import captions as captions_exporter
from charvar.exporters import comfyui as comfyui_exporter
from charvar.exporters import jsonl as jsonl_exporter
from charvar.exporters import txt as txt_exporter
from charvar.llm_compose import generate_llm
from charvar.llm_prompts import draft_subject_image_prompt_messages
from charvar.models import GenerateRequest, GenerateResult, SubjectProfile
from charvar.preset_loader import (
    ListPreset,
    MatrixPreset,
    PresetError,
    load,
    load_file,
    list_presets,
)

EXPORT_FORMATS = ("txt", "jsonl", "captions_zip", "captions")

_TEMPLATE_MAX_BYTES = 200_000


class ComfyQueueRequest(BaseModel):
    workload: str = Field(description="workflows 目录里的 workflow 名称，或 workflow JSON 路径")
    negative_prompt: str | None = None
    wait: bool = False
    comfy_url: str | None = None
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: Literal["1k", "2k", "4k"] | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    reference_kinds: list[str] = Field(default_factory=list, description="主体图参考（LLM 模式），按序注入 image/image2/…")


class SyncOutputsRequest(BaseModel):
    build_sheets: bool = True


class ContactSheetRequest(BaseModel):
    mode: Literal["contact", "face"] = "contact"


class SubjectImageGenerateRequest(BaseModel):
    prompt: str | None = None
    seed: int = 0
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: Literal["1k", "2k", "4k"] | None = None
    comfy_url: str | None = None
    wait: bool = True


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


def build_app(static_dir: Path | None = None, db_path=None) -> FastAPI:
    app = FastAPI(title="charvar 角色变体提示词生成器", version=__version__)

    def _db():
        return closing(storage.connect(db_path))

    def _preset(name: str):
        try:
            return load(name)
        except PresetError as exc:
            raise HTTPException(404, str(exc)) from exc

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
            data = llm.chat_json(messages, config=config)
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
        thumb = subject_images.thumb_file(subject_id, kind)
        if not thumb.exists():
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

    # ---- 全局设置 ----
    @app.get("/settings")
    def api_settings_get() -> dict[str, Any]:
        with _db() as conn:
            settings = settings_service.get_settings(conn)
        data = settings.model_dump()
        data["llm"]["api_key"] = ""
        data["llm_configured"] = bool(settings.llm.api_key) or bool(llm.get_llm_config().get("api_key"))
        return data

    @app.put("/settings")
    def api_settings_put(patch: dict[str, Any]) -> dict[str, Any]:
        with _db() as conn:
            settings = settings_service.save_settings(conn, patch)
        data = settings.model_dump()
        data["llm"]["api_key"] = ""
        data["llm_configured"] = bool(settings.llm.api_key) or bool(llm.get_llm_config().get("api_key"))
        return data

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
        if req.mode == "llm":
            return _generate_llm_mode(subject, req, preset)
        try:
            result = compose_generate(subject, preset, req)
        except (ValueError, PresetError) as exc:
            raise HTTPException(400, str(exc)) from exc
        with _db() as conn:
            storage.save_run(conn, result)
        return result

    def _generate_llm_mode(subject: SubjectProfile, req: GenerateRequest, preset) -> GenerateResult:
        if not req.subject_id:
            raise HTTPException(400, "LLM 模式需要 subject_id：参考图来自主体档案的图片槽位")
        kinds: list[str] = []
        for kind in req.reference_kinds:
            if kind not in subject_images.IMAGE_KINDS:
                raise HTTPException(400, f"未知参考图类型：{kind}（支持 {'/'.join(subject_images.IMAGE_KINDS)}）")
            if kind not in kinds:
                kinds.append(kind)
        if not 1 <= len(kinds) <= 4:
            raise HTTPException(400, "LLM 模式需要 1–4 个参考图类型（reference_kinds）")
        with _db() as conn:
            for kind in kinds:
                slot = subject_images.get_slot(conn, subject.id, kind, settings_service.get_settings(conn))
                if not slot.is_ready:
                    raise HTTPException(400, f"参考图未就绪：{kind}（请先在主体档案里生成或上传）")
            template = storage.get_subject_template(conn, subject.id)
            if not template:
                raise HTTPException(400, f"主体 {subject.id} 还没有上传「角色全场景通用提示词模板」")
            try:
                result = generate_llm(
                    subject,
                    preset,
                    req,
                    template,
                    kinds,
                    config=settings_service.llm_overrides(conn),
                )
            except llm.LLMError as exc:
                raise HTTPException(502, f"LLM 改写提示词失败：{exc}") from exc
            except (ValueError, PresetError) as exc:
                raise HTTPException(400, str(exc)) from exc
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

    @app.post("/runs/{run_id}/comfyui")
    def api_run_comfyui(run_id: str, req: ComfyQueueRequest) -> dict[str, Any]:
        with _db() as conn:
            result = storage.get_run(conn, run_id)
        if result is None:
            raise HTTPException(404, f"生成记录不存在：{run_id}")
        try:
            workflow, mapping = comfyui_exporter.load_workflow_pair(req.workload)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(400, f"workflow/mapping 无效：{exc}") from exc

        overrides = dict(req.overrides)
        if req.negative_prompt is not None:
            overrides["negative_prompt"] = req.negative_prompt
        if req.reference_kinds:
            # LLM 模式：把选定的主体档案图上传到 ComfyUI input，按序注入 image/image2/…
            # 按选择顺序对应 <image1>..<image4>；未选中的槽位由 mapping on_missing 从 payload 摘除
            base = paths.comfy_base_url(req.comfy_url)
            seen: list[str] = []
            for kind in req.reference_kinds:
                if kind not in subject_images.IMAGE_KINDS or kind in seen:
                    continue
                seen.append(kind)
                for suffix in (".png", ".jpg", ".jpeg", ".webp"):
                    local = subject_images.subject_dir(result.subject_id) / f"{kind}{suffix}"
                    if local.exists():
                        break
                else:
                    raise HTTPException(400, f"参考图不存在：{kind}（请先在主体档案里生成或上传）")
                try:
                    remote = comfyui_exporter.upload_image(
                        base, local, remote_name=f"{result.subject_id}_{kind}{local.suffix}"
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    raise HTTPException(502, f"上传参考图到 ComfyUI 失败：{exc}") from exc
                overrides["image" if len(seen) == 1 else f"image{len(seen)}"] = remote
        try:
            submissions = comfyui_exporter.submit(
                result,
                workflow,
                mapping,
                comfy_url=req.comfy_url,
                overrides=overrides,
                wait=req.wait,
                aspect_ratio=req.aspect_ratio,
                resolution=req.resolution,
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, f"mapping 注入失败：{exc}") from exc
        with _db() as conn:
            storage.save_comfy_submissions(conn, run_id, submissions)
        ok_count = sum(1 for s in submissions if s.get("ok"))
        return {
            "run_id": run_id,
            "comfy_url": paths.comfy_base_url(req.comfy_url),
            "workload": req.workload,
            "wait": req.wait,
            "succeeded": ok_count,
            "failed": len(submissions) - ok_count,
            "submissions": submissions,
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

    @app.get("/api/version")
    def api_version() -> dict[str, str]:
        return {"app": "charvar", "version": __version__}

    return app


app = build_app()
