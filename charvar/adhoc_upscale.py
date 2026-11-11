"""Playground 放大：给一段视频把短边放大到目标档位（SeedVR2 超分 / FFmpeg 插值）。

与出图（``charvar.adhoc``）/ 出视频（``charvar.adhoc_video``）同构：网页与 CLI
``charvar upscale`` 是同一套参数语义、同一套落盘、同一份 manifest；不进主体档案、
不跑预设批量、不落 runs 表。

两条后端，按 ``backend`` 分派（manifest 层已经后端无关）：
- ``comfyui``：SeedVR2 workflow（``data/workflows/utility_seedvr2_3b_int8_upscale_video``
  成对文件）。视频先 ``upload_video`` 进 ComfyUI input 目录、``file`` 字段按
  mapping 注入；**放大倍数不是固定 2x** —— 按「目标短边/源短边」算出 multiplier
  注入 ResizeImageMaskNode（lanczos），交给 SeedVR2 单步修复。Submit 之后是
  ComfyUI 队列作业：prompt_id 进 manifest.job.id，:func:`refresh` 轮询
  /history 收片 —— 与视频通路一样是异步作业。
- ``ffmpeg``：本地 lanczos 插值重编码（libx264 crf 18），秒级出片。CLI
  （``wait=True``）同步 ``subprocess.run``；web（``wait=False``）后台线程跑完
  写终态，:func:`refresh` 对该分支只读 manifest。

「短边已达目标」规则（用户拍板）：源视频短边 ≥ 目标档位时**不再放大、原样
复制当产物** —— SeedVR2 是超分修复模型，缩小/无增益都不该跑；ffmpeg 同规则
保持两条后端语义一致。

落盘：``data/generated/adhoc_upscale/{id}/``（original/ 成片 + thumbs/ 海报帧 +
manifest.json）。产物不落 runs 表（临时性质，图片墙不会扫到，这是预期的）。

红线（同 AGENTS.md / adhoc 家族）：
- ComfyUI 请求一律走 ``charvar.exporters.comfyui`` 的客户端（``trust_env=False``，
  绕 shell 代理）；**节点 id 一律来自 mapping.json**，本模块不出现任何节点 id；
- run_id 直接拼路径，校验正则比 ``assets.safe_run_id`` 严（防 ``..``）；
- 上传 ComfyUI 的 remote_name 必须唯一（upload 端点 overwrite=true，同名覆盖）；
- 输入视频按魔数判容器（``openrouter_video`` 的嗅探），不信任文件名后缀；
- 不引入随机源（本功能根本没有随机参数）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from charvar import paths, settings_service
from charvar.exporters import comfyui as comfyui_exporter
from charvar.openrouter_video import OpenRouterVideoError as _ORError

Backend = Literal["comfyui", "ffmpeg"]

# 目标档位 = 短边像素。与 charvar.video._RES_SHORT 同值（那边服务合成视频，
# 这里服务放大；两处都按「短边」定义档位）。别只改一处。
_TARGET_SHORT = {"720p": 720, "1080p": 1080, "4k": 2160}

# SeedVR2 workflow 的兜底名（成对文件，在 data/workflows/）。
_DEFAULT_WORKFLOW = "utility_seedvr2_3b_int8_upscale_video"

# 网页上传的输入视频上限：放大不经 base64（直接落盘 + 转 ComfyUI input），
# 比 adhoc_video._vrefs 的 48MB（base64 请求体上限）放宽；200MB 拦手机长视频。
_INPUT_MAX_BYTES = 200 * 1024 * 1024
_VIDEO_SUFFIXES = (".mp4", ".mov", ".mpeg", ".mpg", ".webm")


class UpscaleError(ValueError):
    """参数或环境错误（API 层转 400，CLI 层转友好提示）。"""


class UpscaleTimeout(UpscaleError):
    """轮询超时（作业还在跑；manifest 保持 in_progress，refresh 可续）。"""


class UpscaleRequest(BaseModel):
    """一次视频放大的请求。"""

    backend: Backend = "comfyui"
    input_path: Path  # 放大源视频；web 层由暂存 id 解析出路径
    target: str = "1080p"  # 档位 = 输出短边：720p/1080p/4k
    workflow: str | None = None  # 仅 comfyui；空 = 设置页/兜底 workflow
    wait: bool = True  # CLI 默认阻塞到终态；web 恒传 False 走轮询
    staged_input_id: str = ""  # web 暂存输入 id，ready 时清理
    out: Path | None = None

    def request_backend(self) -> str:
        """请求里给的后端（空串/None = None，让上层走设置页链路）。"""
        return (self.backend or "").strip() or None


class UpscaleResult(BaseModel):
    """一次视频放大的结果。"""

    run_id: str
    video_path: str = ""
    poster_path: str = ""
    backend: Backend
    manifest_path: str = ""
    job_id: str = ""
    status: Literal["pending", "in_progress", "ready", "failed"] = "pending"
    target: str = "1080p"
    source_short: int = 0  # 源视频短边（探测值）
    target_short: int = 0
    multiplier: float = 0.0  # target/source；passthrough 时为 0
    passthrough: bool = False  # 源短边已达标 → 原样复制返回
    width: int = 0  # 产物实际尺寸（探测值）
    height: int = 0
    duration_ms: int = 0


# ---------- 落盘目录 ----------


def upscale_root() -> Path:
    """Playground 放大根目录：``<data>/generated/adhoc_upscale/``。"""
    return paths.data_dir() / "generated" / "adhoc_upscale"


def inputs_dir() -> Path:
    """网页上传的输入视频暂存目录（``_inputs/``）—— 放完就用不上，可随时清。"""
    return upscale_root() / "_inputs"


def _new_run_id() -> str:
    """本次放大的 id：只含 ``[A-Za-z0-9_.-]``（assets.safe_run_id 的字符集约束）。"""
    return f"ups_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"


# run_id 必须是「ups_ 前缀 + 安全字符集」。与 adhoc 家族同款：run_id 直接拼进路径，
# assets.safe_run_id 的正则放行 ".."，这里必须自己拦。
_RUN_ID_RE = re.compile(r"^ups_[A-Za-z0-9_.-]+$")


def _run_dir(run_id: str) -> Path:
    return upscale_root() / run_id


def run_dir(run_id: str) -> Path:
    """公开的 run 目录（对外服务产物用；非法 id 直接报错，不拼路径）。"""
    if not _RUN_ID_RE.fullmatch(str(run_id)) or ".." in str(run_id):
        raise UpscaleError(f"非法 run_id：{run_id}")
    return _run_dir(run_id)


def video_file(run_id: str) -> Path:
    """某次放大的成片（``original/`` 下唯一一个文件）。"""
    directory = run_dir(run_id) / "original"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file() and not path.name.startswith("."):
            return path
    raise FileNotFoundError(f"没有视频产物：{run_id}")


def poster_file(run_id: str) -> Path:
    """某次放大的海报帧（``thumbs/`` 下唯一一个文件）。"""
    directory = run_dir(run_id) / "thumbs"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有海报帧：{run_id}")


# ---------- 暂存输入视频（网页上传用；四件套同 adhoc 家族） ----------


def save_staged_input(data: bytes, filename: str = "") -> Path:
    """把上传的输入视频存进暂存目录，返回路径。

    后缀按**魔数**判（``openrouter_video.sniff_video_ext``），不信任上传时的
    文件名；大小先拦（放大不跑 base64，上限比参考视频宽）。
    """
    if not data:
        raise UpscaleError("输入视频是空的")
    if len(data) > _INPUT_MAX_BYTES:
        raise UpscaleError(
            f"输入视频超过 {_INPUT_MAX_BYTES // (1024 * 1024)}MB 上限"
            f"（收到 {len(data) // (1024 * 1024)}MB）"
        )
    ext = _sniff_ext_bytes(data)
    target = inputs_dir() / f"{uuid.uuid4().hex}{'.mov' if ext == 'mov' else '.' + ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _sniff_ext_bytes(data: bytes) -> str:
    from charvar import openrouter_video

    ext = openrouter_video.sniff_video_ext(data)
    if ext == "bin":
        raise UpscaleError("输入视频不是可识别的容器（mp4/mov/mpeg/webm）")
    return ext


def staged_input(ref_id: str) -> Path:
    """按 id 取暂存输入视频路径（非法 id 报错）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", str(ref_id or "")):
        raise UpscaleError(f"非法输入视频 id：{ref_id}")
    matches = sorted(inputs_dir().glob(f"{ref_id}.*"))
    for path in matches:
        if path.suffix.lower() in _VIDEO_SUFFIXES and path.is_file():
            return path
    raise UpscaleError(f"输入视频不存在或已清理：{ref_id}")


def clear_staged_inputs(ref_ids: list[str]) -> None:
    """删掉用完的暂存输入（ready 时调用，失败时保留以便重试）。"""
    for ref_id in ref_ids or []:
        try:
            staged_input(ref_id).unlink(missing_ok=True)
        except UpscaleError:
            continue


def prune_staged_inputs(max_age_s: float = 24 * 3600) -> int:
    """清掉过期未用的暂存输入，返回删掉几个（传了视频没点放大的兜底）。"""
    directory = inputs_dir()
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
    root = upscale_root()
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
    """manifest 留痕的 run 总数（「最近放大」翻页用）。"""
    return len(_recent_run_names())


def list_recent(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """最近的 Playground 放大记录（读 manifest.json，按时间倒序翻页窗口）。"""
    root = upscale_root()
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
        result = manifest.get("result") or {}
        status = str(manifest.get("status") or "")
        has_video = False
        if status == "ready":
            original = directory / "original"
            has_video = original.is_dir() and any(
                p.is_file() and not p.name.startswith(".") for p in original.iterdir()
            )
        records.append(
            {
                "run_id": directory.name,
                "created_at": manifest.get("created_at") or "",
                "backend": request.get("backend") or "",
                "target": request.get("target") or "",
                "input_name": Path(str(request.get("input_path") or "")).name,
                "status": status or "failed",
                "error": manifest.get("error") or "",
                "has_video": has_video,
                "multiplier": result.get("multiplier"),
                "width": result.get("width") or 0,
                "height": result.get("height") or 0,
                "duration_ms": result.get("duration_ms") or 0,
            }
        )
    return records


def read_manifest(run_id: str) -> dict[str, Any]:
    """读某个 run 的 manifest（web status 端点与刷新逻辑共用）。"""
    path = run_dir(run_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"没有 manifest：{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_manifest(req: UpscaleRequest, run_id: str, *, job_id: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": {
            "backend": req.backend,
            "input_path": str(req.input_path),
            "target": req.target,
            "workflow": req.workflow,
            "wait": req.wait,
            "staged_input_id": req.staged_input_id,
            "out": str(req.out or ""),
        },
        "job": {"id": job_id, "status": "pending"},
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
        "backend": request.get("backend") or "",
        "target": request.get("target") or "",
        "source_short": result.get("source_short") if result else 0,
        "target_short": result.get("target_short") if result else 0,
        "multiplier": result.get("multiplier") if result else None,
        "passthrough": result.get("passthrough") if result else False,
        "width": result.get("width") if result else 0,
        "height": result.get("height") if result else 0,
        "duration_ms": result.get("duration_ms") or 0,
    }


def _mark(run_dir: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """小工具：就地改 manifest 若干键并落盘（终态/状态推进共用）。"""
    patch.setdefault("finalized_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    manifest.update(patch)
    _write_manifest(run_dir, manifest)
    return manifest


def _mark_if_open(run_dir: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """终态写入保护：写前重读盘上 manifest，已是终态（ready/failed）就不覆写。

    CLI 与 web（或两个页签）可能同时盯同一个作业；本路失败不该把另一路刚收好
    的 ready 抹掉（实时踩坑见 adhoc_video 同名函数）。
    """
    try:
        disk = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        disk = None
    if isinstance(disk, dict) and disk.get("status") in ("ready", "failed"):
        return disk
    return _mark(run_dir, manifest, **patch)


# ---------- 校验与探测 ----------


def _ffprobe_bin() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/ffprobe")
    if fallback.exists():
        return str(fallback)
    raise UpscaleError("未找到 ffprobe：请先 brew install ffmpeg")


def probe_video_size(path: Path) -> tuple[int, int]:
    """ffprobe 读视频宽高（探测失败当参数错误，别把坏文件发去上游）。"""
    cmd = [
        _ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except OSError as exc:
        raise UpscaleError(f"探测视频尺寸失败：{exc}") from exc
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    line = text.splitlines()[0].strip() if text else ""
    parts = line.split("x")
    if proc.returncode != 0 or len(parts) != 2:
        raise UpscaleError(f"无法读取视频尺寸（不是有效视频？）：{Path(path).name}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise UpscaleError(f"视频尺寸解析失败：{text[:80]}") from exc


def compute_multiplier(source_short: int, target_short: int) -> float:
    """放大倍数 = 目标短边/源短边（六位小数，注入 resize 的 multiplier）。"""
    if source_short <= 0:
        raise UpscaleError("源视频短边无效")
    return round(target_short / source_short, 6)


def validate(req: UpscaleRequest) -> None:
    """请求自检：能在提交前发现的错就别浪费一次入队。"""
    target = str(req.target or "").strip().lower()
    if target not in _TARGET_SHORT:
        raise UpscaleError(f"target 只支持 {'/'.join(_TARGET_SHORT)}，当前：{req.target}")
    if req.backend not in (("comfyui", "ffmpeg")):
        raise UpscaleError(f"backend 只支持 comfyui/ffmpeg，当前：{req.backend}")
    if not Path(req.input_path).exists():
        raise UpscaleError(f"输入视频不存在：{req.input_path}")
    try:
        from charvar import openrouter_video

        openrouter_video.sniff_video_ref(Path(req.input_path))
    except _ORError as exc:
        raise UpscaleError(str(exc)) from exc


def _config(db_path: Path | None = None) -> dict[str, Any]:
    with _conn(db_path) as conn:
        settings = settings_service.get_settings(conn)
    return settings.upscale.model_dump()


def _conn(db_path: Path | None = None):
    from charvar import storage

    return storage.connect(db_path)


# ---------- 合成 GenerateResult（喂给 comfyui_exporter.submit 的占位单条） ----------


def _to_synthetic_result(run_id: str) -> "GenerateResult":  # noqa: F821
    from charvar.models import GenerateResult, PromptItem

    item = PromptItem(
        index=0,
        variant_id="upscale",
        prompt="",
        master="",
        variant_text="",
        background=None,
        lighting=None,
        caption="",
        meta={"upscale": True},
    )
    return GenerateResult(
        run_id=run_id,
        subject_id=run_id,
        preset="upscale",
        seed=0,
        items=[item],
    )


# ---------- 海报与产物 ----------


def _make_poster(video_path: Path, run_dir: Path) -> Path | None:
    """海报帧抽第一帧（复用 adhoc_video 的实现，失败不挡成片）。"""
    from charvar.adhoc_video import _make_poster as _ad_video_poster

    return _ad_video_poster(video_path, run_dir)


def _write_out(result: UpscaleResult, out: Path | None) -> None:
    """``--out`` 指定路径时再落一份出去（成片本体不动，只是复制）。"""
    if out is None or not result.video_path:
        return
    source = Path(result.video_path)
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    result.video_path = str(destination)


# ---------- comfyui（SeedVR2）后端 ----------


def _submit_comfyui(req: UpscaleRequest, run_id: str, info: dict[str, Any], db_path: Path | None) -> dict[str, Any]:
    """上传视频 → 算 multiplier → 注入提交；返回 in_progress manifest。

    节点 id 全部来自 mapping.json（file → LoadVideo 的 file、multiplier →
    ResizeImageMaskNode 的 resize_type.multiplier、filename_prefix → SaveVideo）。
    """
    run_dir = _run_dir(run_id)
    cfg = _config(db_path)
    workflow_name = (
        (req.workflow or "").strip()
        or str(cfg.get("workflow") or "").strip()
        or _DEFAULT_WORKFLOW
    )
    try:
        workflow, mapping = comfyui_exporter.load_workflow_pair(workflow_name)
    except FileNotFoundError as exc:
        raise UpscaleError(str(exc)) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise UpscaleError(f"workflow/mapping 无效：{exc}") from exc

    base = paths.comfy_base_url(None)
    remote = comfyui_exporter.upload_video(
        base, Path(req.input_path), remote_name=f"{run_id}_input{Path(req.input_path).suffix}"
    )
    overrides = {
        "file": remote,
        "multiplier": info["multiplier"],
        "filename_prefix": f"upscale/{run_id}",
    }
    try:
        submissions = comfyui_exporter.submit(
            _to_synthetic_result(run_id),
            workflow,
            mapping,
            overrides=overrides,
            wait=False,
        )
    except Exception as exc:  # noqa: BLE001 - submit 单条外的意外错误也要报原文
        raise UpscaleError(f"ComfyUI 提交失败：{exc}") from exc
    entry = submissions[0] if submissions else {}
    if not entry.get("ok"):
        raise UpscaleError(str(entry.get("error") or "ComfyUI 提交失败"))
    prompt_id = str(entry.get("prompt_id") or "") or ""
    if not prompt_id:
        raise UpscaleError("ComfyUI 未返回 prompt_id")

    manifest = _new_manifest(req, run_id, job_id=prompt_id)
    manifest["status"] = "in_progress"
    manifest["job"]["status"] = "queued"
    manifest["job"]["workflow"] = workflow_name
    nodes = mapping.get("output_nodes")
    manifest["job"]["output_nodes"] = [str(n) for n in nodes] if isinstance(nodes, list) and nodes else []
    manifest["compute"] = {
        "source_width": info["width"],
        "source_height": info["height"],
        "source_short": info["source_short"],
        "target_short": info["target_short"],
        "multiplier": info["multiplier"],
    }
    _write_manifest(run_dir, manifest)
    return manifest


def _download_output(client, base: str, file: dict[str, str]) -> bytes:
    params = {
        "filename": file.get("filename", ""),
        "subfolder": file.get("subfolder", ""),
        "type": file.get("type", "output"),
    }
    resp = client.get(f"{base.rstrip('/')}/view", params=params, timeout=60)
    resp.raise_for_status()
    if not resp.content:
        raise UpscaleError(f"ComfyUI 输出为空：{file}")
    return resp.content


def _refresh_comfyui(
    run_id: str, manifest: dict[str, Any], info: dict[str, Any], submitted_at: float
) -> dict[str, Any]:
    """ComfyUI 分支：单次查 /history，终态收片，否则返回当前摘要。"""
    run_dir = _run_dir(run_id)
    job_id = str((manifest.get("job") or {}).get("id") or "")
    base = paths.comfy_base_url(None)
    try:
        with comfyui_exporter._httpx_client(timeout=60.0) as client:
            resp = client.get(f"{base}/history/{job_id}", timeout=30)
        entry = (resp.json() or {}).get(job_id) if resp.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001 - 网络抖动不该打断轮询节奏
        summary = _summary(manifest)
        summary["poll_error"] = str(exc)
        return summary
    if entry is None:
        # 还没进 history（排队中 / 已执行完被清）——状态不变
        return _summary(manifest)
    status = entry.get("status") or {}
    for msg_type, msg_data in status.get("messages", []) or []:
        if msg_type == "execution_error":
            return _mark(
                run_dir, manifest, status="failed",
                error=f"ComfyUI execution error: {json.dumps(msg_data, ensure_ascii=False)[:400]}",
            )
    if not (status.get("completed") or "outputs" in entry):
        manifest["status"] = "in_progress"
        manifest["job"]["status"] = "running"
        _write_manifest(run_dir, manifest)
        return _summary(manifest)

    output_nodes = output_nodes_of(manifest)
    files = comfyui_exporter._output_filenames(entry, output_nodes)
    if not files:
        return _mark(run_dir, manifest, status="failed", error="ComfyUI 执行完成但没有输出文件")
    try:
        with comfyui_exporter._httpx_client(timeout=600.0) as client:
            data = _download_output(client, base, files[0])
    except Exception as exc:  # noqa: BLE001
        return _mark(run_dir, manifest, status="failed", error=f"ComfyUI 输出下载失败：{exc}")
    suffix = Path(str(files[0].get("filename") or "")).suffix or ".mp4"
    destination = run_dir / "original" / f"000000{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return _finalize_success(run_dir, manifest, destination, info, submitted_at)


def output_nodes_of(manifest: dict[str, Any]) -> list[str] | None:
    """收片范围：提交时存进 manifest.job.output_nodes 的 mapping 声明。"""
    nodes = (manifest.get("job") or {}).get("output_nodes") or []
    return [str(n) for n in nodes] or None


# ---------- ffmpeg 后端 ----------


def _ffmpeg_command(source: Path, destination: Path, info: dict[str, Any]) -> list[str]:
    """lanczos 等比放大 + libx264 重编码（编码参数对齐 video.render_video）。

    输出宽高按 multiplier 各自取偶（yuv420p 要求偶数边）；音频统一转 aac，
    防源容器（如 webm 的 opus）拷进 mp4 失败。
    """
    width = int(info["width"] * info["multiplier"])
    height = int(info["height"] * info["multiplier"])
    width += width % 2
    height += height % 2
    from charvar.video import _ffmpeg_bin

    return [
        _ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        str(destination),
    ]


def _finalize_success(
    run_dir: Path, manifest: dict[str, Any], video_path: Path,
    info: dict[str, Any], submitted_at: float,
) -> dict[str, Any]:
    """成片已落盘：海报 → 探测尺寸 → result 写 manifest → ready。"""
    poster = _make_poster(video_path, run_dir)
    try:
        out_w, out_h = probe_video_size(video_path)
    except UpscaleError:
        out_w, out_h = 0, 0
    result = UpscaleResult(
        run_id=str(manifest.get("run_id") or ""),
        video_path=str(video_path),
        poster_path=str(poster) if poster else "",
        backend=str((manifest.get("request") or {}).get("backend") or "comfyui"),  # type: ignore[arg-type]
        manifest_path="",
        job_id=str((manifest.get("job") or {}).get("id") or ""),
        status="ready",
        target=str((manifest.get("request") or {}).get("target") or ""),
        source_short=info["source_short"],
        target_short=info["target_short"],
        multiplier=info["multiplier"],
        passthrough=bool(info.get("passthrough")),
        width=out_w,
        height=out_h,
        duration_ms=int((time.time() - submitted_at) * 1000),
    )
    request = manifest.get("request") or {}
    out = str(request.get("out") or "")
    _write_out(result, Path(out) if out else None)
    result.manifest_path = str(run_dir / "manifest.json")
    manifest["result"] = result.model_dump()
    manifest = _mark(run_dir, manifest, status="ready", error="")
    clear_staged_inputs([str(request.get("staged_input_id") or "")])
    return manifest


def _run_ffmpeg(
    run_id: str, req: UpscaleRequest, info: dict[str, Any], manifest: dict[str, Any], submitted_at: float
) -> UpscaleResult:
    """同步跑 ffmpeg（CLI wait=True 路径）；失败写 manifest failed 并抛错。"""
    run_dir = _run_dir(run_id)
    tmp = run_dir / "original" / ".ups-000000.mp4"  # 先写临时名，成功后原子改名
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    cmd = _ffmpeg_command(Path(req.input_path), tmp, info)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    except subprocess.TimeoutExpired:
        _mark(run_dir, manifest, status="failed", error="ffmpeg 放大超时（1h）")
        raise UpscaleError("ffmpeg 放大超时（1h）") from None
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
        _mark(run_dir, manifest, status="failed", error=f"ffmpeg 放大失败：{detail}")
        raise UpscaleError(f"ffmpeg 放大失败：{detail}")
    destination = run_dir / "original" / "000000.mp4"
    tmp.replace(destination)
    final_manifest = _finalize_success(run_dir, manifest, destination, info, submitted_at)
    if final_manifest.get("status") != "ready":
        raise UpscaleError(str(final_manifest.get("error") or "ffmpeg 收尾失败"))
    return UpscaleResult(**(final_manifest.get("result") or {}))


def _ffmpeg_worker(run_id: str, req: UpscaleRequest, info: dict[str, Any]) -> None:
    """web wait=False 的后台线程：跑完写终态（异常也不许裸崩）。"""
    try:
        manifest = read_manifest(run_id)
        _run_ffmpeg(run_id, req, info, manifest, float(manifest.get("submitted_at") or time.time()))
    except Exception as exc:  # noqa: BLE001 - 线程边界：兜一切错并落盘留痕
        try:
            manifest = read_manifest(run_id)
            _mark_if_open(_run_dir(run_id), manifest, status="failed",
                          error=f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass


# ---------- 已达标分支 ----------


def _finalize_passthrough(
    run_id: str, req: UpscaleRequest, info: dict[str, Any], manifest: dict[str, Any], submitted_at: float
) -> UpscaleResult:
    """源短边已 ≥ 目标：原样复制当产物（不跑放大，两条后端同规则）。"""
    run_dir = _run_dir(run_id)
    ext_map = {"video/mp4": ".mp4", "video/mov": ".mov", "video/webm": ".webm"}
    from charvar import openrouter_video

    mime = openrouter_video.sniff_video_ref(Path(req.input_path))
    destination = run_dir / "original" / f"000000{ext_map.get(mime, '.mp4')}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(req.input_path, destination)
    info["passthrough"] = True
    info["multiplier"] = 0.0
    final = _finalize_success(run_dir, manifest, destination, info, submitted_at)
    result = UpscaleResult(**(final.get("result") or {}))
    _write_out(result, req.out)
    return result


# ---------- 对外入口 ----------


def refresh(run_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """查一次作业并收片（web /upscales/{id}/status 的引擎，幂等）。

    终态（ready/failed）或没有 job 的记录：直接读 manifest 返回摘要；
    comfyui 分支查一次 /history（未完成 → 状态不变）；ffmpeg 分支只读
    manifest（后台线程自己会写终态）。网络抖动以 poll_error 暴露，不打断轮询。
    """
    manifest = read_manifest(run_id)
    status = str(manifest.get("status") or "")
    job_id = str((manifest.get("job") or {}).get("id") or "")
    if status in ("ready", "failed") or not job_id:
        if not status:  # 脏数据兜底：有 job 没 status
            manifest = _mark(_run_dir(run_id), manifest, status="failed",
                             error=manifest.get("error") or "提交失败")
        return _summary(manifest)
    backend = str((manifest.get("request") or {}).get("backend") or "comfyui")
    if backend == "ffmpeg":
        return _summary(manifest)  # 后台线程写终态；这里只读
    compute = manifest.get("compute") or {}
    info = {
        "width": compute.get("source_width") or 0,
        "height": compute.get("source_height") or 0,
        "source_short": compute.get("source_short") or 0,
        "target_short": compute.get("target_short") or 0,
        "multiplier": compute.get("multiplier") or 1.0,
        "passthrough": False,
    }
    try:
        return _refresh_comfyui(run_id, manifest, info, float(manifest.get("submitted_at") or time.time()))
    except Exception as exc:  # noqa: BLE001 - 收片失败的意外错误也落盘留痕
        message = f"{type(exc).__name__}: {exc}"
        manifest = _mark(_run_dir(run_id), manifest, status="failed", error=message)
        return _summary(manifest)


def generate(
    req: UpscaleRequest,
    db_path: Path | None = None,
    *,
    poll_interval: float = 5.0,
    timeout_s: float | None = None,
) -> UpscaleResult:
    """跑一次视频放大，返回结果（含落盘路径与 manifest 路径）。

    ``wait=True``（CLI 默认）：阻塞到终态（ready → 成片；failed → 抛错）；
    ``wait=False``（web）：提交即返回 pending，后续由 :func:`refresh` 收片
    （ffmpeg 分支是后台线程，comfyui 分支轮询 ComfyUI history）。失败也留痕。
    """
    validate(req)
    run_id = _new_run_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    submitted_at = time.time()

    try:
        width, height = probe_video_size(req.input_path)
    except UpscaleError as exc:
        manifest = _new_manifest(req, run_id)
        _mark(run_dir, manifest, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise

    target_short = _TARGET_SHORT[str(req.target).strip().lower()]
    source_short = min(width, height)
    info = {
        "width": width,
        "height": height,
        "source_short": source_short,
        "target_short": target_short,
        "passthrough": False,
    }
    if source_short >= target_short:
        # 用户拍板：短边已达标 → 原样返回不放大（SeedVR2 是超分模型，缩小不出增益）
        manifest = _new_manifest(req, run_id)
        manifest["submitted_at"] = submitted_at
        _write_manifest(run_dir, manifest)
        return _finalize_passthrough(run_id, req, info, manifest, submitted_at)

    manifest = _new_manifest(req, run_id)
    try:
        if req.backend == "comfyui":
            info["multiplier"] = compute_multiplier(source_short, target_short)
            manifest = _submit_comfyui(req, run_id, info, db_path)
        else:
            manifest["job"]["id"] = "ffmpeg"
            manifest["status"] = "in_progress"
            manifest["job"]["status"] = "running"
            manifest["submitted_at"] = submitted_at
            info["multiplier"] = compute_multiplier(source_short, target_short)
            manifest["compute"] = {
                "source_width": width, "source_height": height,
                "source_short": source_short, "target_short": target_short,
                "multiplier": compute_multiplier(source_short, target_short),
            }
            _write_manifest(run_dir, manifest)
    except Exception as exc:  # noqa: BLE001 - 提交失败也要留痕（历史列表能看见原因）
        _mark(run_dir, manifest, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    manifest["submitted_at"] = submitted_at
    _write_manifest(run_dir, manifest)

    def _pending() -> UpscaleResult:
        return UpscaleResult(
            run_id=run_id,
            backend=req.backend,  # type: ignore[arg-type]
            manifest_path=str(run_dir / "manifest.json"),
            job_id=str((manifest.get("job") or {}).get("id") or ""),
            status="pending",
            target=req.target,
            source_short=source_short,
            target_short=target_short,
            multiplier=float((manifest.get("compute") or {}).get("multiplier") or 0),
        )

    if not req.wait:
        if req.backend == "ffmpeg":
            threading.Thread(
                target=_ffmpeg_worker, args=(run_id, req, info), daemon=True, name=f"upscale-{run_id}"
            ).start()
        return _pending()

    if req.backend == "ffmpeg":
        return _run_ffmpeg(run_id, req, info, manifest, submitted_at)

    # comfyui wait=True：阻塞轮询 refresh 到终态
    timeout = 1800.0 if timeout_s is None else float(timeout_s)
    deadline = time.time() + timeout
    while True:
        summary = refresh(run_id, db_path)
        if summary.get("status") in ("ready", "failed"):
            break
        if time.time() >= deadline:
            manifest["submitted_at"] = submitted_at
            _write_manifest(run_dir, manifest)
            raise UpscaleTimeout(
                f"放大作业 {run_id} 在 {timeout:.0f}s 内未完成（仍在跑，manifest 保留，可再查）"
            )
        time.sleep(poll_interval)
    final = read_manifest(run_id)
    if final.get("status") == "failed":
        raise UpscaleError(str(final.get("error") or "放大作业失败"))
    result = UpscaleResult(**(final.get("result") or {}))
    _write_out(result, req.out)
    return result
