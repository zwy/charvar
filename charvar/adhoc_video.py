"""Playground 视频：给一段提示词（+ 可选首帧图 / 参考图）直接出一段视频。

与出图（``charvar.adhoc``）同构：网页与 CLI ``charvar video`` 是同一套参数语义、
同一套落盘、同一份 manifest；不进主体档案 5 槽位、不跑预设批量、不落 runs 表。

供应商两路，按 ``backend`` 分派（manifest 层已经供应商无关）：
- ``openrouter``：在线 API 异步作业（``charvar.openrouter_video``）；
- ``dreamina``：即梦官方 CLI 子进程（``charvar.dreamina_video``），同一套
  submit → 轮询 → 下载语义，作业 id 是即梦 ``submit_id``。

与出图的关键差异（视频 API 是**异步作业**）：
- 提交只拿作业 id（OpenRouter job id / 即梦 submit_id），要轮询到终态再下载
  成片 —— 所以 :func:`generate` 分两步：提交即落 manifest（status=in_progress）
  并返回 pending；成片由 :func:`refresh` 补齐（web 的 /videos/{id}/status 每次
  轮询调它，CLI 的 wait=True 在这里循环）。
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
- OpenRouter 的 API key 与生图同链路（``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``）；
- 即梦通路无鉴权配置（本机 ``dreamina login`` 登录态），**无 seed 参数**：请求
  里的 seed 只记 manifest，绝不发送（``dreamina_video`` 负责枚举与组合校验）；
- 参考图/首帧统一走 ``charvar.reference_images`` 预处理（长边 1536 / JPEG q88），
  data URL 编码复用 ``openrouter_image.encode_reference``；
- OpenRouter 侧 seed 收敛 2^53-1；目录标 ``seed: false`` 的模型不发；
- 不引入随机源：seed 一律由调用方显式给定。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from charvar import dreamina_video, openrouter_video, paths, reference_images, settings_service
from charvar.openrouter_video import OpenRouterVideoError as _ORError

Backend = Literal["openrouter", "dreamina"]

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
    last_frame: Path | None = None  # 尾帧图（first+last 模型；首尾成对或单独给）
    reference_paths: list[Path] = Field(default_factory=list)  # 参考图 ≤4
    reference_video_paths: list[Path] = Field(default_factory=list)  # 参考视频 ≤2（Seedance gen2+）
    wait: bool = True  # CLI 用（阻塞到成品）；web 恒传 wait=False 走轮询
    staged_ref_ids: list[str] = Field(default_factory=list)  # web 暂存参考图 id，ready 时清理
    staged_video_ref_ids: list[str] = Field(default_factory=list)  # web 暂存参考视频 id，ready 时清理
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

# 参考视频暂存（同款四件套的一个小变体）：实际容器按魔数判；上限 48MB
# （base64 后 ~64MB 的请求体，别把 web 请求打爆）
_VIDEO_MAX_BYTES = 48 * 1024 * 1024
_VIDEO_REF_CAP = 2


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


# ---------- 暂存参考视频（网页上传；图池之外独立目录，互不误删） ----------


def video_refs_dir() -> Path:
    """暂存参考视频目录：``<adhoc_video>/_vrefs/``。"""
    return video_root() / "_vrefs"


def save_staged_video_reference(data: bytes, filename: str = "") -> Path:
    """把上传的视频存进暂存目录，按魔数判后缀（mp4/mov/mpeg/webm 之外直接拒）。"""
    if not data:
        raise AdhocVideoError("参考视频是空的")
    if len(data) > _VIDEO_MAX_BYTES:
        raise AdhocVideoError(
            f"参考视频超过 {_VIDEO_MAX_BYTES // (1024 * 1024)}MB 上限（收到 {len(data) // (1024 * 1024)}MB）"
        )
    ext = openrouter_video.sniff_video_ext(data)
    if ext == "bin":
        raise AdhocVideoError("参考视频不是可识别的容器（mp4/mov/mpeg/webm）")
    target = video_refs_dir() / f"{uuid.uuid4().hex}{'.mov' if ext == 'mov' else '.' + ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def staged_video_reference(ref_id: str) -> Path:
    """按 id 取暂存视频路径（非法 id 报错）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", str(ref_id or "")):
        raise AdhocVideoError(f"非法参考视频 id：{ref_id}")
    matches = sorted(video_refs_dir().glob(f"{ref_id}.*"))
    for path in matches:
        if path.suffix.lower() in (".mp4", ".mov", ".mpeg", ".mpg", ".webm") and path.is_file():
            return path
    raise AdhocVideoError(f"参考视频不存在或已清理：{ref_id}")


def clear_staged_video_references(ref_ids: list[str]) -> None:
    """删掉用完的暂存视频（成片 ready 时调用，失败时保留以便重试）。"""
    for ref_id in ref_ids or []:
        try:
            staged_video_reference(ref_id).unlink(missing_ok=True)
        except AdhocVideoError:
            continue


def prune_staged_video_references(max_age_s: float = 24 * 3600) -> int:
    """清掉过期未用的暂存视频，返回删掉几个。"""
    directory = video_refs_dir()
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


def _recent_run_names() -> list[str]:
    """manifest 留痕的 run 目录名，时间倒序（run_id 自带时间前缀，倒序即时间倒序）。"""
    root = video_root()
    if not root.is_dir():
        return []
    names = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "manifest.json").exists()
    )
    names.reverse()
    return names


def count_recent() -> int:
    """manifest 留痕的 run 总数（「最近生成」翻页用）。"""
    return len(_recent_run_names())


def list_recent(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """最近的 Playground 视频记录（读 manifest.json，按时间倒序）。

    manifest 是唯一留痕（不落 runs 表），所以这里就是扫目录；
    offset 是翻页起点（run_id 自带时间前缀，倒序即时间倒序），只对窗口内的目录读 manifest。
    """
    root = video_root()
    if not root.is_dir():
        return []
    start = max(0, int(offset))
    window = _recent_run_names()[start : start + max(1, int(limit))]
    records: list[dict[str, Any]] = []
    for name in window:
        directory = root / name
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
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
                # 即梦的解析结果（model/resolution/duration 缺省值）落在 job 上：列表显示生效值
                "model": request.get("model") or (manifest.get("job") or {}).get("model") or "",
                "duration": request.get("duration") if request.get("duration") is not None
                else (manifest.get("job") or {}).get("duration"),
                "resolution": request.get("resolution") or (manifest.get("job") or {}).get("resolution") or None,
                "aspect_ratio": request.get("aspect_ratio") or "",
                "seed": request.get("seed"),
                "status": status or "failed",
                "error": manifest.get("error") or "",
                # 只提交（未终态）与失败的记录没有成片 → 前端别去取（否则 404）
                "has_video": has_video,
                # AI 文案（有 = 卡片直接弹层展示旧的，不重新生成）
                "copy": manifest.get("copy") or None,
            }
        )
    return records


def read_manifest(run_id: str) -> dict[str, Any]:
    """读某个 run 的 manifest（web status 端点与刷新逻辑共用）。"""
    path = run_dir(run_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"没有 manifest：{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_copy(run_id: str, copy: dict[str, Any]) -> dict[str, Any]:
    """把生成的文案写进 manifest["copy"]（带时间戳），返回写进去的内容。"""
    directory = run_dir(run_id)
    manifest = read_manifest(run_id)
    stored = {**copy, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    manifest["copy"] = stored
    _write_manifest(directory, manifest)
    return stored


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
            "last_frame": str(req.last_frame or ""),
            "reference_paths": [str(p) for p in req.reference_paths],
            "reference_video_paths": [str(p) for p in req.reference_video_paths],
            "staged_ref_ids": list(req.staged_ref_ids),
            "staged_video_ref_ids": list(req.staged_video_ref_ids),
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
        "job_status": job.get("status") or "",
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
    if len(req.reference_video_paths) > _VIDEO_REF_CAP:
        raise AdhocVideoError(f"参考视频最多 {_VIDEO_REF_CAP} 条，收到 {len(req.reference_video_paths)} 条")
    for path in req.reference_video_paths:
        # 存在性 + 容器魔数都校验：把图片或别的文件当视频发出去只会烧一次提交费
        if not Path(path).exists():
            raise AdhocVideoError(f"参考视频不存在：{path}")
        try:
            openrouter_video.sniff_video_ref(Path(path))
        except _ORError as exc:
            raise AdhocVideoError(str(exc)) from exc
    if req.first_frame is not None and not Path(req.first_frame).exists():
        raise AdhocVideoError(f"首帧图不存在：{req.first_frame}")
    if req.last_frame is not None and not Path(req.last_frame).exists():
        raise AdhocVideoError(f"尾帧图不存在：{req.last_frame}")


def _prepare_images(req: VideoRequest, run_dir: Path) -> tuple[Path | None, Path | None, list[Path]]:
    """首帧 + 尾帧 + 参考图统一预处理（长边 1536 / JPEG q88 / 透明白底合成）。

    走 ``charvar.reference_images`` —— 不写新的压缩逻辑；编码成 data URL 在
    ``openrouter_video.build_body`` 里走 ``openrouter_image.encode_reference``。
    """
    cache_dir = run_dir / "_ref_cache"
    first_frame: Path | None = None
    if req.first_frame is not None:
        first_frame = reference_images.prepare_reference(
            Path(req.first_frame), cache_dir / "frame0.jpg"
        )
    last_frame: Path | None = None
    if req.last_frame is not None:
        last_frame = reference_images.prepare_reference(
            Path(req.last_frame), cache_dir / "frame_last.jpg"
        )
    prepared: list[Path] = []
    for index, source in enumerate(req.reference_paths):
        prepared.append(
            reference_images.prepare_reference(Path(source), cache_dir / f"ref{index}.jpg")
        )
    return first_frame, last_frame, prepared


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


def _mark_if_open(run_dir: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """终态写入保护：写前重读盘上 manifest，已是终态（ready/failed）就不覆写。

    CLI 与 web（或两个页签）会同时盯同一个作业、各自轮询各自收片：本路失败时
    另一路可能刚把 run 收成 ready —— 用本进程的旧 dict 全量覆写会把 ready 抹成
    failed 且永久卡死（实测冒烟踩过）。返回值以盘上为准（已终态返回盘上版本）。
    """
    try:
        disk = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        disk = None
    if isinstance(disk, dict) and disk.get("status") in ("ready", "failed"):
        return disk
    return _mark(run_dir, manifest, **patch)


# ---------- 提交与收尾 ----------


def _submit(req: VideoRequest, run_id: str, db_path: Path | None) -> dict[str, Any]:
    """提交作业；返回带 job 信息的 manifest（status=in_progress）。"""
    if req.backend == "dreamina":
        return _submit_dreamina(req, run_id, db_path)
    run_dir = _run_dir(run_id)
    model = _resolve_model(req, db_path)
    with _conn(db_path) as conn:
        creds = settings_service.openrouter_credentials(conn)
    api_key, base_url = creds["api_key"], creds["base_url"]
    if not api_key:
        raise AdhocVideoError(
            "缺少 OpenRouter API Key：请设置 OP_API_KEY，或在「设置」页 / 旧环境变量 OP_IMAGE_API_KEY、LLM_API_KEY 配"
        )
    first_frame, last_frame, refs = _prepare_images(req, run_dir)
    # 参考视频不预处理（无压缩逻辑可用；原样 data URL 发），但先把魔数验一遍
    for source in req.reference_video_paths:
        try:
            openrouter_video.sniff_video_ref(Path(source))
        except _ORError as exc:
            raise AdhocVideoError(str(exc)) from exc
    has_images = first_frame is not None or bool(refs) or last_frame is not None
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
            last_frame_path=last_frame,
            reference_paths=refs,
            reference_videos=list(req.reference_video_paths),
        )
        job = openrouter_video.submit(body, api_key=api_key, base_url=base_url)
    except _ORError as exc:
        raise AdhocVideoError(f"OpenRouter 视频提交失败：{exc}") from exc

    manifest = _new_manifest(req, run_id, job_id=job.job_id, model=model)
    manifest["status"] = "in_progress"
    _write_manifest(run_dir, manifest)
    return manifest


def _submit_dreamina(req: VideoRequest, run_id: str, db_path: Path | None) -> dict[str, Any]:
    """即梦分支的提交：本地路径直接交给 dreamina CLI 子进程（submit_id 落 job.id）。

    与 OpenRouter 分支的差异：
    - 没有 API key 与目录校验 —— 枚举校验在 ``dreamina_video.submit`` 的静态表里
      （``--video_resolution`` 是必填参数，没有「不发」这个路径）；
    - 模型默认链：请求显式 > 设置页「即梦视频」组（DB） > env
      ``DREAMINA_VIDEO_MODEL`` > ``seedance2.0fast_vip``；
    - 时长/分辨率缺省：5s / 720p（即梦必填，没有「跟随 provider 不发」）；
    - 画幅：请求显式发；image2video 的空值 = 从首帧图推断（即梦原生行为）；
    - ``--poll=0``：子进程只提交不等待，web 提交即返回，CLI 的等待在本模块循环；
    - 提交被拒（含首用合规确认）当场抛，generate 的失败留痕逻辑收尾。
    """
    run_dir = _run_dir(run_id)
    with _conn(db_path) as conn:
        cfg = settings_service.dreamina_video_config(conn)
    model = (req.request_model() or "").strip() or str(cfg["model"]).strip()
    resolution = dreamina_video.resolve_resolution(req.resolution)
    duration = dreamina_video.resolve_duration(req.duration)
    # 首帧/参考图统一走预处理（与 OpenRouter 同一份缓存）；参考视频原样本地路径直传
    first_frame, last_frame, refs = _prepare_images(req, run_dir)
    has_images = first_frame is not None or bool(refs) or last_frame is not None
    prompt = _rewrite_prompt(req.prompt, has_images)
    try:
        task = dreamina_video.submit(
            prompt=prompt,
            model=model,
            resolution=resolution,
            duration=duration,
            ratio=req.aspect_ratio or None,
            first_frame=first_frame,
            last_frame=last_frame,
            reference_paths=refs,
            reference_video_paths=list(req.reference_video_paths),
            poll_s=0,
        )
    except dreamina_video.DreaminaVideoError as exc:
        raise AdhocVideoError(f"即梦视频提交失败：{exc}") from exc
    submit_id = dreamina_video.submit_id_of(task)
    manifest = _new_manifest(req, run_id, job_id=submit_id, model=model)
    manifest["status"] = "in_progress"
    manifest["job"]["status"] = str(task.get("gen_status") or "")
    # 即梦必填参数的解析结果（result 展示层用：请求可能给的是 None = 缺省）
    manifest["job"]["resolution"] = resolution
    manifest["job"]["duration"] = duration
    _write_manifest(run_dir, manifest)
    return manifest


def _finalize(
    run_id: str,
    manifest: dict[str, Any],
    payload: dict[str, Any],
    submitted_at: float,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """拿终态 payload 收尾：completed 下载成片 + 海报 + 清暂存；failed 记错误。

    按供应商分流：OpenRouter 的终态 payload 就是 poll 的响应（带 unsigned_urls /
    usage.cost）；即梦的 payload 只带 gen_status，成片要再用 ``query_result
    --download_dir`` 单独下。
    """
    backend = str((manifest.get("request") or {}).get("backend") or "openrouter")
    if backend == "dreamina":
        return _finalize_dreamina(run_id, manifest, submitted_at)
    run_dir = _run_dir(run_id)
    status = str(payload.get("status") or "")
    if status == "completed":
        job_id = (manifest.get("job") or {}).get("id") or ""
        unsigned = payload.get("unsigned_urls")
        url = unsigned[0] if isinstance(unsigned, list) and unsigned else None
        data = openrouter_video.download(job_id, url=url, api_key=api_key, base_url=base_url)
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
        # 实测价缓存：usage.cost 回写每秒实收率（估算的实测优先层；写失败不挡成片）
        if isinstance(cost, (int, float)) and float(cost) > 0:
            try:
                openrouter_video.record_observed_cost(
                    str(job.get("model") or ""),
                    resolution=request.get("resolution"),
                    n_images=(1 if request.get("first_frame") else 0)
                    + len(request.get("reference_paths") or []),
                    seconds=float(request.get("duration") or 0),
                    cost=float(cost),
                )
            except Exception:  # noqa: BLE001 - 旁路数据，不挡成片；warning 留痕便于发现接线错误
                import logging

                logging.getLogger("charvar.adhoc_video").warning(
                    "实测价缓存回写失败（video run %s）", run_id, exc_info=True
                )
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
        # 暂存参考图/视频用完了清掉（首帧也可能来自暂存）
        clear_staged_references(list(request.get("staged_ref_ids") or []))
        clear_staged_video_references(list(request.get("staged_video_ref_ids") or []))
    else:
        error = payload.get("error")
        error = str(error) if error else "OpenRouter 视频作业失败"
        manifest = _mark(run_dir, manifest, status="failed", error=error)
    return manifest


def _finalize_dreamina(
    run_id: str, manifest: dict[str, Any], submitted_at: float
) -> dict[str, Any]:
    """即梦分支收尾：query_result --download_dir 落盘成片 → 转存 original/ + 海报。

    下载是终态后的**另一次** ``query_result``（产物 URL 会过期，成功后立刻要）。
    并发兜底：CLI 与 web 可能同时盯同一个作业、各自轮询各自收片 ——
    ``original/`` 里已有成片就直接复用，不再重复收（实测冒烟踩过：
    一路刚 rmtree 掉 download 目录，另一路的产物校验就报「文件不存在」）。
    失败/超时按现有策略转 failed 留痕（重试 = 重新生成）；失败路径不清暂存。
    """
    run_dir = _run_dir(run_id)
    job = manifest.get("job") or {}
    request = manifest.get("request") or {}
    submit_id = str(job.get("id") or "")
    if not submit_id:
        return _mark(run_dir, manifest, status="failed", error="即梦作业缺 submit_id，无法收片")

    original_dir = run_dir / "original"

    def existing_video():
        if not original_dir.is_dir():
            return None
        for path in sorted(original_dir.iterdir()):
            if path.is_file():
                return path
        return None

    source = existing_video()
    if source is not None:
        # 并发兜底：另一路已收好片，直接复用
        pass
    else:
        # 每路收片用独立 download 目录：CLI 按确定性文件名下载，共享目录会让
        # 并发两次下载互相截断半成品（实测踩过）
        download_dir = run_dir / f"download_{uuid.uuid4().hex[:8]}"
        try:
            task = dreamina_video.query(submit_id, download_dir=download_dir, timeout_s=180.0)
        except dreamina_video.DreaminaVideoError as exc:
            source = existing_video()  # 另一路可能刚好收好片
            if source is None:
                message = f"即梦视频下载失败：{exc}"
                manifest = _mark_if_open(run_dir, manifest, status="failed", error=message)
                raise AdhocVideoError(message) from exc
        else:
            gen_status, reason = dreamina_video.task_status(task)
            if gen_status != "success":
                error = dreamina_video.explain_fail(reason) or "即梦视频作业失败"
                source = existing_video()  # 另一路可能刚好收好片
                if source is None:
                    return _mark_if_open(run_dir, manifest, status="failed", error=error)
            else:
                try:
                    sources = dreamina_video.extract_video_paths(task)
                except dreamina_video.DreaminaVideoError as exc:
                    source = existing_video()  # 另一路可能刚好收好片
                    if source is None:
                        return _mark_if_open(run_dir, manifest, status="failed", error=str(exc))
                else:
                    first = sources[0]
                    ext = dreamina_video.video_file_ext(first)
                    if ext == "bin":
                        return _mark_if_open(
                            run_dir, manifest, status="failed",
                            error="即梦产物不是可识别的视频格式（按魔数嗅探失败）",
                        )
                    destination = original_dir / f"000000.{ext}"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(first, destination)
                    shutil.rmtree(download_dir, ignore_errors=True)
                    source = destination

    poster = _make_poster(source, run_dir)
    result = VideoResult(
        run_id=run_id,
        video_path=str(source),
        poster_path=str(poster) if poster else "",
        provider="dreamina",
        manifest_path="",
        job_id=submit_id,
        status="ready",
        seed=request.get("seed") or 0,
        model=str(job.get("model") or "") or None,
        duration=job.get("duration") if job.get("duration") is not None else request.get("duration"),
        resolution=str(job.get("resolution") or request.get("resolution") or "720p"),
        aspect_ratio=str(request.get("aspect_ratio") or ""),
        cost=None,  # 即梦没有逐条费用：按会员权益/积分计（user_credit 查余额）
        duration_ms=int((time.time() - submitted_at) * 1000),
    )
    out = str(request.get("out") or "")
    _write_out(result, Path(out) if out else None)
    result.manifest_path = str(run_dir / "manifest.json")
    manifest["result"] = result.model_dump()
    manifest = _mark(run_dir, manifest, status="ready", error="")
    # 暂存参考图/视频用完了清掉（失败路径保留以便重试 —— 与 OpenRouter 同策略）
    clear_staged_references(list(request.get("staged_ref_ids") or []))
    clear_staged_video_references(list(request.get("staged_video_ref_ids") or []))
    return manifest

def refresh(run_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """查一次作业并把成片收下来（web /videos/{id}/status 的引擎，幂等）。

    - 终态（ready/failed）或没有 job 的记录：直接读 manifest 返回摘要，不再打上游；
    - 其余：轮询一次；OpenRouter completed → 下载成片，即梦 success → 单独下成片
      （query_result --download_dir），终态失败 → 记错误；still running → 更新状态。
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
    backend = str((manifest.get("request") or {}).get("backend") or "openrouter")
    if backend == "dreamina":
        # 即梦分支：job.id 是 submit_id，每次轮询是一发 query_result 子进程 ——
        # 保持「每次轻查询」，子进程里不再 --poll 等待（别把 web 轮询线程挂住）
        try:
            task = dreamina_video.poll(job_id)
        except dreamina_video.DreaminaVideoError as exc:
            # 网络抖动/CLI 偶发失败不该打断轮询节奏：状态不变，摘要带 poll_error
            summary = _summary(manifest)
            summary["poll_error"] = str(exc)
            return summary
        gen_status, _reason = dreamina_video.task_status(task)
        if not gen_status:
            # 响应缺 gen_status（上游改字段名 / 本地任务记录被清）——
            # 别静默当中间态：以 poll_error 暴露（前端照常重试，但错误可见），
            # 否则 web 会每 8s 一发 query_result 永远不停、还零提示
            summary = _summary(manifest)
            summary["poll_error"] = (
                f"即梦任务响应缺 gen_status（submit_id={job_id}）："
                f"{json.dumps(task, ensure_ascii=False)[:200]}"
            )
            return summary
        job["status"] = gen_status or job.get("status") or ""
        if gen_status in ("success", "fail"):
            submitted = float(manifest.get("submitted_at") or time.time())
            try:
                manifest = _finalize(run_id, manifest, task, submitted)
            except (AdhocVideoError, dreamina_video.DreaminaVideoError) as exc:
                message = str(exc)
                if isinstance(exc, dreamina_video.DreaminaVideoError):
                    message = f"即梦视频下载失败：{message}"
                manifest = _mark_if_open(_run_dir(run_id), manifest, status="failed", error=message)
        else:
            # 中间态统一映射成内部枚举（即梦只有 querying，web 侧叫 in_progress）
            manifest["status"] = "in_progress"
            _write_manifest(_run_dir(run_id), manifest)
        return _summary(manifest)
    try:
        with _conn(db_path) as conn:
            creds = settings_service.openrouter_credentials(conn)
        payload = openrouter_video.poll(
            job_id, api_key=creds["api_key"], base_url=creds["base_url"]
        )
    except _ORError as exc:
        # 网络抖动不该打断前端的轮询节奏：返回当前摘要并带上 poll_error（状态不变）
        summary = _summary(manifest)
        summary["poll_error"] = str(exc)
        return summary
    status_now = str(payload.get("status") or "")
    job["status"] = status_now or job.get("status") or ""
    if status_now in ("completed", "failed", "cancelled", "expired"):
        submitted = float(manifest.get("submitted_at") or time.time())
        try:
            manifest = _finalize(
                run_id, manifest, payload, submitted,
                api_key=creds["api_key"], base_url=creds["base_url"],
            )
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
    poll_interval: float | None = None,
    timeout_s: float | None = None,
) -> VideoResult:
    """跑一次 Playground 视频生成，返回结果（含落盘路径与 manifest 路径）。

    ``wait=True``（CLI 默认）：阻塞轮询到终态（completed → 成片；failed → 抛错）；
    ``wait=False``（web）：提交即返回 pending，后续由 :func:`refresh` 收片。
    失败也留痕：manifest 记下失败原因方便回头查；超时不改状态（作业还在跑，
    refresh 可续）。
    """
    poll_interval = 5.0 if poll_interval is None else float(poll_interval)
    if req.backend == "dreamina":
        # --timeout 不给时走即梦链（env DREAMINA_VIDEO_TIMEOUT > 900s）
        timeout_s = dreamina_video.resolve_timeout(timeout_s)
    else:
        timeout_s = 900.0 if timeout_s is None else float(timeout_s)
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
            provider=req.backend,
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

    creds_key, creds_url = None, None
    if req.backend != "dreamina":
        with _conn(db_path) as conn:
            creds = settings_service.openrouter_credentials(conn)
        creds_key, creds_url = creds["api_key"], creds["base_url"]
    try:
        if req.backend == "dreamina":
            payload = dreamina_video.wait_for_video(
                job_id,
                poll_interval=poll_interval,
                timeout_s=timeout_s,
                timeout_error=AdhocVideoTimeout,
            )
        else:
            payload = openrouter_video.wait_for_job(
                job_id,
                api_key=creds_key,
                base_url=creds_url,
                poll_interval=poll_interval,
                timeout_s=timeout_s,
                timeout_error=AdhocVideoTimeout,
            )
        _finalize(run_id, manifest, payload, submitted_at, api_key=creds_key, base_url=creds_url)
    except AdhocVideoTimeout:
        # 作业还在跑：状态保持 in_progress，refresh/CLI 再查可以续
        manifest["submitted_at"] = submitted_at
        _write_manifest(run_dir, manifest)
        raise
    except (AdhocVideoError, _ORError, dreamina_video.DreaminaVideoError) as exc:
        message = str(exc)
        if isinstance(exc, _ORError):
            message = f"OpenRouter 视频下载失败：{message}"
        elif isinstance(exc, dreamina_video.DreaminaVideoError):
            message = f"即梦视频失败：{message}"
        if req.backend == "dreamina":
            _mark_if_open(run_dir, manifest, status="failed", error=message)
        else:
            _mark(run_dir, manifest, status="failed", error=message)
        raise AdhocVideoError(message) from exc

    final = read_manifest(run_id)
    if final.get("status") == "failed":
        raise AdhocVideoError(str(final.get("error") or "OpenRouter 视频作业失败"))
    return VideoResult(**(final.get("result") or {}))