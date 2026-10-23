"""Playground 视频：给一段提示词（+ 可选首帧图 / 参考图）直接出一段视频。

与出图（``charvar.adhoc``）同构：网页与 CLI ``charvar video`` 是同一套参数语义、
同一套落盘、同一份 manifest；不进主体档案 5 槽位、不跑预设批量、不落 runs 表。

这一轮只接 OpenRouter（``charvar.openrouter_video``），后续可能补 ComfyUI / 即梦。

与出图的关键差异（视频 API 是**异步作业**）：
- 提交 ``POST /api/v1/videos`` 只拿 job id，要轮询到 completed 再下载成片 ——
  所以 :func:`generate` 分两步：提交即落 manifest（status=in_progress）并返回 pending；
  成片由 :func:`refresh` 补齐（web 的 /videos/{id}/status 每次轮询调它，
  CLI 的 wait=True 在这里循环）。
- 画幅不讲像素讲档位（480p/768p/2K…），枚举按所选模型的目录条目校验，
  提交前拦截（超枚举是 400，白扣一次提交费）。
- 首帧图 = ``frame_images``（图生视频，主体一致性靠它），参考图 = ``input_references``；
  两者同给时官方语义 frame_images 优先。
- 带参考图时提示词里的 ``<imageN>`` 换序数词、不带时整段摘掉 —— 与出图同一套
  （``openrouter_image.rewrite_reference_tags`` / ``strip_reference_tags``）。

落盘：``data/generated/adhoc_video/{id}/``（original/ 里的成片 + thumbs/ 里的海报帧 +
manifest.json）。manifest 记完整请求与作业 id，便于复现与续查；产物不落 runs 表
（临时性质，图片墙不会扫到，这是预期的）。

红线（同 AGENTS.md / adhoc）：
- OpenRouter 请求一律走 ``charvar.openrouter_video`` 的客户端（``trust_env=False``，绕 shell 代理）；
- API key 与生图同链路（``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``）；
- 参考图/首帧统一走 ``charvar.reference_images`` 预处理（长边 1536 / JPEG q88），
  data URL 编码复用 ``openrouter_image.encode_reference``；
- seed 收敛 2^53-1；目录标 ``seed: false`` 的模型不发；
- 不引入随机源：seed 一律由调用方显式给定。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from charvar import openrouter_video, paths, reference_images, settings_service
from charvar.openrouter_video import OpenRouterVideoError as _ORError

Backend = Literal["openrouter"]

# 参考图上限与生图 / openrouter_video 一致
_MAX_REFERENCES = openrouter_video._MAX_REFERENCES


class AdhocVideoError(ValueError):
    """参数或环境错误（API 层转 400，CLI 层转友好提示）。"""


class AdhocVideoTimeout(AdhocVideoError):
    """轮询超时（作业还在跑；manifest 保持 in_progress，refresh 可续）。"""


class VideoRequest(BaseModel):
    """一次 Playground 视频生成的请求。"""

    prompt: str
    backend: Backend = "openrouter"
    model: str | None = None  # OpenRouter slug；空 = 设置页/env 默认
    duration: int | None = None  # 秒；None = 不发，跟随 provider 默认
    resolution: str | None = None  # 480p/768p/2K…；None = 不发
    aspect_ratio: str = "9:16"
    seed: int = 0
    first_frame: Path | None = None  # 首帧图（图生视频，单张）
    reference_paths: list[Path] = Field(default_factory=list)  # 参考图 ≤4
    wait: bool = True  # CLI 用（阻塞到成品）；web 恒传 wait=False 走轮询
    staged_ref_ids: list[str] = Field(default_factory=list)  # web 暂存参考图 id，ready 时清理
    out: Path | None = None

    def request_model(self) -> str:
        """请求里给的模型（空串/None = None，让上层走设置页链路）。"""
        return (self.model or "").strip() or None


class VideoResult(BaseModel):
    """一次 Playground 视频生成的结果。"""

    run_id: str
    video_path: str = ""
    poster_path: str = ""
    provider: Backend
    manifest_path: str = ""
    job_id: str = ""
    status: Literal["pending", "in_progress", "ready", "failed"] = "pending"
    seed: int = 0
    model: str | None = None
    duration: int | None = None  # 请求的时长（秒）；None = provider 自选
    resolution: str | None = None
    aspect_ratio: str = ""
    cost: float | None = None
    duration_ms: int = 0  # 提交到成品的墙钟时间


# ---------- 落盘目录 ----------


def video_root() -> Path:
    """Playground 视频根目录：``<data>/generated/adhoc_video/``。"""
    return paths.data_dir() / "generated" / "adhoc_video"


def refs_dir() -> Path:
    """网页上传的参考图暂存目录（``_refs/``）—— 成了片就没用了，可随时清。"""
    return video_root() / "_refs"


def _new_run_id() -> str:
    """本次生成的 id：只含 ``[A-Za-z0-9_.-]``（assets.safe_run_id 的字符集约束）。"""
    return f"video_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"


# run_id 必须是「video_ 前缀 + 安全字符集」。与 adhoc 同款：run_id 直接拼进路径，
# assets.safe_run_id 的正则放行 ".."，这里必须自己拦。
_RUN_ID_RE = re.compile(r"^video_[A-Za-z0-9_.-]+$")


def _run_dir(run_id: str) -> Path:
    return video_root() / run_id


def run_dir(run_id: str) -> Path:
    """公开的 run 目录（对外服务产物用；非法 id 直接报错，不拼路径）。"""
    if not _RUN_ID_RE.fullmatch(str(run_id)) or ".." in str(run_id):
        raise AdhocVideoError(f"非法 run_id：{run_id}")
    return _run_dir(run_id)


def video_file(run_id: str) -> Path:
    """某次生成的成片（``original/`` 下唯一一个文件）。"""
    directory = run_dir(run_id) / "original"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有视频产物：{run_id}")


def poster_file(run_id: str) -> Path:
    """某次生成的海报帧（``thumbs/`` 下唯一一个文件）。"""
    directory = run_dir(run_id) / "thumbs"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有海报帧：{run_id}")


# ---------- 暂存参考图（网页上传用，与 adhoc 同款四件套） ----------

_STAGED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def save_staged_reference(data: bytes, filename: str = "") -> Path:
    """把上传的图存到暂存目录，返回路径。

    后缀按**实际图片格式**判（用 PIL 读），不信任上传时的文件名。
    """
    import io

    from PIL import Image

    if not data:
        raise AdhocVideoError("参考图是空的")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or "").upper()
    except Exception as exc:  # noqa: BLE001
        raise AdhocVideoError(f"参考图不是可识别的图片：{exc}") from exc
    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(fmt)
    if suffix is None:
        raise AdhocVideoError(f"不支持的参考图格式：{fmt or '未知'}")
    target = refs_dir() / f"{uuid.uuid4().hex}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def staged_reference(ref_id: str) -> Path:
    """按 id 取暂存图路径（非法 id 报错）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", str(ref_id or "")):
        raise AdhocVideoError(f"非法参考图 id：{ref_id}")
    matches = sorted(refs_dir().glob(f"{ref_id}.*"))
    for path in matches:
        if path.suffix.lower() in _STAGED_SUFFIXES and path.is_file():
            return path
    raise AdhocVideoError(f"参考图不存在或已清理：{ref_id}")


def clear_staged_references(ref_ids: list[str]) -> None:
    """删掉用完的暂存图（作业 ready 时调用，失败时保留以便重试）。"""
    for ref_id in ref_ids or []:
        try:
            staged_reference(ref_id).unlink(missing_ok=True)
        except AdhocVideoError:
            continue


def prune_staged_references(max_age_s: float = 24 * 3600) -> int:
    """清掉过期未用的暂存图，返回删掉几个（用户传了图没点生成就走了的兜底）。"""
    directory = refs_dir()
    if not directory.is_dir():
        return 0
    now = time.time()
    removed = 0
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime < max_age_s:
                continue
        except OSError:
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed


# ---------- 记录列表与 manifest ----------


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    """最近的 Playground 视频记录（读 manifest.json，按时间倒序）。

    manifest 是唯一留痕（不落 runs 表），所以这里就是扫目录。
    """
    root = video_root()
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name.startswith("_"):
            continue
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        request = manifest.get("request") or {}
        status = str(manifest.get("status") or "")
        has_video = False
        if status == "ready":
            original = directory / "original"
            has_video = original.is_dir() and any(original.iterdir())
        records.append(
            {
                "run_id": directory.name,
                "created_at": manifest.get("created_at") or "",
                "backend": request.get("backend") or "openrouter",
                "prompt": request.get("prompt") or "",
                "model": request.get("model") or "",
                "duration": request.get("duration"),
                "resolution": request.get("resolution"),
                "aspect_ratio": request.get("aspect_ratio") or "",
                "seed": request.get("seed"),
                "status": status or "failed",
                "error": manifest.get("error") or "",
                # 只提交（未终态）与失败的记录没有成片 → 前端别去取（否则 404）
                "has_video": has_video,
            }
        )
    records.sort(key=lambda r: r["run_id"], reverse=True)
    return records[: max(1, int(limit))]


def read_manifest(run_id: str) -> dict[str, Any]:
    """读某个 run 的 manifest（web status 端点与刷新逻辑共用）。"""
    path = run_dir(run_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"没有 manifest：{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_manifest(req: VideoRequest, run_id: str, *, job_id: str = "", model: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": {
            "backend": req.backend,
            "prompt": req.prompt,
            "model": req.model,
            "duration": req.duration,
            "resolution": req.resolution,
            "aspect_ratio": req.aspect_ratio,
            "seed": req.seed,
            "first_frame": str(req.first_frame or ""),
            "reference_paths": [str(p) for p in req.reference_paths],
            "staged_ref_ids": list(req.staged_ref_ids),
            "out": str(req.out or ""),
        },
        "job": {"id": job_id, "status": "pending", "model": model},
        "status": "pending",
        "result": None,
        "error": "",
    }


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """web status 端点用的精简摘要（URL 由 api 层拼，这里只给数据）。"""
    result = manifest.get("result") or {}
    request = manifest.get("request") or {}
    job = manifest.get("job") or {}
    return {
        "run_id": manifest.get("run_id") or "",
        "status": manifest.get("status") or "failed",
        "error": manifest.get("error") or "",
        "job_id": job.get("id") or "",
        "model": job.get("model") or request.get("model") or "",
        "prompt": request.get("prompt") or "",
        "duration": result.get("duration") if result else request.get("duration"),
        "resolution": result.get("resolution") if result else request.get("resolution"),
        "aspect_ratio": result.get("aspect_ratio") if result else request.get("aspect_ratio"),
        "seed": result.get("seed") if result else request.get("seed"),
        "cost": result.get("cost"),
        "duration_ms": result.get("duration_ms") or 0,
    }


# ---------- 校验与预处理 ----------


def validate(req: VideoRequest) -> None:
    """请求自检：能在发请求前发现的错就别浪费一次提交（heygen 一提交就计费）。"""
    if not req.prompt.strip():
        raise AdhocVideoError("提示词为空")
    if req.duration is not None:
        # pydantic 会把 JSON 里的 5.5 解出来拒绝，但把 bool 收成 0/1 —— bool 明确拦
        if isinstance(req.duration, bool) or not isinstance(req.duration, int) or req.duration <= 0:
            raise AdhocVideoError(f"时长必须是正整数秒，当前：{req.duration!r}")
    if len(req.reference_paths) > _MAX_REFERENCES:
        raise AdhocVideoError(f"参考图最多 {_MAX_REFERENCES} 张，收到 {len(req.reference_paths)} 张")
    for path in req.reference_paths:
        if not Path(path).exists():
            raise AdhocVideoError(f"参考图不存在：{path}")
    if req.first_frame is not None and not Path(req.first_frame).exists():
        raise AdhocVideoError(f"首帧图不存在：{req.first_frame}")


def _prepare_images(req: VideoRequest, run_dir: Path) -> tuple[Path | None, list[Path]]:
    """首帧 + 参考图统一预处理（长边 1536 / JPEG q88 / 透明白底合成）。

    走 ``charvar.reference_images`` —— 不写新的压缩逻辑；编码成 data URL 在
    ``openrouter_video.build_body`` 里走 ``openrouter_image.encode_reference``。
    """
    cache_dir = run_dir / "_ref_cache"
    first_frame: Path | None = None
    if req.first_frame is not None:
        first_frame = reference_images.prepare_reference(
            Path(req.first_frame), cache_dir / "frame0.jpg"
        )
    prepared: list[Path] = []
    for index, source in enumerate(req.reference_paths):
        prepared.append(
            reference_images.prepare_reference(Path(source), cache_dir / f"ref{index}.jpg")
        )
    return first_frame, prepared


def _rewrite_prompt(prompt: str, has_images: bool) -> str:
    """``<imageN>`` 处理与出图同一套：有图换序数词，无图整段摘掉。"""
    from charvar import openrouter_image

    text = prompt.strip()
    if has_images:
        return openrouter_image.rewrite_reference_tags(text)
    return openrouter_image.strip_reference_tags(text)


def _resolve_model(req: VideoRequest, db_path: Path | None = None) -> str:
    """模型默认链：请求显式 > 设置页（DB） > env（OP_VIDEO_MODEL） > heygen 默认。"""
    with _conn(db_path) as conn:
        cfg = settings_service.openrouter_video_config(conn)
    return (req.request_model() or "").strip() or str(cfg["model"]).strip()


def _conn(db_path: Path | None = None):
    from charvar import storage

    return storage.connect(db_path)


# ---------- 海报帧与 --out ----------


def _make_poster(video_path: Path, run_dir: Path) -> Path | None:
    """ffmpeg 抽第一帧当海报（失败不算致命，海报只是列表里好看）。"""
    try:
        from charvar.video import _ffmpeg_bin
    except Exception:  # noqa: BLE001
        return None
    destination = run_dir / "thumbs" / "000000.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path), "-frames:v", "1", "-vf", "scale=540:-2",
        str(destination),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except Exception:  # noqa: BLE001 - 海报抽不出来别影响成片
        return None
    return destination


def _write_out(result: VideoResult, out: Path | None) -> None:
    """``--out`` 指定路径时再落一份出去（成片本体不动，只是复制）。"""
    if out is None or not result.video_path:
        return
    import shutil

    source = Path(result.video_path)
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    result.video_path = str(destination)


def _mark(run_dir: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """小工具：就地改 manifest 若干键并落盘（终态/状态推进共用）。"""
    patch.setdefault("finalized_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    manifest.update(patch)
    _write_manifest(run_dir, manifest)
    return manifest


# ---------- 提交与收尾 ----------


def _submit(req: VideoRequest, run_id: str, db_path: Path | None) -> dict[str, Any]:
    """提交作业；返回带 job 信息的 manifest（status=in_progress）。"""
    run_dir = _run_dir(run_id)
    model = _resolve_model(req, db_path)
    api_key = openrouter_video.resolve_api_key()
    if not api_key:
        raise AdhocVideoError(
            "缺少 OpenRouter API Key：请设置 OP_IMAGE_API_KEY / LLM_API_KEY，或在「设置」页配置"
        )
    first_frame, refs = _prepare_images(req, run_dir)
    has_images = first_frame is not None or bool(refs)
    prompt = _rewrite_prompt(req.prompt, has_images)
    try:
        body = openrouter_video.build_body(
            prompt=prompt,
            model=model,
            duration=req.duration,
            resolution=req.resolution,
            aspect_ratio=req.aspect_ratio,
            seed=req.seed,
            first_frame_path=first_frame,
            reference_paths=refs,
        )
        job = openrouter_video.submit(body, api_key=api_key)
    except _ORError as exc:
        raise AdhocVideoError(f"OpenRouter 视频提交失败：{exc}") from exc

    manifest = _new_manifest(req, run_id, job_id=job.job_id, model=model)
    manifest["status"] = "in_progress"
    _write_manifest(run_dir, manifest)
    return manifest


def _finalize(
    run_id: str, manifest: dict[str, Any], payload: dict[str, Any], submitted_at: float
) -> dict[str, Any]:
    """拿终态 payload 收尾：completed 下载成片 + 海报 + 清暂存；failed 记错误。"""
    run_dir = _run_dir(run_id)
    status = str(payload.get("status") or "")
    if status == "completed":
        job_id = (manifest.get("job") or {}).get("id") or ""
        unsigned = payload.get("unsigned_urls")
        url = unsigned[0] if isinstance(unsigned, list) and unsigned else None
        data = openrouter_video.download(job_id, url=url)
        ext = openrouter_video.sniff_video_ext(data)
        if ext == "bin":
            raise AdhocVideoError("OpenRouter 返回的不是可识别的视频格式")
        destination = run_dir / "original" / f"000000.{ext}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        poster = _make_poster(destination, run_dir)
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        cost = usage.get("cost")
        job = manifest.get("job") or {}
        request = manifest.get("request") or {}
        result = VideoResult(
            run_id=run_id,
            video_path=str(destination),
            poster_path=str(poster) if poster else "",
            provider="openrouter",
            manifest_path="",
            job_id=job_id,
            status="ready",
            seed=request.get("seed") or 0,
            model=job.get("model") or None,
            duration=request.get("duration"),
            resolution=request.get("resolution"),
            aspect_ratio=request.get("aspect_ratio") or "",
            cost=float(cost) if isinstance(cost, (int, float)) else None,
            duration_ms=int((time.time() - submitted_at) * 1000),
        )
        out = str(request.get("out") or "")
        _write_out(result, Path(out) if out else None)
        result.manifest_path = str(run_dir / "manifest.json")
        manifest["result"] = result.model_dump()
        manifest = _mark(run_dir, manifest, status="ready", error="")
        # 暂存参考图用完了清掉（首帧也可能来自暂存）
        clear_staged_references(list(request.get("staged_ref_ids") or []))
    else:
        error = payload.get("error")
        error = str(error) if error else "OpenRouter 视频作业失败"
        manifest = _mark(run_dir, manifest, status="failed", error=error)
    return manifest


def refresh(run_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """查一次作业并把成片收下来（web /videos/{id}/status 的引擎，幂等）。

    - 终态（ready/failed）或没有 job 的记录：直接读 manifest 返回摘要，不再打 OpenRouter；
    - 其余：轮询一次；completed → 下载成片，failed → 记错误；still running → 更新状态。
    """
    manifest = read_manifest(run_id)
    status = str(manifest.get("status") or "")
    job = manifest.get("job") or {}
    job_id = str(job.get("id") or "")
    if status in ("ready", "failed") or not job_id:
        if not status:  # 脏数据兜底：有 job 没 status
            manifest = _mark(_run_dir(run_id), manifest, status="failed",
                             error=manifest.get("error") or "提交失败")
        return _summary(manifest)
    try:
        payload = openrouter_video.poll(job_id)
    except _ORError as exc:
        # 网络抖动不该打断前端的轮询节奏：返回当前摘要并带上 poll_error（状态不变）
        summary = _summary(manifest)
        summary["poll_error"] = str(exc)
        return summary
    status_now = str(payload.get("status") or "")
    job["status"] = status_now or job.get("status") or ""
    if status_now in ("completed", "failed"):
        submitted = float(manifest.get("submitted_at") or time.time())
        try:
            manifest = _finalize(run_id, manifest, payload, submitted)
        except (AdhocVideoError, _ORError) as exc:
            message = str(exc)
            if isinstance(exc, _ORError):
                message = f"OpenRouter 视频下载失败：{message}"
            manifest = _mark(_run_dir(run_id), manifest, status="failed", error=message)
    elif status_now in ("pending", "in_progress"):
        manifest["status"] = status_now
        _write_manifest(_run_dir(run_id), manifest)
    return _summary(manifest)


# ---------- 对外入口 ----------


def generate(
    req: VideoRequest,
    db_path: Path | None = None,
    *,
    poll_interval: float = 5.0,
    timeout_s: float = 900.0,
) -> VideoResult:
    """跑一次 Playground 视频生成，返回结果（含落盘路径与 manifest 路径）。

    ``wait=True``（CLI 默认）：阻塞轮询到终态（completed → 成片；failed → 抛错）；
    ``wait=False``（web）：提交即返回 pending，后续由 :func:`refresh` 收片。
    失败也留痕：manifest 记下失败原因方便回头查；超时不改状态（作业还在跑，
    refresh 可续）。
    """
    validate(req)
    run_id = _new_run_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    submitted_at = time.time()

    try:
        manifest = _submit(req, run_id, db_path)
    except Exception as exc:  # noqa: BLE001 - 提交失败也要留痕（历史列表里能看见失败原因）
        manifest = _new_manifest(req, run_id)
        manifest = _mark(run_dir, manifest, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    manifest["submitted_at"] = submitted_at
    _write_manifest(run_dir, manifest)
    job_id = (manifest.get("job") or {}).get("id") or ""

    def pending() -> VideoResult:
        return VideoResult(
            run_id=run_id,
            provider="openrouter",
            manifest_path=str(run_dir / "manifest.json"),
            job_id=job_id,
            status="pending",
            seed=req.seed,
            model=(manifest.get("job") or {}).get("model"),
            duration=req.duration,
            resolution=req.resolution,
            aspect_ratio=req.aspect_ratio,
        )

    if not req.wait:
        return pending()

    try:
        payload = openrouter_video.wait_for_job(
            job_id,
            poll_interval=poll_interval,
            timeout_s=timeout_s,
            timeout_error=AdhocVideoTimeout,
        )
        _finalize(run_id, manifest, payload, submitted_at)
    except AdhocVideoTimeout:
        # 作业还在跑：状态保持 in_progress，refresh/CLI 再查可以续
        manifest["submitted_at"] = submitted_at
        _write_manifest(run_dir, manifest)
        raise
    except (AdhocVideoError, _ORError) as exc:
        message = str(exc)
        if isinstance(exc, _ORError):
            message = f"OpenRouter 视频下载失败：{message}"
        _mark(run_dir, manifest, status="failed", error=message)
        raise AdhocVideoError(message) from exc

    final = read_manifest(run_id)
    if final.get("status") == "failed":
        raise AdhocVideoError(str(final.get("error") or "OpenRouter 视频作业失败"))
    return VideoResult(**(final.get("result") or {}))