"""OpenRouter 视频生成客户端（镜像 ``openrouter_image`` 的骨架，语义按官方视频 API）。

与生图（``openrouter_image``）的差异：

- 作业是**异步**的：``POST /api/v1/videos`` 提交后立刻返回 202 + job id，
  要轮询 ``GET /api/v1/videos/{id}`` 到 completed / failed，再从
  ``GET /api/v1/videos/{id}/content`` 下载成片；
- 画幅不讲 ``size`` 像素，讲 ``resolution`` 档（480p/768p/2K…）+ ``aspect_ratio``，
  校验依据是 ``GET /api/v1/videos/models`` 目录里每模型的 supported_* 枚举
  （超枚举是 400，白扣一次提交 —— 尽量在提交前拦截）；
- 图参与两种方式：``frame_images``（首帧/末帧，图生视频）、``input_references``
  （参考图，仅供参考）；同给时 frame_images 优先（官方文档语义）。
- 与生图一致的红线：httpx ``trust_env=False``（shell 的 ALL_PROXY 会劫持地址）、
  seed 收敛到 2^53-1、API key 复用 ``openrouter_image.resolve_api_key``
  （``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``，同一家 OpenRouter）。

模型目录缓存在 ``data/openrouter/video-models.json``——与生图的
``api-response.json`` 是两个端点、两份缓存。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from charvar import openrouter_image
from charvar.openrouter_image import (
    _cache_lock,
    _env,
    _read_cache,
    _write_cache_atomic,
)

logger = logging.getLogger(__name__)

_BASE_URL = openrouter_image._BASE_URL
_CREATE_URL = f"{_BASE_URL}/videos"
_LIST_MODELS_URL = f"{_BASE_URL}/videos/models"

_DEFAULT_MODEL = "heygen/heygen-video-1"
_DEFAULT_TIMEOUT_S = 300.0
_LIST_MODELS_TIMEOUT_S = 60.0

# input_references 上限与生图一致（openrouter_image._MAX_REFERENCES）
_MAX_REFERENCES = 4

_TERMINAL_OK = "completed"
_TERMINAL_FAIL = ("failed", "cancelled", "expired")  # 官方 202 语义：cancelled/expired 也是终态


class OpenRouterVideoError(RuntimeError):
    """OpenRouter 视频作业失败（基类）。"""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class OpenRouterVideoAuthError(OpenRouterVideoError):
    """鉴权 / 余额类失败（401 / 402 / 非内容策略的 403）。"""


class OpenRouterVideoFetchError(OpenRouterVideoError):
    """网络层失败，或上游 provider 用 403 拒绝了违规 prompt。"""


class OpenRouterVideoRateLimitError(OpenRouterVideoError):
    """限流（429）。"""


class OpenRouterVideoNotFoundError(OpenRouterVideoError):
    """资源不存在（404，通常是 model slug 或 job id 写错）。"""


class OpenRouterVideoParseError(OpenRouterVideoError):
    """响应不是合法 JSON，或缺 id / status 等关键结构。"""


class OpenRouterVideoParamError(OpenRouterVideoError):
    """入参 / 配置校验失败（提交前就该拦下的那类）。"""


# ---------- 配置 ----------


def resolve_model(model: str | None = None) -> str:
    """生效的模型 slug：显式传入 > env ``OP_VIDEO_MODEL`` > 默认 heygen-video-1。

    设置页的 DB 值由上层（``settings_service.openrouter_video_config``）先合并进
    ``model`` 再调这里，本函数只收「已经过 DB 一层」的候选。
    """
    for candidate in (model, _env("OP_VIDEO_MODEL")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    return _DEFAULT_MODEL


def resolve_api_key(api_key: str | None = None) -> str:
    """API key 与生图同链路（同一家 OpenRouter）：显式 > OP_IMAGE_API_KEY > LLM_API_KEY。"""
    return openrouter_image.resolve_api_key(api_key)


def resolve_timeout(timeout_s: float | None = None) -> float:
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("OP_VIDEO_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def models_cache_path() -> Any:
    """视频模型目录缓存：``{CHARVAR_DATA_DIR}/openrouter/video-models.json``。"""
    from charvar import paths

    return paths.data_dir() / "openrouter" / "video-models.json"


# ---------- HTTP ----------


def _http_client(timeout: float) -> httpx.Client:
    # trust_env=False：忽略 shell 的 ALL_PROXY 等环境代理（与 openrouter_image 同一策略）
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(timeout, write=60.0))


def _error_from_response(resp: httpx.Response) -> OpenRouterVideoError:
    """把非 2xx 响应映射成对应异常，message 携带上游 error.message。"""
    try:
        body = resp.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        message = None
    if not message:
        message = resp.text[:300] or f"HTTP {resp.status_code}"

    if resp.status_code == 403 and openrouter_image._is_provider_policy_rejection(str(message)):
        return OpenRouterVideoFetchError(
            f"上游 model provider 拒绝 prompt（HTTP 403 内容策略）：{message}",
            http_status=403,
        )
    if resp.status_code in (401, 402, 403):
        return OpenRouterVideoAuthError(
            f"OpenRouter 鉴权/余额失败（HTTP {resp.status_code}）：{message}",
            http_status=resp.status_code,
        )
    if resp.status_code == 429:
        return OpenRouterVideoRateLimitError(
            f"OpenRouter 限流（HTTP 429）：{message}", http_status=429
        )
    if resp.status_code == 404:
        return OpenRouterVideoNotFoundError(
            f"OpenRouter 资源不存在（HTTP 404）：{message}", http_status=404
        )
    return OpenRouterVideoFetchError(
        f"OpenRouter API 返回 HTTP {resp.status_code}：{message}",
        http_status=resp.status_code,
    )


# ---------- 模型目录 ----------


def list_models(*, force_refresh: bool = False, api_key: str | None = None) -> dict:
    """读 ``GET /api/v1/videos/models``（本地缓存优先，无则拉取并落盘）。

    与生图共用锁与原子写逻辑（``openrouter_image``）：冷缓存并发只拉一次。
    """
    cache_path = models_cache_path()
    if not force_refresh:
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached

    with _cache_lock:
        if not force_refresh:
            cached = _read_cache(cache_path)
            if cached is not None:
                return cached
        headers = {"Accept": "application/json"}
        key = resolve_api_key(api_key)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            with _http_client(_LIST_MODELS_TIMEOUT_S) as client:
                resp = client.get(_LIST_MODELS_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise OpenRouterVideoFetchError(f"请求 OpenRouter 视频模型目录失败：{exc}") from exc
        if resp.status_code != 200:
            raise _error_from_response(resp)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterVideoParseError(f"视频模型目录响应不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise OpenRouterVideoParseError("视频模型目录响应顶层不是 dict")
        _write_cache_atomic(cache_path, data)
        return data


def video_model_entry(model_id: str) -> dict[str, Any]:
    """目录条目（含 supported_* 枚举与 pricing_skus）；未知模型返空 dict。"""
    try:
        data = list_models()
    except OpenRouterVideoError:
        return {}
    for item in data.get("data") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == model_id:
            return item
    return {}


def model_choices() -> list[dict[str, str]]:
    """ 目录精简成 ``[{id, name}]``，供下拉。"""
    data = list_models()
    out: list[dict[str, str]] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        out.append({"id": model_id, "name": str(item.get("name") or model_id)})
    return out


def _str_list(entry: dict[str, Any], key: str) -> tuple[str, ...]:
    values = entry.get(key)
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values)


def _int_list(entry: dict[str, Any], key: str) -> tuple[int, ...]:
    values = entry.get(key)
    if not isinstance(values, list):
        return ()
    out: list[int] = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def supported_durations(entry: dict[str, Any]) -> tuple[int, ...]:
    """时长档（秒，整数）；未知返空（空 = 提交前不校验，交给 provider/降级）。"""
    return _int_list(entry, "supported_durations")


def supports_first_frame(entry: dict[str, Any]) -> bool:
    frame_types = _str_list(entry, "supported_frame_images")
    return not frame_types or "first_frame" in frame_types


def supports_last_frame(entry: dict[str, Any]) -> bool:
    frame_types = _str_list(entry, "supported_frame_images")
    return not frame_types or "last_frame" in frame_types


# ---------- 视频参考（input_references 里的 video_url；Seedance gen2+ 才接） ----------

_VIDEO_MIME = {
    "mp4": "video/mp4",
    "mov": "video/mov",
    "webm": "video/webm",
    "mpeg": "video/mpeg",
    "mpg": "video/mpeg",
}


def sniff_video_ref(path: Path) -> str:
    """参考视频文件的容器格式 → mime（官方仅收 mp4/mpeg/mov/webm）。

    按魔数判，不按后缀（后缀骗人的例子生图侧已经踩过）：MP4/MOV 是 ftyp 盒子，
    MKV/WebM 是 EBML 魔数。
    """
    head = path.read_bytes()[:12]
    if len(head) >= 8 and head[4:8] == b"ftyp":
        brand = bytes(head[8:12]).lower()
        return "video/mov" if brand.startswith(b"qt") else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    raise OpenRouterVideoParamError(
        f"参考视频不是可识别的容器（mp4/mov/mpeg/webm）：{path.name}"
    )


def encode_video_reference(path: Any) -> str:
    """参考视频 → data URL（本地文件；远程 URL 由调用方直接当 url 传）。"""
    source = Path(path)
    if not source.exists():
        raise OpenRouterVideoParamError(f"参考视频不存在：{source}")
    mime = sniff_video_ref(source)
    import base64

    return f"data:{mime};base64,{base64.b64encode(source.read_bytes()).decode('ascii')}"


# ---------- 请求体 ----------


def build_body(
    *,
    prompt: str,
    model: str,
    duration: int | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    seed: int | None = None,
    first_frame_path: Any = None,
    last_frame_path: Any = None,
    reference_paths: list[Any] | None = None,
    reference_videos: list[Any] | None = None,
) -> dict[str, Any]:
    """组装 ``POST /api/v1/videos`` 请求体；提交前按目录枚举校验（超枚举 400 白扣钱）。

    - duration：正整数，且在模型 ``supported_durations`` 内（目录没收录的模型不拦）；
    - resolution / aspect_ratio：值在模型枚举内才发，枚举外直接 ParamError；
    - seed：目录标 ``seed: false`` 的模型不发（如 avatar-iv）；目录没收录的模型
      照发，被 provider 拒收时由 :func:`submit` 自动降级；
    - first_frame_path / last_frame_path：成对或单独的首帧/尾帧图 →
      ``frame_images``（frame_type=first_frame/last_frame）；模型声明了
      supported_frame_images 且不含对应档 → ParamError；
    - reference_paths / reference_videos：``input_references``（与首帧同给时
      frame_images 优先，这里不拦，由 provider 语义裁决）。图按 data URL 发；
      视频（Seedance gen2+ 才接）按 magic 嗅探容器后编码进 video_url data URL。
    """
    if not prompt or not prompt.strip():
        raise OpenRouterVideoParamError("prompt 不能为空")
    if not model or not str(model).strip():
        raise OpenRouterVideoParamError(
            "model 不能为空（设置 OP_VIDEO_MODEL 或显式传入）"
        )

    body: dict[str, Any] = {"model": model, "prompt": prompt.strip()}
    entry = video_model_entry(model)

    if duration is not None:
        try:
            duration_value = int(duration)
        except (TypeError, ValueError) as exc:
            raise OpenRouterVideoParamError(f"时长必须是整数秒：{duration!r}") from exc
        if duration_value <= 0:
            raise OpenRouterVideoParamError(f"时长必须为正整数秒：{duration_value}")
        allowed = supported_durations(entry)
        if allowed and duration_value not in allowed:
            raise OpenRouterVideoParamError(
                f"模型 {model} 不支持时长 {duration_value}s（支持 {'/'.join(map(str, allowed))}）"
            )
        body["duration"] = duration_value

    resolutions = _str_list(entry, "supported_resolutions")
    if resolution is not None and str(resolution).strip():
        value = str(resolution).strip()
        if resolutions and value not in resolutions:
            raise OpenRouterVideoParamError(
                f"模型 {model} 不支持分辨率 {value}（支持 {'/'.join(resolutions)}）"
            )
        body["resolution"] = value

    aspects = _str_list(entry, "supported_aspect_ratios")
    if aspect_ratio is not None and str(aspect_ratio).strip():
        value = str(aspect_ratio).strip()
        if aspects and value not in aspects:
            raise OpenRouterVideoParamError(
                f"模型 {model} 不支持画幅 {value}（支持 {'/'.join(aspects)}）"
            )
        body["aspect_ratio"] = value

    if seed is not None and (not entry or entry.get("seed")):
        body["seed"] = openrouter_image.normalize_seed(seed)

    if first_frame_path is not None or last_frame_path is not None:
        if first_frame_path is not None and not supports_first_frame(entry):
            raise OpenRouterVideoParamError(
                f"模型 {model} 不支持首帧图（图生视频），请去掉首帧或换模型"
            )
        if last_frame_path is not None and not supports_last_frame(entry):
            raise OpenRouterVideoParamError(
                f"模型 {model} 不支持尾帧图（last_frame），请去掉尾帧或换模型"
            )
        frames: list[dict[str, Any]] = []
        if first_frame_path is not None:
            frames.append(
                {
                    "type": "image_url",
                    "image_url": {"url": openrouter_image.encode_reference(first_frame_path)},
                    "frame_type": "first_frame",
                }
            )
        if last_frame_path is not None:
            frames.append(
                {
                    "type": "image_url",
                    "image_url": {"url": openrouter_image.encode_reference(last_frame_path)},
                    "frame_type": "last_frame",
                }
            )
        body["frame_images"] = frames

    parts: list[dict[str, Any]] = []
    for p in list(reference_paths or [])[:_MAX_REFERENCES]:
        parts.append({"type": "image_url", "image_url": {"url": openrouter_image.encode_reference(p)}})
    for v in list(reference_videos or []):
        parts.append({"type": "video_url", "video_url": {"url": encode_video_reference(v)}})
    if len(parts) > _MAX_REFERENCES:
        raise OpenRouterVideoParamError(
            f"input_references 最多 {_MAX_REFERENCES} 项（图+视频合计），收到 {len(parts)}"
        )
    if parts:
        body["input_references"] = parts
    return body


# ---------- 作业（submit / poll / download） ----------


@dataclass
class VideoJob:
    """一次提交返回的作业句柄。"""

    job_id: str
    status: str
    polling_url: str = ""


def _extract_job(payload: dict[str, Any]) -> VideoJob:
    job_id = str(payload.get("id") or "").strip()
    if not job_id:
        raise OpenRouterVideoParseError(
            f"提交响应缺 id：{json.dumps(payload, ensure_ascii=False)[:300]}"
        )
    return VideoJob(
        job_id=job_id,
        status=str(payload.get("status") or "pending"),
        polling_url=str(payload.get("polling_url") or ""),
    )


# 提交后被 provider 拒了可以摘掉重试的参数（画幅枚举这些提交前已校验，
# 这里只兜「目录没收录的参数」：seed / duration / frame_images / input_references）。
_DEGRADABLE = ("seed", "duration", "resolution", "aspect_ratio", "frame_images", "input_references")


def submit(body: dict[str, Any], *, api_key: str | None = None, timeout_s: float | None = None) -> VideoJob:
    """提交视频作业；provider 拒了某个参数就摘掉重试（自动降级）。

    与生图 :meth:`openrouter_image._post_generate` 同思路：只对「参数不被接受」
    降级，鉴权/限流/内容策略直接抛。视频没有独立的能力缓存文件
    （生图的 capabilities.json 只记生图参数），这里当场降级不记档。
    """
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterVideoParamError(
            "缺少 OpenRouter API Key：请设置 OP_IMAGE_API_KEY / LLM_API_KEY，或在「设置」页配置"
        )
    current = dict(body)
    tried: set[str] = set()
    while True:
        try:
            with _http_client(resolve_timeout(timeout_s)) as client:
                resp = client.post(
                    _CREATE_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=current,
                )
        except httpx.HTTPError as exc:
            raise OpenRouterVideoFetchError(f"请求 OpenRouter 视频提交失败：{exc}") from exc
        if resp.status_code in (200, 201, 202):
            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise OpenRouterVideoParseError(f"提交响应不是合法 JSON：{exc}") from exc
            if not isinstance(payload, dict):
                raise OpenRouterVideoParseError("提交响应顶层不是 dict")
            return _extract_job(payload)

        error = _error_from_response(resp)
        if not isinstance(error, OpenRouterVideoFetchError):
            raise error
        rejected = openrouter_image._rejected_parameter(str(error))
        if not rejected or rejected not in _DEGRADABLE or rejected in tried:
            raise error
        tried.add(rejected)
        logger.info("openrouter_video.param_degraded | model=%s | rejected=%s", current.get("model"), rejected)
        current.pop(rejected, None)
        if not current:
            raise error


def poll(job_id: str, *, api_key: str | None = None, timeout_s: float | None = None) -> dict[str, Any]:
    """轮询一次作业状态：返回原 payload（含 status / error? / usage? / unsigned_urls?）。"""
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterVideoParamError("缺少 OpenRouter API Key")
    if not str(job_id or "").strip():
        raise OpenRouterVideoParamError("job_id 不能为空")
    try:
        with _http_client(resolve_timeout(timeout_s)) as client:
            resp = client.get(f"{_CREATE_URL}/{job_id}", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise OpenRouterVideoFetchError(f"请求 OpenRouter 视频状态失败：{exc}") from exc
    if resp.status_code != 200:
        raise _error_from_response(resp)
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise OpenRouterVideoParseError(f"状态响应不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or not str(payload.get("status") or "").strip():
        raise OpenRouterVideoParseError(f"状态响应缺 status：{json.dumps(payload, ensure_ascii=False)[:200]}")
    return payload


def wait_for_job(
    job_id: str,
    *,
    api_key: str | None = None,
    poll_interval: float = 5.0,
    timeout_s: float = 900.0,
    sleep: Any = time.sleep,
    timeout_error: type[Exception] | None = None,
) -> dict[str, Any]:
    """轮询到终态（completed / failed）返回最终 payload。

    ``sleep`` 参数给测试替换；``timeout_error`` 给调用方（adhoc_video）换成自己的异常类型。
    """
    deadline = time.monotonic() + max(1.0, timeout_s)
    while True:
        payload = poll(job_id, api_key=api_key)
        status = str(payload.get("status") or "")
        if status in (_TERMINAL_OK, *_TERMINAL_FAIL):
            return payload
        if time.monotonic() >= deadline:
            name = timeout_error or TimeoutError
            raise name(f"视频作业 {job_id} 超时未完成（>{int(timeout_s)}s，状态 {status}）")
        sleep(max(0.5, poll_interval))


def download(job_id: str, *, url: str | None = None, api_key: str | None = None, timeout_s: float | None = None) -> bytes:
    """下载成片字节（未签名 URL 也要带 Authorization：与轮询同一把 key）。"""
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterVideoParamError("缺少 OpenRouter API Key")
    if not str(job_id or "").strip() and not url:
        raise OpenRouterVideoParamError("job_id 不能为空")
    target = url or f"{_CREATE_URL}/{job_id}/content?index=0"
    try:
        with _http_client(resolve_timeout(timeout_s)) as client:
            resp = client.get(target, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise OpenRouterVideoFetchError(f"下载视频失败：{exc}") from exc
    if resp.status_code != 200:
        raise _error_from_response(resp)
    if not resp.content:
        raise OpenRouterVideoParseError("视频内容为空")
    return resp.content


# ---------- 费用预估 ----------


def _sku_price(skus: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        value = skus.get(k)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def estimate_cost(
    entry: dict[str, Any],
    *,
    seconds: float,
    resolution: str | None = None,
    n_refs: int = 0,
) -> float | None:
    """按 ``pricing_skus`` 估算一条片子的钱（美元）；算不出返 None。

    目录里认识两套键名（只做展示预估，不参与提交校验）：
    - heygen 系：``duration_seconds_<分辨率小写>``（美元/秒）；带参考图换
      ``reference_duration_seconds_<分辨率小写>``，另加每张 ``reference_input_images``；
    - grok 系：``cents_per_video_output_second_<分辨率小写>``（**美分**/秒），
      输入图统一 ``cents_per_image_input``（美分/张）。
    其它写法（如 avatar-iv 的 ``duration_seconds`` 总价）模糊兜底：找到任何
    以 duration 开头的 SKU 按该值 × 秒数近似。
    """
    skus = entry.get("pricing_skus")
    if not isinstance(skus, dict) or seconds <= 0:
        return None
    res = str(resolution or "").strip().lower()

    def price(prefix: str) -> float | None:
        candidates = [f"{prefix}_{res}"] if res else []
        # 无分辨率后缀的 SKU 兜底（如 avatar-iv 的 duration_seconds）
        candidates += [k for k in skus if str(k).startswith(prefix)]
        return _sku_price(skus, *candidates)

    def dollars_per_second() -> float | None:
        """美元/秒：先 heygen 系键名，再 grok 系（美分 → 除 100）。"""
        rate = price("reference_duration_seconds") if n_refs > 0 else None
        rate = rate or price("duration_seconds")
        if rate is not None:
            return rate
        cents = price("cents_per_video_output_second")
        return cents / 100 if cents is not None else None

    per_second = dollars_per_second()
    if per_second is None:
        return None
    cost = per_second * seconds
    if n_refs > 0:
        per_image = _sku_price(skus, "reference_input_images")
        if per_image is None:
            cents_image = _sku_price(skus, "cents_per_image_input")
            per_image = cents_image / 100 if cents_image is not None else 0.0
        cost += per_image * n_refs
    return round(cost, 4)


def sniff_video_ext(data: bytes) -> str:
    """按魔数嗅探视频容器，返回扩展名（mp4/webm/mov/bin）。"""
    # MP4/MOV：偏移 4 起是 ftyp 盒子；MOV 常见 brand qt
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = bytes(data[8:12]).lower()
        return "mov" if brand.startswith(b"qt") else "mp4"
    # MKV/WebM：EBML 魔数
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return "bin"