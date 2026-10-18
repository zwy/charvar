"""HTTP API（设计文档 §7.2，默认 127.0.0.1:8710）。

build_app() 返回 FastAPI 应用：业务路由 + 可选静态管理页。
web/app.py 负责启动（uvicorn），与其他本机工具栈一致。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Literal

from contextlib import closing

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from charvar import __version__, storage
from charvar.composer import expand_matrix, generate as compose_generate
from charvar import paths
from charvar.exporters import captions as captions_exporter
from charvar.exporters import comfyui as comfyui_exporter
from charvar.exporters import jsonl as jsonl_exporter
from charvar.exporters import txt as txt_exporter
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


class ComfyQueueRequest(BaseModel):
    workload: str = Field(description="workflows 目录里的 workflow 名称，或 workflow JSON 路径")
    negative_prompt: str | None = None
    wait: bool = False
    comfy_url: str | None = None
    aspect_ratio: Literal["auto","1:1","3:4","16:9","4:3","9:16","2:3","3:2","21:9"] | None = None
    resolution: Literal["1k", "2k", "4k"] | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


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
        if not ok:
            raise HTTPException(404, f"主体档案不存在：{subject_id}")
        return {"ok": True, "deleted": subject_id}

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
        try:
            result = compose_generate(subject, preset, req)
        except (ValueError, PresetError) as exc:
            raise HTTPException(400, str(exc)) from exc
        with _db() as conn:
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

    @app.get("/api/version")
    def api_version() -> dict[str, str]:
        return {"app": "charvar", "version": __version__}

    return app


app = build_app()
