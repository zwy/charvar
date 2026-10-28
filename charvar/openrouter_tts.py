"""OpenRouter 语音合成(TTS)客户端(镜像 ``openrouter_video`` 的骨架,语义按官方 TTS API)。

API 形态(2026-10 实测 + 官方文档,与生图/视频的两条通路都不同):

- 端点 ``POST /api/v1/audio/speech``,OpenAI Audio Speech API 兼容,**同步**返回
  原始音频字节流(不是 JSON)—— 更像生图的同步模式,没有视频那套异步作业轮询;
- 模型目录没有独立端点(``/audio/models`` 是 404),走标准
  ``GET /api/v1/models?output_modalities=speech``,每模型条目带 ``supported_voices``
  枚举与 ``pricing.prompt``($/字符 ≈ $/token)。缓存 ``data/openrouter/speech-models.json``
  (与生图 ``api-response.json`` / 视频 ``video-models.json`` 是三份独立缓存);
- ``response_format`` 只有 ``mp3`` / ``pcm`` 两档:HeyGen 只出 ``pcm``、MiniMax 只出
  ``mp3``、给错格式是 400 —— 本家静态表 + provider 拒收时按错误话术里的合法值
  自动换值重试并记 ``data/openrouter/tts-capabilities.json``(与生图 capabilities.json
  同哲学,但这里是值级缓存,独立建文件,不混用);
- **voice 实测必填**(heygen/minimax 不带 voice 一律 400):目录枚举非空时自动
  兜底第一项(与视频页「枚举默认取第一项」同款);枚举为空的模型(fish/seed)
  不发送,交给 provider 裁决。

与生图/视频一致的红线:httpx ``trust_env=False``(shell 的 ALL_PROXY 会劫持地址)、
API key 复用 ``openrouter_image.resolve_api_key``(同一家 OpenRouter)、
目录缓存冷启动并发只拉一次。
"""
from __future__ import annotations

import io
import json
import logging
import re
import wave
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
_CREATE_URL = f"{_BASE_URL}/audio/speech"
_LIST_MODELS_URL = f"{_BASE_URL}/models"
_LIST_MODELS_QUERY = "output_modalities=speech"

_DEFAULT_MODEL = "heygen/heygen-voice-1-turbo:free"
_DEFAULT_TIMEOUT_S = 300.0
_LIST_MODELS_TIMEOUT_S = 60.0

# HeyGen 官方单次合成上限(字符);只有模型 id 以 heygen/ 开头的才在 adhoc_tts 预检,
# 其他模型的各自上限未知 → 交给 provider(400 原样报)。
_HEYGEN_MAX_CHARS = 5000

# provider 明文声明的格式约束(官方文档:HeyGen/Gemini TTS 只出 pcm,MiniMax 只出
# mp3)。只是「不发探测一发」的初值优化:探测缓存 > 静态表 > 不发(API 默认 pcm)。
_STATIC_FORMAT_MAP = (("heygen/", "pcm"), ("minimax/", "mp3"), ("google/", "pcm"))


class OpenRouterTtsError(RuntimeError):
    """OpenRouter TTS 失败(基类)。"""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class OpenRouterTtsAuthError(OpenRouterTtsError):
    """鉴权 / 余额类失败(401 / 402 / 非内容策略的 403)。"""


class OpenRouterTtsFetchError(OpenRouterTtsError):
    """网络层失败,或上游 provider 用 403 拒绝了违规文本。"""


class OpenRouterTtsRateLimitError(OpenRouterTtsError):
    """限流(429)。"""


class OpenRouterTtsNotFoundError(OpenRouterTtsError):
    """资源不存在(404:模型 slug / voice 写错)。"""


class OpenRouterTtsParseError(OpenRouterTtsError):
    """响应不是音频字节流,或缺 rate/voice 等关键结构。"""


class OpenRouterTtsParamError(OpenRouterTtsError):
    """入参 / 配置校验失败(提交前就该拦下的那类)。"""


# ---------- 配置 ----------


def resolve_model(model: str | None = None) -> str:
    """生效的模型 slug:显式传入 > env ``OP_TTS_MODEL`` > 默认 heygen-voice-1-turbo:free。

    设置页的 DB 值由上层(settings_service.openrouter_tts_config)先合并进
    ``model`` 再调这里,本函数只收「已经过 DB 一层」的候选。
    """
    for candidate in (model, _env("OP_TTS_MODEL")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    return _DEFAULT_MODEL


def resolve_api_key(api_key: str | None = None) -> str:
    """API key 与生图/视频同链路(同一家 OpenRouter):显式 > OP_IMAGE_API_KEY > LLM_API_KEY。"""
    return openrouter_image.resolve_api_key(api_key)


def resolve_timeout(timeout_s: float | None = None) -> float:
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("OP_TTS_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def models_cache_path() -> Path:
    """语音模型目录缓存:``{CHARVAR_DATA_DIR}/openrouter/speech-models.json``。"""
    from charvar import paths

    return paths.data_dir() / "openrouter" / "speech-models.json"


def capabilities_cache_path() -> Path:
    """探测出来各模型实际接受的 response_format:``data/openrouter/tts-capabilities.json``。

    值级缓存(生图 capabilities.json 是参数名级语义,不能混用):
    ``{model_id: {"response_format": "mp3"}}`` —— provider 用错误话术明示了
    只吃哪个格式,实测后记档,下次同一模型直接发对。
    """
    from charvar import paths

    return paths.data_dir() / "openrouter" / "tts-capabilities.json"


# ---------- HTTP ----------


def _http_client(timeout: float) -> httpx.Client:
    # trust_env=False:忽略 shell 的 ALL_PROXY 等环境代理(与 openrouter_image 同一策略)
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(timeout, write=60.0))


def _error_from_response(resp: httpx.Response) -> OpenRouterTtsError:
    """把非 2xx 响应映射成对应异常,message 携带上游 error.message。"""
    try:
        body = resp.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        message = None
    if not message:
        message = resp.text[:300] or f"HTTP {resp.status_code}"

    if resp.status_code == 403 and openrouter_image._is_provider_policy_rejection(str(message)):
        return OpenRouterTtsFetchError(
            f"上游 model provider 拒绝文本(HTTP 403 内容策略):{message}",
            http_status=403,
        )
    if resp.status_code in (401, 402, 403):
        return OpenRouterTtsAuthError(
            f"OpenRouter 鉴权/余额失败(HTTP {resp.status_code}):{message}",
            http_status=resp.status_code,
        )
    if resp.status_code == 429:
        return OpenRouterTtsRateLimitError(
            f"OpenRouter 限流(HTTP 429):{message}", http_status=429
        )
    if resp.status_code == 404:
        return OpenRouterTtsNotFoundError(
            f"OpenRouter 资源不存在(HTTP 404,通常是 model slug 或 voice 写错):{message}",
            http_status=404,
        )
    return OpenRouterTtsFetchError(
        f"OpenRouter API 返回 HTTP {resp.status_code}:{message}",
        http_status=resp.status_code,
    )


# ---------- 模型目录 ----------


def list_models(*, force_refresh: bool = False, api_key: str | None = None) -> dict:
    """读 ``GET /api/v1/models?output_modalities=speech``(本地缓存优先,无则拉取并落盘)。

    与生图/视频共用锁与原子写逻辑(openrouter_image):冷缓存并发只拉一次。
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
                resp = client.get(f"{_LIST_MODELS_URL}?{_LIST_MODELS_QUERY}", headers=headers)
        except httpx.HTTPError as exc:
            raise OpenRouterTtsFetchError(f"请求 OpenRouter 语音模型目录失败:{exc}") from exc
        if resp.status_code != 200:
            raise _error_from_response(resp)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterTtsParseError(f"语音模型目录响应不是合法 JSON:{exc}") from exc
        if not isinstance(data, dict):
            raise OpenRouterTtsParseError("语音模型目录响应顶层不是 dict")
        _write_cache_atomic(cache_path, data)
        return data


def speech_model_entry(model_id: str) -> dict[str, Any]:
    """目录条目(含 supported_voices / pricing);未知模型返空 dict。"""
    try:
        data = list_models()
    except OpenRouterTtsError:
        return {}
    for item in data.get("data") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == model_id:
            return item
    return {}


def model_choices() -> list[dict[str, str]]:
    """目录精简成 ``[{id, name}]``,供下拉。"""
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


def supported_voices(entry: dict[str, Any]) -> tuple[str, ...]:
    """voice 枚举;未知(目录没收录或没声明)返空 —— 空 = 提交前不校验,交给 provider。"""
    values = entry.get("supported_voices")
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values)


# ---------- 格式协商(tts-capabilities.json) ----------


def _read_format_cache() -> dict[str, Any]:
    cached = _read_cache(capabilities_cache_path())
    return cached if isinstance(cached, dict) else {}


def _record_format(model_id: str, fmt: str) -> None:
    """记一次探测结果:该模型实际接受的 response_format(下次直接发对)。"""
    if fmt not in ("pcm", "mp3"):
        return
    with _cache_lock:
        cache = _read_format_cache()
        entry = cache.get(model_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["response_format"] = fmt
        cache[model_id] = entry
        try:
            _write_cache_atomic(capabilities_cache_path(), cache)
        except OSError as exc:  # noqa: BLE001 - 缓存写失败不影响生成
            logger.warning("openrouter_tts.capability_cache_write_failed | err=%s", exc)


def static_format(model_id: str) -> str | None:
    """官方文档明文声明的格式约束(前缀匹配);未知返 None(不发,交探测)。"""
    low = str(model_id or "").lower()
    for prefix, fmt in _STATIC_FORMAT_MAP:
        if low.startswith(prefix):
            return fmt
    return None


def _suggested_format(message: str) -> str | None:
    """从 provider 的报错话术里提取它指定的合法格式。

    实测话式(2026-10):
    - heygen:`HeyGen Voice only supports response_format "pcm".`
    - minimax:`MiniMax TTS only supports response_format="mp3" for streaming. Got "pcm".`
    """
    match = re.search(r'only supports response_format\s*=?\s*["\'](\w+)', str(message or "").lower())
    if match and match.group(1) in ("pcm", "mp3"):
        return match.group(1)
    return None


# ---------- 请求体 ----------


def build_body(
    *,
    text: str,
    model: str,
    voice: str | None = None,
    response_format: str | None = None,
) -> dict[str, Any]:
    """组装 ``POST /api/v1/audio/speech`` 请求体;提交前按目录枚举校验 voice。

    - voice:目录枚举非空且值超枚举 → ParamError(目录没收录或没声明的模型不拦,
      交 provider 裁决 —— Unknown voice 的 404 会原样带出 provider 提示);
    - response_format:pcm/mp3 之外直接 ParamError(上游只有这两档)。
    """
    if not text or not text.strip():
        raise OpenRouterTtsParamError("文本(input)不能为空")
    if not model or not str(model).strip():
        raise OpenRouterTtsParamError("model 不能为空(设置 OP_TTS_MODEL 或显式传入)")

    body: dict[str, Any] = {"model": model, "input": text.strip()}
    entry = speech_model_entry(model)
    if voice is not None and str(voice).strip():
        value = str(voice).strip()
        voices = supported_voices(entry)
        if voices and value not in voices:
            raise OpenRouterTtsParamError(
                f"模型 {model} 不支持 voice「{value}」(支持 {'/'.join(voices)})"
            )
        body["voice"] = value

    fmt = str(response_format or "").strip().lower()
    if fmt:
        if fmt not in ("pcm", "mp3"):
            raise OpenRouterTtsParamError(f"response_format 只支持 pcm/mp3,收到:{fmt!r}")
        body["response_format"] = fmt
    return body


# ---------- 合成(同步 POST → 音频字节) ----------


@dataclass
class SpeechResult:
    """一次合成的结果。

    Fields:
        audio:            最终音频字节(wav 已封装 / mp3 等原样)。
        ext:              实际封装/嗅探出的扩展名(wav/mp3/ogg/flac/bin)。
        content_type:     上游响应 Content-Type(原始,含 rate/channels 参数)。
        sample_rate:      采样率(PC 从 Content-Type 解析;其他容器 None)。
        channels:         声道数(同上)。
        generation_id:    X-Generation-Id 头(OpenRouter 的配对 ID)。
        format_requested: 请求显式给的 response_format(None = 没显式给)。
        format_sent:      实际发出去的 response_format(None = 没发,API 默认 pcm)。
        request_body:     实际发出去的请求体(调试用)。
    """

    audio: bytes
    ext: str
    content_type: str = ""
    sample_rate: int | None = None
    channels: int | None = None
    generation_id: str = ""
    format_requested: str | None = None
    format_sent: str | None = None
    request_body: dict = field(default_factory=dict)


def _parse_audio_params(content_type: str) -> tuple[int | None, int | None]:
    """``audio/pcm;rate=44100;channels=1`` → (44100, 1);解析不了就 (None, None)。"""
    rate: int | None = None
    channels: int | None = None
    for part in content_type.split(";")[1:]:
        part = part.strip().lower()
        if part.startswith("rate="):
            try:
                rate = int(part[5:])
            except ValueError:
                continue
        elif part.startswith("channels="):
            try:
                channels = int(part[9:])
            except ValueError:
                continue
    return rate, channels


def sniff_audio_ext(data: bytes) -> str:
    """按魔数嗅探音频容器,返回扩展名(wav/mp3/ogg/flac/bin)。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    return "bin"


def wrap_pcm_as_wav(data: bytes, sample_rate: int, channels: int, sampwidth: int = 2) -> bytes:
    """裸 PCM → WAV 封装(标准库 wave;上游 heygen 的实测规格 44100Hz mono 16bit)。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(sample_rate)
        handle.writeframes(data)
    return buffer.getvalue()


def _initial_format(model_id: str, requested: str | None) -> str | None:
    """要发的 response_format 初值:显式 > 探测缓存 > 静态表 > 不发(API 默认 pcm)。"""
    value = str(requested or "").strip().lower()
    if value:
        return value
    cached = _read_format_cache().get(model_id)
    if isinstance(cached, dict):
        cached_fmt = str(cached.get("response_format") or "").strip().lower()
        if cached_fmt in ("pcm", "mp3"):
            return cached_fmt
    return static_format(model_id)


def _post_speech(
    body: dict[str, Any],
    *,
    key: str,
    model_id: str,
    timeout_s: float | None,
) -> tuple[bytes, str, int | None, int | None, str, str | None]:
    """POST 合成;格式被 provider 拒了就按错误话术里的合法值换掉重试。

    与生图 :func:`openrouter_image._post_generate` 同思路,但 TTS 的降级不是
    「摘参数」而是「换值」—— 上游只有 mp3/pcm 两档,400 话术会明示合法档
    (only supports response_format "X"),按它重试并记 tts-capabilities.json。

    Returns:
        (音频字节, content_type, sample_rate, channels, generation_id, format_sent)
    """
    current = dict(body)
    format_sent = current.get("response_format")
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
            raise OpenRouterTtsFetchError(f"请求 OpenRouter 语音合成失败:{exc}") from exc

        if resp.status_code == 200:
            if not resp.content:
                raise OpenRouterTtsParseError("音频内容为空")
            return (
                resp.content,
                resp.headers.get("content-type", ""),
                *_parse_audio_params(resp.headers.get("content-type", "")),
                resp.headers.get("x-generation-id", ""),
                str(format_sent) if format_sent else None,
            )

        error = _error_from_response(resp)
        # 只对"格式不被接受"换值重试;鉴权/限流/内容策略/Unknown voice 重试没意义
        if not isinstance(error, OpenRouterTtsFetchError):
            raise error
        hinted = _suggested_format(str(error))
        if not hinted or hinted == format_sent or hinted in tried:
            raise error
        tried.add(str(format_sent))
        logger.info(
            "openrouter_tts.format_degraded | model=%s | rejected=%s -> %s",
            model_id,
            format_sent,
            hinted,
        )
        _record_format(model_id, hinted)
        current["response_format"] = hinted
        format_sent = hinted


def generate(
    *,
    text: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str | None = None,
    api_key: str | None = None,
    timeout_s: float | None = None,
) -> SpeechResult:
    """调 OpenRouter ``POST /api/v1/audio/speech`` 合成一段语音(同步)。

    Args:
        text: 要朗读的文本(必填)。
        model: 模型 slug;缺省走 ``OP_TTS_MODEL`` / 默认 heygen-voice-1-turbo:free。
        voice: voice 标识;空且目录枚举非空时自动兜底第一项(voice 实测必填);
            枚举为空的模型原样不发送,由 provider 裁决。
        response_format: mp3/pcm;空 = 探测缓存 > 静态表 > 不发(API 默认 pcm),
            被拒时按 provider 话术换值重试并记档。
        api_key: OpenRouter key;缺省 ``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``。
        timeout_s: HTTP 超时(秒);缺省 ``OP_TTS_TIMEOUT`` 或 300。

    Raises:
        OpenRouterTtsParamError: 参数缺失 / 非法(voice 超枚举等)。
        OpenRouterTtsAuthError: 401 / 402 / 非内容策略的 403。
        OpenRouterTtsFetchError: 网络失败,或 provider 用 403 拒绝违规文本。
        OpenRouterTtsRateLimitError: 429。
        OpenRouterTtsNotFoundError: 404(模型 slug / voice 写错)。
        OpenRouterTtsParseError: 响应缺音频字节等关键结构。
    """
    model_id = resolve_model(model)
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterTtsParamError(
            "缺少 OpenRouter API Key:请设置 OP_IMAGE_API_KEY / LLM_API_KEY,或在「设置」页配置"
        )

    entry = speech_model_entry(model_id)
    voices = supported_voices(entry)
    voice_value = str(voice or "").strip()
    if not voice_value and voices:
        # 实测 voice 必填(heygen/minimax 不带 voice 一律 400):枚举非空默认取第一项,
        # 与视频页「枚举默认取第一项」同一选择策略
        voice_value = voices[0]
        logger.info("openrouter_tts.voice_defaulted | model=%s | voice=%s", model_id, voice_value)

    body = build_body(
        text=text,
        model=model_id,
        voice=voice_value or None,
        response_format=response_format,
    )
    format_requested = str(response_format or "").strip().lower() or None
    # 格式初值求解:显式 > 探测缓存 > 静态表 > 不发。初值直接写进 body,
    # provider 拒收时 _post_speech 按话术换值重试。
    initial = _initial_format(model_id, format_requested)
    if initial:
        body["response_format"] = initial
    else:
        body.pop("response_format", None)

    audio, content_type, rate, channels, generation_id, format_sent = _post_speech(
        body, key=key, model_id=model_id, timeout_s=timeout_s
    )

    ext = sniff_audio_ext(audio)
    sample_rate: int | None = rate
    channel_count: int | None = channels
    if ext == "bin":
        mime = content_type.split(";")[0].strip().lower()
        # 裸 PCM(无魔数可嗅):按 Content-Type 参数封装成 WAV
        if mime == "audio/pcm":
            ext = "wav"
            audio = wrap_pcm_as_wav(audio, rate or 44100, channels or 1)
            sample_rate = rate or 44100
            channel_count = channels or 1
        else:
            raise OpenRouterTtsParseError(
                f"OpenRouter 返回的不是可识别的音频格式(Content-Type {content_type!r})"
            )

    characters = len(text.strip())
    cost = estimate_cost(model_id, characters)
    logger.info(
        "openrouter_tts.speech_generated | model=%s | voice=%s | chars=%d | bytes=%d | cost~%s",
        model_id,
        voice_value,
        characters,
        len(audio),
        cost,
    )
    return SpeechResult(
        audio=audio,
        ext=ext,
        content_type=content_type,
        sample_rate=sample_rate,
        channels=channel_count,
        generation_id=generation_id,
        format_requested=format_requested,
        format_sent=format_sent,
        request_body={k: ("<redacted>" if k == "input" else v) for k, v in body.items()},
    )


# ---------- 费用预估 ----------


def estimate_cost(model_id: str, characters: int) -> float | None:
    """按目录 ``pricing.prompt``($/token ≈ $/字符)估算一次合成的钱(美元)。

    TTS 响应没有费用头(SpeechResult 不带 usage),这只是展示预估,
    与视频通路「预计 ≈」同一口径;免费档(turbo:free)算出 0.0。
    算不出(目录没收录 / pricing 缺失)返 None。
    """
    entry = speech_model_entry(model_id)
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict) or characters <= 0:
        return None
    rate = pricing.get("prompt")
    if rate is None:
        return None
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return None
    return round(value * characters, 6)


def estimate_cost_with_chars(model: str | None, text: str) -> float | None:
    """给上层(前端 /tts/models 预览、CLI)一行算价:resolve_model + estimate_cost。"""
    return estimate_cost(resolve_model(model), len(str(text or "").strip()))