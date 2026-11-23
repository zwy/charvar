"""OpenRouter 音乐生成客户端(Lyria 3;镜像 ``openrouter_tts`` 的骨架,语义按 chat/completions 流式音频)。

API 形态(2026-11 官方文档 + 实测,与语音/生图/视频三条通路都不同):

- 没有独立端点,走**普通 chat/completions**:
  ``POST /api/v1/chat/completions``,body = ``{model, messages, modalities:
  ["text","audio"], stream: true}``;**``stream: true`` 必开**——不发流式直接
  400(``Audio output requires stream: true``);
- SSE 分片在 ``choices[0].delta.audio``:``.data``(base64 片段,**按顺序拼接后
  一次 decode**)与可选 ``.transcript``;``delta.content`` 的文本段落(Lyria 会
  输出曲目说明)一并收集;``data: [DONE]`` 终结;
- 流末的 ``usage`` 块带 ``usage.cost`` **实收费用**(与视频通路一样回写实价,
  比 TTS 多一项);预估价来自目录条目 description 里的 ``$0.08 per song`` /
  ``$0.04 per clip``(pricing.prompt/completion 恒 0,不能按 token 算);
- 产物按魔数嗅探(Lyria 实测完整 MP3);裸流(pcm16 等,`gpt-audio` 系按
  ``audio.format`` 出)第一期不支持——采样率信息流里拿不到,直接 ParseError;
- 目录读 ``GET /api/v1/models?output_modalities=audio``,缓存
  ``data/openrouter/music-models.json``(生图/视频/语音之外的第四份缓存)。

与生图/视频/语音一致的红线:httpx ``trust_env=False``(shell 的 ALL_PROXY 会
劫持地址)、API key 复用 ``openrouter_image.resolve_api_key``(同一家 OpenRouter)、
目录缓存冷启动并发只拉一次、不引入随机源(seed 只透传调用方显式给的值)。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from charvar import openrouter_image
from charvar import openrouter_tts as _tts_share
from charvar.openrouter_image import (
    _cache_lock,
    _env,
    _read_cache,
    _write_cache_atomic,
)

logger = logging.getLogger(__name__)


def resolve_proxy(proxy: str | None = None) -> str | None:
    """音乐通路的出口代理:显式 > env ``OP_MUSIC_PROXY`` > macOS 系统代理 > 直连。

    与生图/语音/视频「一律 bypass 代理」的红线相反:Lyria 的 provider 有地区限制,
    OpenRouter 按请求出口 IP 判区 —— 本机直连被 403(``This model is not available in
    your region.``,实测),必须走系统代理出口。跟随系统代理的当前端口(不写死,
    QuickQ 端口每次启动会变);系统代理没开时回退直连(此时 Lyria 报地区 403,
    目录列表不受影响)。
    """
    for candidate in (proxy, _env("OP_MUSIC_PROXY")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - 读系统代理失败按直连走
        return None
    value = str(proxies.get("https") or proxies.get("http") or "").strip()
    return value or None


def _http_client(timeout: float, proxy: str | None = None) -> httpx.Client:
    """HTTP 客户端工厂(trust_env=False + 显式 proxy;测试用 MockTransport 覆写本函数)。"""
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(timeout, write=60.0), proxy=proxy)

_BASE_URL = openrouter_image._BASE_URL
_LIST_MODELS_QUERY = "output_modalities=audio"

_DEFAULT_MODEL = "google/lyria-3-clip-preview"
_DEFAULT_TIMEOUT_S = 600.0
_LIST_MODELS_TIMEOUT_S = 60.0

# 目录 description 里的单价声明(实测口径:整曲/30 秒片段一口价,按次数计费):
#   "Full-length songs are priced at $0.08 per song. ..."
#   "30 second duration clips are priced at $0.04 per clip. ..."
_PRICE_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*per\s+(song|track|clip)\b", re.IGNORECASE)


class OpenRouterMusicError(RuntimeError):
    """OpenRouter 音乐生成失败(基类)。"""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class OpenRouterMusicAuthError(OpenRouterMusicError):
    """鉴权 / 余额类失败(401 / 402 / 非内容策略的 403)。"""


class OpenRouterMusicFetchError(OpenRouterMusicError):
    """网络层失败,或上游 provider 用 403 拒绝了违规提示词。"""


class OpenRouterMusicRateLimitError(OpenRouterMusicError):
    """限流(429)。"""


class OpenRouterMusicNotFoundError(OpenRouterMusicError):
    """资源不存在(404:模型 slug 写错)。"""


class OpenRouterMusicParseError(OpenRouterMusicError):
    """SSE 流缺音频结构,或产物容器不可识别。"""


class OpenRouterMusicParamError(OpenRouterMusicError):
    """入参 / 配置校验失败(提交前就该拦下的那类)。"""


# ---------- 配置 ----------


def resolve_model(model: str | None = None) -> str:
    """生效的模型 slug:显式传入 > env ``OP_MUSIC_MODEL`` > 默认 lyria-3-clip(便宜档)。

    设置页的 DB 值由上层(settings_service.openrouter_music_config)先合并进
    ``model`` 再调这里,本函数只收「已经过 DB 一层」的候选。
    """
    for candidate in (model, _env("OP_MUSIC_MODEL")):
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    return _DEFAULT_MODEL


def resolve_api_key(api_key: str | None = None) -> str:
    """API key 与生图/视频/语音同链路(同一家 OpenRouter),复用 openrouter_image 的链。"""
    return openrouter_image.resolve_api_key(api_key)


def resolve_base_url(base_url: str | None = None) -> str:
    """生效的 API 基址(与生图同链):显式 > ``OP_BASE_URL`` > 默认。"""
    return openrouter_image.resolve_base_url(base_url)


def resolve_timeout(timeout_s: float | None = None) -> float:
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("OP_MUSIC_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def models_cache_path() -> Path:
    """音乐模型目录缓存:``{CHARVAR_DATA_DIR}/openrouter/music-models.json``。"""
    from charvar import paths

    return paths.data_dir() / "openrouter" / "music-models.json"


# ---------- 模型目录 ----------


def list_models(
    *, force_refresh: bool = False, api_key: str | None = None, base_url: str | None = None
) -> dict:
    """读 ``GET /api/v1/models?output_modalities=audio``(本地缓存优先,无则拉取并落盘)。

    与生图/视频/语音共用锁与原子写逻辑(openrouter_image):冷缓存并发只拉一次。
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
            with _http_client(_LIST_MODELS_TIMEOUT_S, resolve_proxy()) as client:
                resp = client.get(
                    f"{resolve_base_url(base_url)}/models?{_LIST_MODELS_QUERY}", headers=headers
                )
        except httpx.HTTPError as exc:
            raise OpenRouterMusicFetchError(f"请求 OpenRouter 音乐模型目录失败:{exc}") from exc
        if resp.status_code != 200:
            raise _tts_share._error_from_response(resp)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterMusicParseError(f"音乐模型目录响应不是合法 JSON:{exc}") from exc
        if not isinstance(data, dict):
            raise OpenRouterMusicParseError("音乐模型目录响应顶层不是 dict")
        _write_cache_atomic(cache_path, data)
        return data


def music_model_entry(model_id: str) -> dict[str, Any]:
    """目录条目(含 supported_parameters / pricing / description);未知模型返空 dict。"""
    try:
        data = list_models()
    except (OpenRouterMusicError, _tts_share.OpenRouterTtsError):
        return {}
    for item in data.get("data") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == model_id:
            return item
    return {}


def model_choices() -> list[dict[str, str]]:
    """目录精简成 ``[{id, name}]``,供下拉(按 id 排序)。"""
    data = list_models()
    out: list[dict[str, str]] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        out.append({"id": model_id, "name": str(item.get("name") or model_id)})
    out.sort(key=lambda m: (m["id"].lower(), m["name"].lower()))
    return out


# ---------- 费用 ----------


def estimate_cost(model_id: str) -> float | None:
    """按目录 description 里的单价声明估算一次生成的钱(美元):``$0.04 per clip``。

    pricing.prompt/completion 恒 0(一口价按次数计费),只能从 description 抽;
    实收费用不用估——流末 ``usage.cost`` 直接回写(adhoc_music 记 ``cost_real``)。
    抽不到(没声明/目录没收录)返 None。
    """
    entry = music_model_entry(model_id)
    description = str(entry.get("description") or "")
    match = _PRICE_RE.search(description)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


# ---------- 请求体 ----------


def build_body(
    *,
    prompt: str,
    model: str,
    seed: int | None = None,
    voice: str | None = None,
    audio_format: str | None = None,
) -> dict[str, Any]:
    """组装 ``POST /api/v1/chat/completions`` 请求体(流式音频)。

    - ``modalities: ["text","audio"]`` 与 ``stream: true`` 恒带(音频输出的前提);
    - ``seed`` 只在显式给且目录声明(``supported_parameters`` 含 ``seed``)时发,
      与生图「没声明就不发」同一选择策略;没给不生成随机值(不引入随机源);
    - ``audio: {voice, format}`` 只在显式给时组(gpt-audio 系才认,Lyria 不发);
      本期 CLI/网页不暴露,留给后续(声音克隆 / 格式指定)。
    """
    if not prompt or not prompt.strip():
        raise OpenRouterMusicParamError("提示词(prompt)不能为空")
    if not model or not str(model).strip():
        raise OpenRouterMusicParamError("model 不能为空(设置 OP_MUSIC_MODEL 或显式传入)")

    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt.strip()}],
        "modalities": ["text", "audio"],
        "stream": True,
    }
    if seed is not None:
        entry = music_model_entry(model)
        params = entry.get("supported_parameters")
        if isinstance(params, list) and "seed" in params:
            body["seed"] = int(seed)
    voice_value = str(voice or "").strip()
    format_value = str(audio_format or "").strip()
    if voice_value or format_value:
        audio_config: dict[str, str] = {}
        if voice_value:
            audio_config["voice"] = voice_value
        if format_value:
            audio_config["format"] = format_value
        body["audio"] = audio_config
    return body


# ---------- 生成(SSE 流式 → 音频字节) ----------


@dataclass
class MusicResult:
    """一次音乐生成的结果。

    Fields:
        audio:       最终音频字节(Lyria 实测完整 MP3)。
        ext:         按魔数嗅探的容器扩展名(mp3/wav/ogg/flac)。
        transcript:  ``delta.audio.transcript`` 拼接(各模型可空)。
        text_content: ``delta.content`` 文本段落拼接(Lyria 的曲目说明)。
        usage_cost:  流末 ``usage.cost`` 实收费用(美元);没有(免费/中途断)则 None。
        seed_sent:   实际发出去的 seed(None = 没发)。
        request_body: 实际发出去的请求体(调试用,prompt 缺省截断)。
    """

    audio: bytes
    ext: str
    transcript: str = ""
    text_content: str = ""
    usage_cost: float | None = None
    seed_sent: int | None = None
    request_body: dict = field(default_factory=dict)


def _error_from_response(resp: httpx.Response) -> OpenRouterMusicError:
    """把非 2xx 响应映射成对应异常(message 携带上游 error.message)。"""
    base = _tts_share._error_from_response(resp)
    # TTS 的映射已经覆盖鉴权/限流/404/内容策略,这里只是换皮(保持本通路异常族)
    kind: type[OpenRouterMusicError] = OpenRouterMusicFetchError
    if isinstance(base, _tts_share.OpenRouterTtsAuthError):
        kind = OpenRouterMusicAuthError
    elif isinstance(base, _tts_share.OpenRouterTtsRateLimitError):
        kind = OpenRouterMusicRateLimitError
    elif isinstance(base, _tts_share.OpenRouterTtsNotFoundError):
        kind = OpenRouterMusicNotFoundError
    elif isinstance(base, _tts_share.OpenRouterTtsParseError):
        kind = OpenRouterMusicParseError
    return kind(str(base), http_status=getattr(base, "http_status", None))


@dataclass
class _StreamAccumulator:
    """SSE 流累积器:base64 音频片段 / transcript / 文本 / usage。"""

    audio_b64_parts: list[str] = field(default_factory=list)
    transcript_parts: list[str] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    usage: dict | None = None

    def feed(self, chunk: dict) -> None:
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return
        delta = choices[0].get("delta") or {}
        if not isinstance(delta, dict):
            return
        audio = delta.get("audio") or {}
        if isinstance(audio, dict):
            data = audio.get("data")
            if isinstance(data, str) and data:
                self.audio_b64_parts.append(data)
            transcript = audio.get("transcript")
            if isinstance(transcript, str) and transcript:
                self.transcript_parts.append(transcript)
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.text_parts.append(content)

    def finalize(self) -> tuple[bytes, str, str, float | None]:
        """拼接 + 一次 decode;终末容器校验。

        Raises:
            OpenRouterMusicParseError: 流里没有音频,或容器不可识别
                (裸 PCM/pcm16 等第一期不支持——采样率信息流里拿不到,别猜格式)。
        """
        if not self.audio_b64_parts:
            raise OpenRouterMusicParseError(
                "OpenRouter 音乐流里没有音频数据(delta.audio.data 缺失;检查模型是否声明音频输出)"
            )
        try:
            audio = base64.b64decode("".join(self.audio_b64_parts))
        except Exception as exc:  # noqa: BLE001 - base64 坏了属于流坏
            raise OpenRouterMusicParseError(f"音频 base64 解码失败:{exc}") from exc
        ext = _tts_share.sniff_audio_ext(audio)
        if ext == "bin":
            raise OpenRouterMusicParseError(
                "OpenRouter 返回的不是可识别的音频容器(裸 PCM/pcm16 等第一期不支持:"
                "采样率信息流里拿不到;换出 mp3 容器的模型,如 google/lyria-3-*)"
            )
        usage_cost = self.usage.get("cost") if self.usage else None
        cost: float | None = None
        if usage_cost is not None:
            try:
                cost = float(usage_cost)
            except (TypeError, ValueError):
                cost = None
        return audio, "".join(self.transcript_parts), "".join(self.text_parts), cost


def generate(
    *,
    prompt: str,
    model: str | None = None,
    seed: int | None = None,
    voice: str | None = None,
    audio_format: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_s: float | None = None,
) -> MusicResult:
    """调 OpenRouter ``POST /api/v1/chat/completions``(流式音频)生成一段音乐(同步)。

    Args:
        prompt: 乐曲描述(流派/节奏/乐器/是否人声/歌词);必填。
        model: 模型 slug;缺省 ``OP_MUSIC_MODEL`` / ``google/lyria-3-clip-preview``。
        seed: 复现用;只在目录声明 ``seed`` 时发送,没给不生成随机值。
        voice / audio_format: gpt-audio 系的 ``audio: {voice, format}`` 配置;
            只在显式给时发(Lyria 不发)。本期对外不暴露,留给后续。
        api_key: OpenRouter key;缺省 ``OP_API_KEY`` / 旧链路回退(同生图)。
        base_url: API 基址;缺省 ``OP_BASE_URL`` / openrouter.ai/api/v1。
        timeout_s: HTTP 超时(秒);缺省 ``OP_MUSIC_TIMEOUT`` 或 600。

    Raises:
        OpenRouterMusicParamError: 提示词为空 / 缺 key / seed 等提交前错误。
        OpenRouterMusicAuthError: 401 / 402 / 非内容策略的 403。
        OpenRouterMusicFetchError: 网络失败,或 provider 用 403 拒绝违规提示词。
        OpenRouterMusicRateLimitError: 429。
        OpenRouterMusicNotFoundError: 404(模型 slug 写错)。
        OpenRouterMusicParseError: 流缺音频 / base64 坏 / 容器不可识别。
    """
    model_id = resolve_model(model)
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterMusicParamError(
            "缺少 OpenRouter API Key:请设置 OP_API_KEY,或在「设置」页 / 旧环境变量 OP_IMAGE_API_KEY、LLM_API_KEY 配"
        )

    body = build_body(
        prompt=prompt, model=model_id, seed=seed, voice=voice, audio_format=audio_format
    )
    seed_sent = body.get("seed")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    accumulator = _StreamAccumulator()
    proxy = resolve_proxy()
    logger.info("openrouter_music.request | model=%s | proxy=%s | seed=%s", model_id, proxy or "direct", seed)
    try:
        with _http_client(resolve_timeout(timeout_s), proxy) as client:
            # httpx.Client(timeout) 对流式请求的「读间隔」同样生效:600s 内没新分片即断,
            # 整曲生成远小于这个间隔,足够。
            with client.stream(
                "POST", f"{resolve_base_url(base_url)}/chat/completions",
                headers=headers, json=body,
            ) as resp:
                if resp.status_code != 200:
                    # 流式下要读完 body 才能拿到错误 JSON
                    resp.read()
                    raise _error_from_response(resp)
                for line in resp.iter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    accumulator.feed(chunk)
    except httpx.HTTPError as exc:
        raise OpenRouterMusicFetchError(f"请求 OpenRouter 音乐生成失败:{exc}") from exc

    audio, transcript, text_content, cost = accumulator.finalize()
    logger.info(
        "openrouter_music.generated | model=%s | bytes=%d | cost=%s | seed=%s",
        model_id,
        len(audio),
        cost,
        seed_sent,
    )
    return MusicResult(
        audio=audio,
        ext=_tts_share.sniff_audio_ext(audio),
        transcript=transcript,
        text_content=text_content,
        usage_cost=cost,
        seed_sent=int(seed_sent) if seed_sent is not None else None,
        request_body={k: ("<redacted>" if k == "messages" else v) for k, v in body.items()},
    )


__all__ = [
    "OpenRouterMusicAuthError",
    "OpenRouterMusicError",
    "OpenRouterMusicFetchError",
    "OpenRouterMusicNotFoundError",
    "OpenRouterMusicParamError",
    "OpenRouterMusicParseError",
    "OpenRouterMusicRateLimitError",
    "MusicResult",
    "build_body",
    "estimate_cost",
    "generate",
    "list_models",
    "model_choices",
    "models_cache_path",
    "music_model_entry",
    "resolve_api_key",
    "resolve_proxy",
    "resolve_base_url",
    "resolve_model",
    "resolve_timeout",
]
