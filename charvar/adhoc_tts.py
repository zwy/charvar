"""Playground 语音:给一段文本直接出一行语音(TTS)。

与出图(``charvar.adhoc``)、出视频(``charvar.adhoc_video``)同构:网页与 CLI
``charvar tts`` 是同一套参数语义、同一套落盘、同一份 manifest;不进主体档案
5 槽位、不跑预设批量、不落 runs 表。

与视频的关键差异(TTS API 是**同步**的):
- ``POST /audio/speech`` 一次请求直接返回音频字节,没有提交/轮询/下载三段,
  所以 :func:`generate` 一步到终态,web 也就不需要 status 轮询端点。
- voice:目录 ``supported_voices`` 枚举非空时实测必填(heygen/minimax 不带 voice
  一律 400),client 层自动兜底第一项;request.voice 记用户原始选择(空 = 没选),
  结果里记实际发送的。
- 音频产物:``original/000000.wav|mp3``(裸 PCM 由 ``openrouter_tts`` 封装成 WAV,
  采样率/声道从响应 Content-Type 解析)。没有 thumbs —— 音频没有海报帧。

落盘:``data/generated/adhoc_tts/{id}/``(original/ + manifest.json)。manifest 记
完整请求与 generation_id(X-Generation-Id 头),便于复现与追溯;失败也留痕。

红线(同 AGENTS.md / adhoc):
- OpenRouter 请求一律走 ``charvar.openrouter_tts`` 的客户端(``trust_env=False``,
  绕 shell 代理);
- API key 与生图同链路(``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``);
- 文本无随机源;heygen 系 5000 字符上限提交前拦截(超限是 provider 400);
- 不落 runs 表(临时性质,图片墙 / 历史都不会扫到,这是预期的)。
"""
from __future__ import annotations

import json
import re
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from charvar import openrouter_tts, paths, settings_service
from charvar.openrouter_tts import OpenRouterTtsError as _ORError

Backend = Literal["openrouter"]

_HEYGEN_PREFIX = "heygen/"
# HeyGen 单次合成的字符上限(官方文档声明,实测一致);其他模型上限未知不预检
_TTS_MAX_CHARS = openrouter_tts._HEYGEN_MAX_CHARS


class AdhocTtsError(ValueError):
    """参数或环境错误(API 层转 400,CLI 层转友好提示)。"""


class TtsRequest(BaseModel):
    """一次 Playground 语音合成的请求。"""

    text: str
    backend: Backend = "openrouter"
    model: str | None = None  # OpenRouter slug;空 = 设置页/env 默认
    voice: str = ""  # 空 = 不发(voice 由 client 兜底/Provider 默认)
    response_format: str = ""  # 空 = 自动(探测缓存 > 静态表 > API 默认 pcm)
    out: Path | None = None

    def request_model(self) -> str:
        """请求里给的模型(空串/None = None,让上层走设置页链路)。"""
        return (self.model or "").strip() or None

    def request_voice(self) -> str:
        """请求里给的 voice(空串 = None,让 client 走枚举兜底)。"""
        return (self.voice or "").strip() or None

    def request_format(self) -> str:
        """请求里给的 response_format(空串 = None,让 client 自动协商)。"""
        return (self.response_format or "").strip() or None


class TtsResult(BaseModel):
    """一次 Playground 语音合成的结果。"""

    run_id: str
    audio_path: str
    provider: Backend
    manifest_path: str = ""
    model: str | None = None
    voice: str = ""  # 实际发送的 voice(client 兜底后)
    response_format: str = ""  # 实际发送的格式(空 = API 默认 pcm)
    sample_rate: int | None = None
    duration_s: float | None = None  # 音频时长(wav 精确读头;mp3 ffprobe 失败为 None)
    size_bytes: int = 0
    generation_id: str = ""
    cost: float | None = None  # 按目录价的预估(SpeechResult 无费用头)
    duration_ms: int = 0


# ---------- 落盘目录 ----------


def tts_root() -> Path:
    """Playground 语音根目录:``<data>/generated/adhoc_tts/``。"""
    return paths.data_dir() / "generated" / "adhoc_tts"


def _new_run_id() -> str:
    """本次合成的 id:只含 ``[A-Za-z0-9_.-]``。"""
    return f"tts_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"


# run_id 必须是「tts_ 前缀 + 安全字符集」。与 adhoc / adhoc_video 同款:run_id 直接
# 拼进路径,assets.safe_run_id 的正则放行 "..",这里必须自己拦。
_RUN_ID_RE = re.compile(r"^tts_[A-Za-z0-9_.-]+$")


def _run_dir(run_id: str) -> Path:
    return tts_root() / run_id


def run_dir(run_id: str) -> Path:
    """公开的 run 目录(对外服务产物用;非法 id 直接报错,不拼路径)。"""
    if not _RUN_ID_RE.fullmatch(str(run_id)) or ".." in str(run_id):
        raise AdhocTtsError(f"非法 run_id:{run_id}")
    return _run_dir(run_id)


def audio_file(run_id: str) -> Path:
    """某次合成的音频(``original/`` 下唯一一个文件)。"""
    directory = run_dir(run_id) / "original"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有语音产物:{run_id}")


# ---------- 记录列表与 manifest ----------


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    """最近的 Playground 语音记录(读 manifest.json,按时间倒序)。

    manifest 是唯一留痕(不落 runs 表),所以这里就是扫目录。
    """
    root = tts_root()
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
        has_audio = False
        if status == "ready":
            original = directory / "original"
            has_audio = original.is_dir() and any(original.iterdir())
        result = manifest.get("result") or {}
        records.append(
            {
                "run_id": directory.name,
                "created_at": manifest.get("created_at") or "",
                "backend": request.get("backend") or "openrouter",
                "text": request.get("text") or "",
                "model": result.get("model") or request.get("model") or "",
                "voice": result.get("voice") or "",
                "duration_s": result.get("duration_s"),
                "cost": result.get("cost"),
                "status": status or "failed",
                "error": manifest.get("error") or "",
                # 只查过(未终态)与失败的记录没有音频 → 前端别去取(否则 404)
                "has_audio": has_audio,
            }
        )
    records.sort(key=lambda r: r["run_id"], reverse=True)
    return records[: max(1, int(limit))]


def read_manifest(run_id: str) -> dict[str, Any]:
    """读某个 run 的 manifest。"""
    path = run_dir(run_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"没有 manifest:{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_manifest(req: TtsRequest, run_id: str, *, model: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": {
            "backend": req.backend,
            "text": req.text,
            "model": req.model,
            "voice": req.voice,
            "response_format": req.response_format,
            "out": str(req.out or ""),
        },
        "job": {"model": model},
        "status": "in_progress",
        "result": None,
        "error": "",
    }


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """精简摘要(URL 由 api 层拼,这里只给数据)。"""
    result = manifest.get("result") or {}
    request = manifest.get("request") or {}
    job = manifest.get("job") or {}
    return {
        "run_id": manifest.get("run_id") or "",
        "status": manifest.get("status") or "failed",
        "error": manifest.get("error") or "",
        "model": result.get("model") or job.get("model") or request.get("model") or "",
        "text": request.get("text") or "",
        "voice": result.get("voice") or "",
        "response_format": result.get("response_format") or "",
        "sample_rate": result.get("sample_rate"),
        "duration_s": result.get("duration_s"),
        "size_bytes": result.get("size_bytes") or 0,
        "cost": result.get("cost"),
        "duration_ms": result.get("duration_ms") or 0,
    }


# ---------- 校验 ----------


def validate(req: TtsRequest, model: str | None = None) -> None:
    """请求自检:能在发请求前发现的错就别浪费一次提交(付费模型提交即计费)。"""
    if not req.text or not req.text.strip():
        raise AdhocTtsError("要朗读的文本为空")
    model_id = (model or "").strip()
    if not model_id:
        model_id = openrouter_tts.resolve_model(req.request_model())
    # HeyGen 系按官方声明的单次 5000 字符上限提交前拦截(码点计);
    # 其他模型上限未知,交给 provider(400 的报错原样带出)
    if model_id.startswith(_HEYGEN_PREFIX):
        characters = len(req.text.strip())
        if characters > _TTS_MAX_CHARS:
            raise AdhocTtsError(
                f"文本 {_TTS_MAX_CHARS} 字符上限(HeyGen 单次合成硬上限),当前 {characters} 字符"
            )


def _conn(db_path: Path | None = None):
    from charvar import storage

    return storage.connect(db_path)


def _resolve_model(req: TtsRequest, db_path: Path | None = None) -> str:
    """模型默认链:请求显式 > 设置页(DB)> env(OP_TTS_MODEL)> 免费默认。"""
    with _conn(db_path) as conn:
        cfg = settings_service.openrouter_tts_config(conn)
    return (req.request_model() or "").strip() or str(cfg["model"]).strip()


# ---------- 时长与 --out ----------


def _audio_duration(destination: Path, ext: str) -> float | None:
    """音频时长(秒):wav 直接读 wave 头(精确);mp3 等用 ffprobe(失败 None)。"""
    if ext == "wav":
        try:
            with wave.open(str(destination), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or 0
            if rate <= 0:
                return None
            return round(frames / rate, 3)
        except (OSError, wave.Error, EOFError):  # noqa: BLE001 - 头坏了不算致命
            return None
    try:
        import shutil
        import subprocess

        from charvar.video import _ffmpeg_bin

        probe = Path(_ffmpeg_bin()).with_name("ffprobe")
        if not probe.exists():
            probe = shutil.which("ffprobe") or ""
        if not probe:
            return None
        cmd = [
            str(probe), "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(destination),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=30, check=True, text=True)
        return round(float(proc.stdout.strip()), 3)
    except Exception:  # noqa: BLE001 - 时长只是展示,失败不影响产物
        return None


def _write_out(result: TtsResult, out: Path | None) -> None:
    """``--out`` 指定路径时再落一份出去(音频本体不动,只是复制)。"""
    if out is None or not result.audio_path:
        return
    import shutil

    source = Path(result.audio_path)
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    result.audio_path = str(destination)


def _mark(run_dir: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """小工具:就地改 manifest 若干键并落盘。"""
    patch.setdefault("finalized_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    manifest.update(patch)
    _write_manifest(run_dir, manifest)
    return manifest


# ---------- 对外入口 ----------


def generate(req: TtsRequest, db_path: Path | None = None, *, timeout_s: float | None = None) -> TtsResult:
    """跑一次 Playground 语音合成,返回结果(含落盘路径与 manifest 路径)。

    同步一步到终态:调 ``openrouter_tts.generate`` → 落盘 → manifest 收尾,
    失败也留痕(manifest 记下失败原因方便回头查)。
    """
    model = _resolve_model(req, db_path)
    validate(req, model)
    run_id = _new_run_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    manifest = _new_manifest(req, run_id, model=model)
    manifest["submitted_at"] = started
    _write_manifest(run_dir, manifest)

    try:
        speech = openrouter_tts.generate(
            text=req.text,
            model=model,
            voice=req.request_voice(),
            response_format=req.request_format(),
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - 失败也要留痕(历史列表里能看见失败原因)
        message = str(exc)
        if isinstance(exc, _ORError):
            message = f"OpenRouter 语音合成失败:{message}"
        manifest = _mark(run_dir, manifest, status="failed", error=f"{type(exc).__name__}: {message}")
        raise AdhocTtsError(message) from exc

    destination = run_dir / "original" / f"000000.{speech.ext}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(speech.audio)
    result = TtsResult(
        run_id=run_id,
        audio_path=str(destination),
        provider=req.backend,
        manifest_path="",
        model=model,
        voice=str(speech.request_body.get("voice") or ""),
        response_format=str(speech.format_sent or ""),
        sample_rate=speech.sample_rate,
        duration_s=_audio_duration(destination, speech.ext),
        size_bytes=len(speech.audio),
        generation_id=speech.generation_id,
        cost=openrouter_tts.estimate_cost(model, len(req.text.strip())),
        duration_ms=int((time.time() - started) * 1000),
    )
    out = req.out
    _write_out(result, Path(out).expanduser() if out else None)
    result.manifest_path = str(run_dir / "manifest.json")
    manifest["result"] = result.model_dump()
    manifest = _mark(run_dir, manifest, status="ready", error="")
    return result


__all__ = [
    "AdhocTtsError",
    "TtsRequest",
    "TtsResult",
    "audio_file",
    "generate",
    "list_recent",
    "read_manifest",
    "run_dir",
    "tts_root",
    "validate",
]