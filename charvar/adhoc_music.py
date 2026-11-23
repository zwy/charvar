"""音乐:给一段提示词直接出一行歌曲(Lyria 3 等音乐生成模型)。

与语音(``charvar.adhoc_tts``)同构:网页与 CLI ``charvar music`` 是同一套参数语义、
同一套落盘、同一份 manifest;不进主体档案 6 槽位、不跑预设批量、不落 runs 表。

与语音的差异(音乐 API 是 **chat/completions 流式音频**):
- 请求是流式的:``stream: true`` 必开(官方:不带流式直接 400),SSE 分片的
  base64 音频按顺序拼接;所以也是**同步**一步到终态,web 没有 status 轮询
  (与 TTS 同型,但生成可能要几分钟,超时默认 600s 而不是 300s);
- 费用有两层:目录 description 的 ``$0.04 per clip`` / ``$0.08 per song`` 是预估
  (一口价按次计费,pricing.prompt 恒 0 不能按 token 算),流末 ``usage.cost``
  是**实收**;两者分开记(``cost`` 估 / ``cost_real`` 实)。
- 音频产物:``original/000000.mp3|wav|ogg|flac``(按魔数嗅探,Lyria 实测完整 MP3);
  裸流(pcm16 等)第一期不支持——openrouter_music 的 ParseError 直接拦。没有 thumbs。

落盘:``data/generated/adhoc_music/{id}/``(original/ + manifest.json)。manifest 记
完整请求与实收费用,便于复现与对账;失败也留痕。

红线(同 AGENTS.md / adhoc_tts):
- OpenRouter 请求一律走 ``charvar.openrouter_music`` 的客户端(``trust_env=False``,
  绕 shell 代理);
- API key 与生图同链路(``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``);
- 不引入随机源:seed 只透传调用方显式给的值(空 = 不发送);
- 不落 runs 表(临时性质,图片墙 / 历史都不会扫到,这是预期的)。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from charvar import openrouter_music, paths, settings_service
from charvar.openrouter_music import OpenRouterMusicError as _ORError
from charvar.adhoc_tts import _audio_duration as _shared_audio_duration

Backend = Literal["openrouter"]


class AdhocMusicError(ValueError):
    """参数或环境错误(API 层转 400,CLI 层转友好提示)。"""


class MusicRequest(BaseModel):
    """一次 音乐生成的请求。"""

    prompt: str
    backend: Backend = "openrouter"
    model: str | None = None  # OpenRouter slug;空 = 设置页/env 默认
    seed: str = ""  # 空 = 不发送;显式给才透传(目录声明 seed 的模型)
    out: Path | None = None

    def request_model(self) -> str:
        """请求里给的模型(空串/None = None,让上层走设置页链路)。"""
        return (self.model or "").strip() or None

    def request_seed(self) -> int | None:
        """请求里给的 seed(空串 = None = 不发送;非法整数 = 原样报 400)。"""
        raw = str(self.seed or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise AdhocMusicError(f"seed 必须是整数,收到:{raw!r}") from exc


class MusicResult(BaseModel):
    """一次 音乐生成的结果。"""

    run_id: str
    audio_path: str
    provider: Backend
    manifest_path: str = ""
    model: str | None = None
    seed_sent: int | None = None  # 实际发送的 seed(None = 没发)
    transcript: str = ""  # delta.audio.transcript 拼接(随模型,可空)
    text_content: str = ""  # delta.content 文本段落拼接(Lyria 曲目说明)
    ext: str = ""
    duration_s: float | None = None  # 音频时长(ffprobe;失败 None)
    size_bytes: int = 0
    cost: float | None = None  # 预估(目录 description 的一口价)
    cost_real: float | None = None  # 实收(流末 usage.cost)
    duration_ms: int = 0


# ---------- 落盘目录 ----------


def music_root() -> Path:
    """音乐根目录:``<data>/generated/adhoc_music/``。"""
    return paths.data_dir() / "generated" / "adhoc_music"


def _new_run_id() -> str:
    """本次生成的 id:只含 ``[A-Za-z0-9_.-]``。"""
    return f"music_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"


# run_id 必须是「music_ 前缀 + 安全字符集」。与 adhoc / tts / adhoc_video 同款:
# run_id 直接拼进路径,assets.safe_run_id 的正则放行 "..",这里必须自己拦。
_RUN_ID_RE = re.compile(r"^music_[A-Za-z0-9_.-]+$")


def run_dir(run_id: str) -> Path:
    """公开的 run 目录(对外服务产物用;非法 id 直接报错,不拼路径)。"""
    if not _RUN_ID_RE.fullmatch(str(run_id)) or ".." in str(run_id):
        raise AdhocMusicError(f"非法 run_id:{run_id}")
    return music_root() / str(run_id)


def audio_file(run_id: str) -> Path:
    """某次生成的音频(``original/`` 下唯一一个文件)。"""
    directory = run_dir(run_id) / "original"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有音频产物:{run_id}")


# ---------- 记录列表与 manifest ----------


def _recent_run_names() -> list[str]:
    """manifest 留痕的 run 目录名,时间倒序(run_id 自带时间前缀,倒序即时间倒序)。"""
    root = music_root()
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
    """manifest 留痕的 run 总数(「最近生成」翻页用)。"""
    return len(_recent_run_names())


def list_recent(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """最近的 音乐记录(读 manifest.json,按时间倒序)。"""
    root = music_root()
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
                "prompt": request.get("prompt") or "",
                "model": result.get("model") or request.get("model") or "",
                "duration_s": result.get("duration_s"),
                "cost": result.get("cost"),
                "cost_real": result.get("cost_real"),
                "status": status or "failed",
                "error": manifest.get("error") or "",
                # 只查过(未终态)与失败的记录没有音频 → 前端别去取(否则 404)
                "has_audio": has_audio,
            }
        )
    return records


def read_manifest(run_id: str) -> dict[str, Any]:
    """读某个 run 的 manifest。"""
    path = run_dir(run_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"没有 manifest:{run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def delete_run(run_id: str) -> None:
    """删掉一个 run 的产物目录(音频/manifest 一并)。非法 id 报错。"""
    directory = run_dir(run_id)
    if not directory.is_dir():
        raise FileNotFoundError(f"没有记录:{run_id}")
    shutil.rmtree(directory)


def _write_manifest(run_dir_path: Path, manifest: dict[str, Any]) -> None:
    path = run_dir_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_manifest(req: MusicRequest, run_id: str, *, model: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": {
            "backend": req.backend,
            "prompt": req.prompt,
            "model": req.model,
            "seed": req.seed,
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
        "prompt": request.get("prompt") or "",
        "seed_sent": result.get("seed_sent"),
        "transcript": result.get("transcript") or "",
        "text_content": result.get("text_content") or "",
        "ext": result.get("ext") or "",
        "duration_s": result.get("duration_s"),
        "size_bytes": result.get("size_bytes") or 0,
        "cost": result.get("cost"),
        "cost_real": result.get("cost_real"),
        "duration_ms": result.get("duration_ms") or 0,
    }


# ---------- 校验与模型链 ----------


def validate(req: MusicRequest, model: str | None = None) -> None:
    """请求自检:能在发请求前发现的错就别浪费一次提交(提交即计费)。"""
    if not req.prompt or not req.prompt.strip():
        raise AdhocMusicError("提示词(prompt)为空")
    model_id = (model or "").strip()
    if not model_id:
        model_id = openrouter_music.resolve_model(req.request_model())
    if not model_id:
        raise AdhocMusicError("解析不出生效的音乐模型(检查设置页 / OP_MUSIC_MODEL)")
    # seed 提前验型(非法整数在这里拦,不浪费一次流式提交)
    req.request_seed()


def _conn(db_path: Path | None = None):
    from charvar import storage

    return storage.connect(db_path)


def _resolve_model(req: MusicRequest, db_path: Path | None = None) -> str:
    """模型默认链:请求显式 > 设置页(DB)> env(OP_MUSIC_MODEL)> 缺省 lyria-3-clip。"""
    with _conn(db_path) as conn:
        cfg = settings_service.openrouter_music_config(conn)
    return (req.request_model() or "").strip() or str(cfg["model"]).strip()


# ---------- --out 与收尾 ----------


def _write_out(result: MusicResult, out: Path | None) -> None:
    """``--out`` 指定路径时再落一份出去(音频本体不动,只是复制)。"""
    if out is None or not result.audio_path:
        return
    source = Path(result.audio_path)
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    result.audio_path = str(destination)


def _mark(run_dir_path: Path, manifest: dict[str, Any], **patch: Any) -> dict[str, Any]:
    """小工具:就地改 manifest 若干键并落盘。"""
    patch.setdefault("finalized_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    manifest.update(patch)
    _write_manifest(run_dir_path, manifest)
    return manifest


# ---------- 对外入口 ----------


def generate(req: MusicRequest, db_path: Path | None = None, *, timeout_s: float | None = None) -> MusicResult:
    """跑一次音乐生成,返回结果(含落盘路径与 manifest 路径)。

    同步一步到终态:调 ``openrouter_music.generate`` → 落盘 → manifest 收尾,
    失败也留痕(manifest 记下失败原因方便回头查)。整曲生成可能要几分钟,
    请求超时默认 600s(客户端层)。
    """
    model = _resolve_model(req, db_path)
    validate(req, model)
    seed = req.request_seed()
    run_id = _new_run_id()
    run_dir_path = music_root() / run_id
    run_dir_path.mkdir(parents=True, exist_ok=True)
    started = time.time()

    manifest = _new_manifest(req, run_id, model=model)
    manifest["submitted_at"] = started
    _write_manifest(run_dir_path, manifest)

    with _conn(db_path) as conn:
        creds = settings_service.openrouter_credentials(conn)
    try:
        music = openrouter_music.generate(
            prompt=req.prompt,
            model=model,
            seed=seed,
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - 失败也要留痕(历史列表里能看见失败原因)
        message = str(exc)
        if isinstance(exc, _ORError):
            message = f"OpenRouter 音乐生成失败:{message}"
        manifest = _mark(run_dir_path, manifest, status="failed", error=f"{type(exc).__name__}: {message}")
        raise AdhocMusicError(message) from exc

    ext = music.ext
    destination = run_dir_path / "original" / f"000000.{ext}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(music.audio)
    result = MusicResult(
        run_id=run_id,
        audio_path=str(destination),
        provider=req.backend,
        manifest_path="",
        model=model,
        seed_sent=music.seed_sent,
        transcript=music.transcript,
        text_content=music.text_content,
        ext=ext,
        duration_s=_shared_audio_duration(destination, ext),
        size_bytes=len(music.audio),
        cost=openrouter_music.estimate_cost(model),
        cost_real=music.usage_cost,
        duration_ms=int((time.time() - started) * 1000),
    )
    out = req.out
    _write_out(result, Path(out).expanduser() if out else None)
    result.manifest_path = str(run_dir_path / "manifest.json")
    manifest["result"] = result.model_dump()
    manifest = _mark(run_dir_path, manifest, status="ready", error="")
    return result


__all__ = [
    "AdhocMusicError",
    "MusicRequest",
    "MusicResult",
    "audio_file",
    "generate",
    "list_recent",
    "music_root",
    "read_manifest",
    "run_dir",
    "validate",
]
