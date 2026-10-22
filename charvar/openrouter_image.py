"""OpenRouter 在线生图客户端（移植自 novel-workspace/shared/openrouter_image_client）。

与参考实现的差异（charvar 侧约束）：

- 画幅走 ``size`` 显式像素而不是 ``aspect_ratio`` 枚举。实测 provider
  (Tencent) 只接受 1:1 / 2:3 / 3:4 / 16:9 等固定比例，charvar 支持的
  ``21:9`` 会被 400 拒绝；而 ``size: "1024x448"`` 对所有比例都生效，
  且与 ``resolution`` / ``aspect_ratio`` 冲突时 provider 明确报 400
  ——避免同时传三个画幅参数。
- 产物直接落 charvar 的图片资产目录（``data/generated/{run_id}/original/``），
  不另开一套落盘目录，这样图片墙 / contact sheet / 合成视频全部复用。
- 出网请求一律 ``httpx trust_env=False``（本机 shell 的 ALL_PROXY 会劫持地址），
  与 ``charvar.llm`` / ``charvar.exporters.comfyui`` 保持一致。
- 不传 ``negative_prompt``：OpenRouter Images API 没有这个字段。
- 画幅/分辨率/质量等参数按 ``data/openrouter/api-response.json`` 里每个模型的
  ``supported_parameters`` 动态决定（见 :func:`model_capabilities`），
  provider 拒绝某个参数时自动降级重试（见 :func:`generate_image`）。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"
_LIST_MODELS_URL = f"{_BASE_URL}/images/models"
_GENERATE_URL = f"{_BASE_URL}/images"

_DEFAULT_MODEL = "meta/muse-image"
_DEFAULT_TIMEOUT_S = 300.0
_LIST_MODELS_TIMEOUT_S = 60.0

# 参考图规格交给 charvar.reference_images 统一处理（长边 1536 / JPEG q88），
# ComfyUI 与 OpenRouter 共用同一份压缩结果。
_REFERENCE_MAX_SIDE = 1536
_REFERENCE_JPEG_QUALITY = 88

# 提示词里的 <imageN> → 序数词。删掉会让句子残缺（"exactly as shown in ;"），
# 序数词替换后语法完整，且顺序与 input_references 天然对应。
_IMAGE_TAG_RE = re.compile(r"<image(\d+)>")
# 经典模板的固定句式："in the character sheet in <image1>"。序数词直替会变成
# "in the character sheet in the first reference image"（啰嗦），单独处理。
_CHARACTER_SHEET_RE = re.compile(r"in the character sheet in <image(\d+)>", re.IGNORECASE)
# 无参考图时整句摘掉：这句话只是在指参考图，去掉标签会剩 "for the character's identity." 这种残句。
_NO_REF_SENTENCES = (
    re.compile(r"\s*Use <image\d+> as the reference[^.]*\.", re.IGNORECASE),
)
# 无参考图时摘掉的指向短语（嵌在更长的句子里，不能整句删）。
_NO_REF_PHRASES = (
    re.compile(r"\s*in the character sheet in <image\d+>", re.IGNORECASE),
    re.compile(r"\s*from <image\d+>", re.IGNORECASE),
    re.compile(r"\s*as <image\d+>", re.IGNORECASE),
)
_MAX_REFERENCES = 4
_ORDINALS = (
    "the first reference image",
    "the second reference image",
    "the third reference image",
    "the fourth reference image",
)


class OpenRouterImageError(RuntimeError):
    """OpenRouter 生图失败（基类）。"""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class OpenRouterAuthError(OpenRouterImageError):
    """鉴权 / 余额类失败（401 / 402 / 非内容策略的 403）。"""


class OpenRouterFetchError(OpenRouterImageError):
    """网络层失败，或上游 provider 用 403 拒绝了违规 prompt。"""


class OpenRouterRateLimitError(OpenRouterImageError):
    """限流（429）。"""


class OpenRouterNotFoundError(OpenRouterImageError):
    """资源不存在（404，通常是 model slug 写错）。"""


class OpenRouterParseError(OpenRouterImageError):
    """响应不是合法 JSON，或缺 ``data[].b64_json`` 等关键结构。"""


class OpenRouterParamError(OpenRouterImageError):
    """入参 / 配置校验失败。"""


@dataclass
class GenerateImageResult:
    """一次生图的结果。

    Fields:
        generation_id:  本次生成的配对 ID（b64 / usage 文件名共用）。
        created:        OpenRouter 返回的 ``created`` unix 时间戳。
        images:         base64 解码后的图片 bytes 列表。
        media_types:    与 ``images`` 一一对应的 media_type（``image/png`` 等）。
        usage:          OpenRouter 返回的 ``usage`` dict（原样）。
        request_body:   实际发给 OpenRouter 的请求体（调试用）。
    """

    generation_id: str
    created: int
    images: list[bytes]
    media_types: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    request_body: dict = field(default_factory=dict)

    @property
    def cost(self) -> float | None:
        value = self.usage.get("cost")
        return float(value) if isinstance(value, (int, float)) else None


# ---------- 配置 ----------


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def resolve_api_key(api_key: str | None = None) -> str:
    """生效的 API Key：显式传入 > OP_IMAGE_API_KEY > LLM_API_KEY。

    两者都是 OpenRouter 的 key，``.env`` 里往往只配了 LLM_API_KEY，
    回退它省得用户再配一份。
    """
    for candidate in (api_key, _env("OP_IMAGE_API_KEY"), _env("LLM_API_KEY")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def resolve_model(model: str | None = None, mode: str = "t2i") -> str:
    """生效的模型 slug：显式传入 > 模式 env > 旧 OP_IMAGE_MODEL > 默认模型。

    mode="t2i"/"i2i" 对齐 ComfyUI 的文生图 / 图片编辑两组配置；
    旧的 ``OP_IMAGE_MODEL`` 保留为公共兜底，存量 .env 不用立刻改。
    """
    chain = [_MODE_ENV_PREFIX.get(mode, "OP_IMAGE_T2I") + "_MODEL", "OP_IMAGE_MODEL"]
    for candidate in (model, *(_env(name) for name in chain)):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return _DEFAULT_MODEL


# 文生图 t2i（不带参考图）/ 图片编辑 i2i（带参考图）的 env 前缀
_MODE_ENV_PREFIX = {"t2i": "OP_IMAGE_T2I", "i2i": "OP_IMAGE_I2I"}


def env_mode_value(mode: str, param: str) -> str:
    """模式级的 env 默认值（RESOLUTION / QUALITY）；没设返空串。

    这是「请求没显式给时的默认」：请求给了一律不用它。
    值是否能发以所选模型的 supported_parameters 枚举为准。
    """
    return _env(f"{_MODE_ENV_PREFIX.get(mode, 'OP_IMAGE_T2I')}_{param.upper()}")


def resolve_timeout(timeout_s: float | None = None) -> float:
    if timeout_s is not None:
        return float(timeout_s)
    raw = _env("OP_IMAGE_TIMEOUT")
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_S
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def models_cache_path() -> Path:
    """模型清单缓存：``{CHARVAR_DATA_DIR}/openrouter/api-response.json``。"""
    from charvar import paths

    return paths.data_dir() / "openrouter" / "api-response.json"


# ---------- HTTP ----------


def _http_client(timeout: float) -> httpx.Client:
    # trust_env=False：忽略 shell 的 ALL_PROXY 等环境代理（与 charvar.llm 同一策略）
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(timeout, write=60.0))


def _error_from_response(resp: httpx.Response) -> OpenRouterImageError:
    """把非 2xx 响应映射成对应异常，message 携带上游 error.message。"""
    try:
        body = resp.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        message = None
    if not message:
        message = resp.text[:300] or f"HTTP {resp.status_code}"

    if resp.status_code == 403 and _is_provider_policy_rejection(message):
        # 上游 model provider 用 403 拒绝违规 prompt：key 和余额都是好的，
        # 归到 AuthError 会让人误判成 key 问题。
        return OpenRouterFetchError(
            f"上游 model provider 拒绝 prompt（HTTP 403 内容策略）：{message}",
            http_status=403,
        )
    if resp.status_code in (401, 402, 403):
        return OpenRouterAuthError(
            f"OpenRouter 鉴权/余额失败（HTTP {resp.status_code}）：{message}",
            http_status=resp.status_code,
        )
    if resp.status_code == 429:
        return OpenRouterRateLimitError(
            f"OpenRouter 限流（HTTP 429）：{message}", http_status=429
        )
    if resp.status_code == 404:
        return OpenRouterNotFoundError(
            f"OpenRouter 资源不存在（HTTP 404）：{message}", http_status=404
        )
    return OpenRouterFetchError(
        f"OpenRouter API 返回 HTTP {resp.status_code}：{message}",
        http_status=resp.status_code,
    )


def _is_provider_policy_rejection(message: str) -> bool:
    low = str(message or "").lower()
    return any(
        token in low
        for token in (
            "terms of service",
            "content policy",
            "content_policy",
            "safety",
            "prohibited",
            "moderation",
        )
    )


# ---------- 模型清单 ----------


def _read_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("openrouter.models_cache_read_failed | path=%s | err=%s", path, exc)
        return None
    return data if isinstance(data, dict) else None


# 清单缓存与能力缓存的进程内锁。批量生图是 4 线程并发：
# 冷缓存时 4 个线程会同时去拉清单（4 次无用请求），探测结果也是
# 读-改-写（并发写会互相覆盖）。加锁后只有第一个线程真的拉。
_cache_lock = threading.Lock()


def _write_cache_atomic(path: Path, data: dict) -> None:
    """原子写：先写唯一的临时文件再 rename。

    临时文件名必须带唯一后缀 —— 批量生图是 4 线程并发，共用一个固定
    ``.tmp`` 名会让一个线程的 finally 把另一个线程刚写的临时文件删掉，
    导致 osreplace 报 FileNotFoundError。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def list_models(*, force_refresh: bool = False, api_key: str | None = None) -> dict:
    """读 ``GET /api/v1/images/models``（本地优先，无则拉取并落盘）。

    冷缓存时并发调用只会真的拉一次（其余线程等锁后直接读缓存）。
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
        return _fetch_and_cache_models(cache_path, api_key)


def _fetch_and_cache_models(cache_path: Path, api_key: str | None) -> dict:
    headers = {"Accept": "application/json"}
    key = resolve_api_key(api_key)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with _http_client(_LIST_MODELS_TIMEOUT_S) as client:
            resp = client.get(_LIST_MODELS_URL, headers=headers)
    except httpx.HTTPError as exc:
        raise OpenRouterFetchError(f"请求 OpenRouter 模型清单失败：{exc}") from exc
    if resp.status_code != 200:
        raise _error_from_response(resp)
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise OpenRouterParseError(f"模型清单响应不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise OpenRouterParseError("模型清单响应顶层不是 dict")
    _write_cache_atomic(cache_path, data)
    return data


def model_choices() -> list[dict[str, str]]:
    """模型清单精简成 ``[{id, name}]``，供设置页下拉。"""
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


# ---------- 模型能力（按 api-response.json 动态决定参数） ----------


def _model_entry(model_id: str) -> dict[str, Any]:
    """从模型清单里取某个模型的原始条目；找不到返空 dict。"""
    try:
        data = list_models()
    except OpenRouterImageError:
        return {}
    for item in data.get("data") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == model_id:
            return item
    return {}


def capabilities_cache_path() -> Path:
    """探测出来的能力缓存：``{data_dir}/openrouter/capabilities.json``。

    模型清单（api-response.json）的 ``supported_parameters`` 不总是准：
    muse-image 声明为空却实际接受 size/aspect_ratio/resolution；
    grok 声明支持 quality 但 provider 拒收。所以实际能不能用要探测，
    探测结果落这里，不重复试错。
    """
    from charvar import paths

    return paths.data_dir() / "openrouter" / "capabilities.json"


def _read_capability_cache() -> dict[str, Any]:
    path = capabilities_cache_path()
    cached = _read_cache(path)
    return cached if isinstance(cached, dict) else {}


def _write_capability_cache(data: dict[str, Any]) -> None:
    try:
        _write_cache_atomic(capabilities_cache_path(), data)
    except OSError as exc:
        logger.warning("openrouter.capability_cache_write_failed | err=%s", exc)


@dataclass
class ModelCapabilities:
    """某个生图模型实际支持的参数。

    Attributes:
        model_id:       模型 slug。
        declared:       清单里 ``supported_parameters`` 声明的参数名集合。
        allowed:        实测确认可用的参数名集合（声明 ∪ 探测结果）。
        rejected:       实测确认 provider 拒收的参数名集合。
        aspect_ratios:  可用的 aspect_ratio 枚举值（未知则空）。
        resolutions:    可用的 resolution 枚举值（未知则空）。
        qualities:      可用的 quality 枚举值（未知则空）。
        supports_refs:  是否支持参考图输入。
    """

    model_id: str
    declared: frozenset[str] = frozenset()
    allowed: frozenset[str] = frozenset()
    rejected: frozenset[str] = frozenset()
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    supports_refs: bool = True

    def supports(self, name: str) -> bool:
        if name in self.rejected:
            return False
        if name in self.allowed:
            return True
        # 清单没声明但也没实测拒过：给个机会让 provider 自己裁决
        return True


def _enum_values(params: dict[str, Any], name: str) -> tuple[str, ...]:
    spec = params.get(name)
    if not isinstance(spec, dict):
        return ()
    values = spec.get("values")
    if not isinstance(values, list):
        return ()
    return tuple(str(v) for v in values)


def model_capabilities(model_id: str) -> ModelCapabilities:
    """某模型的能力：清单声明 + 本地探测缓存合并。

    清单是标准（用户明确要求以 api-response.json 为准），探测结果是对
    清单的补充修正 —— 清单漏声明的（muse 的 size）用探测补上，清单声明
    但 provider 拒收的（grok 的 quality）用探测剔除。
    """
    entry = _model_entry(model_id)
    params = entry.get("supported_parameters")
    params = params if isinstance(params, dict) else {}
    declared = frozenset(params.keys())

    cached = _read_capability_cache().get(model_id)
    cached = cached if isinstance(cached, dict) else {}

    allowed = set(declared) | set(cached.get("allowed") or [])
    rejected = set(cached.get("rejected") or [])

    modalities = ((entry.get("architecture") or {}).get("input_modalities") or [])
    supports_refs = "image" in modalities

    return ModelCapabilities(
        model_id=model_id,
        declared=frozenset(declared),
        allowed=frozenset(allowed),
        rejected=frozenset(rejected),
        aspect_ratios=_enum_values(params, "aspect_ratio") or tuple(cached.get("aspect_ratios") or ()),
        resolutions=_enum_values(params, "resolution") or tuple(cached.get("resolutions") or ()),
        qualities=_enum_values(params, "quality") or tuple(cached.get("qualities") or ()),
        supports_refs=supports_refs,
    )


def record_probe(model_id: str, *, allowed: str | None = None, rejected: str | None = None) -> None:
    """记一次探测结果（provider 拒了某个参数 / 接受了某个参数）。"""
    with _cache_lock:
        cache = _read_capability_cache()
        entry = cache.get(model_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        if allowed:
            entry["allowed"] = sorted(set(entry.get("allowed") or []) | {allowed})
        if rejected:
            entry["rejected"] = sorted(set(entry.get("rejected") or []) | {rejected})
        cache[model_id] = entry
        _write_capability_cache(cache)


def _rejected_parameter(message: str) -> str | None:
    """从 provider 的报错里认出是哪个参数被拒了。"""
    low = str(message or "").lower()
    # "unknown parameter `quality`"
    match = re.search(r"unknown parameter\s+[`'\"]?(\w+)", low)
    if match:
        return match.group(1)
    # "No provider ... supports the requested parameter(s): quality \"high\""
    match = re.search(r"parameter\(s\):\s*(\w+)", low)
    if match:
        return match.group(1)
    # "Black Forest Labs FLUX 3 Image does not support: seed."
    match = re.search(r"does not support:\s*(\w+)", low)
    if match:
        return match.group(1)
    # "… takes a resolution tier (768, 1K, …) plus aspect_ratio, not pixel
    # dimensions. Received size \"1344x2048\"." —— 拒的是 size（像素画幅）
    match = re.search(r"not pixel dimensions", low)
    if match:
        return "size"
    # "Image size '1024x448' is not supported" / "quality: not supported"
    match = re.search(r"\bsize\b[^.]*is not supported", low)
    if match:
        return "size"
    match = re.search(r"(\w+)\s*:\s*not supported", low)
    if match:
        return match.group(1)
    return None



# ---------- 提示词改写 ----------


def rewrite_reference_tags(text: str) -> str:
    """把提示词里的 ``<imageN>`` 换成序数词，句子保持完整。

    - ``in the character sheet in <image1>`` → ``in the character sheet``
      （指向词去掉、名词保留），再把残留的 ``<imageN>`` 换成序数词。
    - 其余标签一律直替成序数词：``exactly as shown in <image1>`` →
      ``exactly as shown in the first reference image``（语法完整）。
    - 删标签会让句子残缺（"exactly as shown in ;"），序数词不会。
    """
    if not text:
        return ""
    text = _CHARACTER_SHEET_RE.sub("in the character sheet", text)
    text = _IMAGE_TAG_RE.sub(_ordinal, text)
    return re.sub(r"\s+", " ", text).strip()


def strip_reference_tags(text: str) -> str:
    """无参考图时：把 ``<imageN>`` 指向子句整段摘掉。

    预设里的指向句式有几种（``Use <image1> as the reference for …``、
    ``from <image1>``、``as <image1>``…）。只删标签会留下残句
    （"featuring the same single character as ."），整段摘掉才干净。
    """
    if not text:
        return ""
    for pattern in _NO_REF_SENTENCES:
        text = pattern.sub("", text)
    for pattern in _NO_REF_PHRASES:
        text = pattern.sub("", text)
    text = _IMAGE_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _ordinal(match: re.Match[str]) -> str:
    index = int(match.group(1))
    if 1 <= index <= len(_ORDINALS):
        return _ORDINALS[index - 1]
    return "the reference image"


# OpenRouter 的 seed 是 JSON 整数，provider 侧按 JS 安全整数校验
# （<= 2^53-1），超了直接 400。charvar 的 subseed 是 sha256 前 8 字节
# （64 位），必须收敛到安全范围。
_MAX_SAFE_SEED = 2**53 - 1


def normalize_seed(seed: int) -> int:
    """把种子收敛到 OpenRouter 接受的范围（0 … 2^53-1）。

    取模而不是截断：截断会丢掉高位，模运算保持"同种子 → 同结果"，
    不同种子之间仍散得开。
    """
    return int(seed) % (_MAX_SAFE_SEED + 1)


# ---------- 参考图 ----------


def encode_reference(path: Path, *, max_side: int = _REFERENCE_MAX_SIDE) -> str:
    """本地图片 → data URL（按统一规格压缩后的 JPEG）。

    走 data URL 而不是 HTTP URL：参考图是本机文件，没有对外可访问的地址。

    压缩规格与 ComfyUI 侧共用（见 ``charvar.reference_images``）：长边 1536、
    JPEG q88。带透明通道的参考图先白底合成 —— 直接 ``convert("RGB")``
    会把透明区域变黑，污染参考图内容。
    """
    from charvar import reference_images

    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        flat = reference_images.flatten_on_white(image)
        if max(flat.size) > max_side:
            flat.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        flat.save(buffer, format="JPEG", quality=_REFERENCE_JPEG_QUALITY, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def build_input_references(paths: list[Path]) -> list[dict[str, Any]]:
    """本地参考图 → OpenRouter ``input_references`` 结构（顺序即标签顺序）。"""
    references: list[dict[str, Any]] = []
    for path in paths[:_MAX_REFERENCES]:
        references.append(
            {"type": "image_url", "image_url": {"url": encode_reference(path)}}
        )
    return references


# ---------- 请求体 ----------


def build_generate_body(
    *,
    prompt: str,
    model: str,
    width: int,
    height: int,
    seed: int | None = None,
    reference_paths: list[Path] | None = None,
    capabilities: ModelCapabilities | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> dict[str, Any]:
    """组装 ``POST /api/v1/images`` 的请求体。

    画幅默认走 ``size`` 显式像素（``"{width}x{height}"``）——``size`` 是
    OpenRouter 层的通用参数，实测多被吃（tencent / qwen 按像素精确输出）。
    但清单里**声明了参数却没列 ``size``** 的模型（如 flux-3）只收
    「分辨率档 + ``aspect_ratio``」，发 ``size`` 直接 400 —— 这种模型改发
    ``aspect_ratio``（值须在模型枚举内）。清单完全没声明（如 muse）当未知
    处理，仍发 ``size``；探测缓存里记过 ``size`` 被拒的也换道。没有可用的
    ``aspect_ratio`` 表达时仍保底发 ``size``，由 :func:`generate_image` 的
    自动降级重试兜底。

    ``resolution`` / ``quality`` / ``seed`` 只在模型清单里**声明**支持时才发：
    清单是标准（用户明确要求以 api-response.json 为准），muse-image 虽
    接受 ``resolution`` 但输出像素完全不变（1K 与 4K 同为 1920×1280），
    发了反而误导；清单完全没声明的模型（如 muse）当未知处理，seed 照发，
    provider 拒收时由 :func:`generate_image` 自动降级重试。
    """
    if not prompt or not prompt.strip():
        raise OpenRouterParamError("prompt 不能为空")
    if not model or not str(model).strip():
        raise OpenRouterParamError("model 不能为空（设置 OP_IMAGE_MODEL 或显式传入）")
    if width <= 0 or height <= 0:
        raise OpenRouterParamError(f"画幅尺寸非法：{width}x{height}")

    caps = capabilities or ModelCapabilities(model_id=model)
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt.strip(),
    }
    # seed 的清单语义：**显式声明了却没列 seed**（如 flux-3-image-klein）→ 不发，
    # provider 会直接 400；清单完全没声明任何参数（如 muse）当未知处理，照发，
    # provider 拒收时由 _post_generate 的自动降级兜底。
    if seed is not None and (not caps.declared or "seed" in caps.declared):
        body["seed"] = normalize_seed(seed)
    if reference_paths and caps.supports_refs:
        body["input_references"] = build_input_references(reference_paths)

    # 画幅的清单语义与 seed 同构：声明了参数却没列 size（flux-3 这类只收
    # 「分辨率档 + aspect_ratio」）→ 改发 aspect_ratio（值在模型枚举内才发）；
    # 探测缓存记过 size 被拒的一并换道。清单为空或没有可用的 aspect 表达时
    # 仍发 size —— 它是像素精确的通路，错报由 _post_generate 降级兜底。
    size_supported = (not caps.declared or "size" in caps.declared) and "size" not in caps.rejected
    aspect_ok = aspect_ratio and (not caps.aspect_ratios or aspect_ratio in caps.aspect_ratios)
    if size_supported or not aspect_ok:
        body["size"] = f"{width}x{height}"
    else:
        body["aspect_ratio"] = aspect_ratio

    provider_resolution = _provider_resolution(resolution, caps)
    if provider_resolution:
        body["resolution"] = provider_resolution
    provider_quality = _provider_quality(quality, caps)
    if provider_quality:
        body["quality"] = provider_quality
    return body


# charvar 的分辨率档位 → provider 清单里的写法（大写 K）。
# 4k 不在 charvar 档位表里（主体图已下线该档），但 provider 清单（gemini/flux 等）
# 有 "4K" 枚举值，面板/env 选了就原样发 —— 这里只是值归一，不是开 4K 档。
_RESOLUTION_TO_PROVIDER = {"1k": "1K", "1.5k": "1.5K", "2k": "2K", "4k": "4K"}


def _provider_resolution(resolution: str | None, caps: ModelCapabilities) -> str | None:
    """resolution 输入 → provider 的 resolution 值；不在模型枚举里就不发。

    输入两种写法都收：
    - charvar 档位（"1k" / "2k"，顺带 "1.5k" / "4k"），归一成大写 K；
    - provider 枚举原样（"512" / "1K" / "1.5K" / "4K"，大小写不敏感）。
    枚举匹配时用清单里的原始写法回传（避免大小写被 provider 拒）。
    """
    value = str(resolution or "").strip()
    if not value:
        return None
    value = _RESOLUTION_TO_PROVIDER.get(value.lower(), value)
    if caps.resolutions:
        for allowed in caps.resolutions:
            if allowed.lower() == value.lower():
                return allowed
        return None
    if "resolution" not in caps.declared:
        # 清单完全没声明（如 muse-image）→ 不发：发了也只是 provider 自由裁量
        return None
    return value


def _provider_quality(quality: str | None, caps: ModelCapabilities) -> str | None:
    """质量档：只有模型声明了 quality 且值在枚举内才发；"auto" = 不发。"""
    if not quality:
        return None
    value = str(quality).strip().lower()
    if not value or value == "auto":
        return None
    if caps.qualities:
        for allowed in caps.qualities:
            if allowed.lower() == value.lower():
                return allowed
        return None
    if "quality" not in caps.declared:
        return None
    return value


def pixel_resolution_tier(
    model: str,
    resolution: str | None = None,
    *,
    fallback_tier: str = "1k",
) -> str:
    """选「用来算 size 像素」的档位字符串（长边解析在 exporters.dimensions_for）。

    三条通路（主体档案 / 批量推送 / Playground）同一套规则：
    - `resolution` 输入能映射进模型枚举 → 用归一后的 provider 值
      （"1K"/"2K"/"1.5K"/"4K"…，exporters 会按 1K=1024 长边换算像素）；
    - 输入是 charvar 档位但模型没声明 resolution → 用档位本身
      （照旧只算像素，provider 参数不发）；
    - 都不是（空 / 无效枚举值）→ 回退 ``fallback_tier``，不让无效值撑大画幅。
    """
    raw = str(resolution or "").strip()
    try:
        caps = model_capabilities(model)
    except OpenRouterImageError:
        caps = ModelCapabilities(model_id=model)
    provider_value = _provider_resolution(raw, caps)
    if provider_value:
        return provider_value
    if raw.lower() in _RESOLUTION_TO_PROVIDER:
        return raw.lower()
    return fallback_tier



# ---------- 生图 ----------


def sniff_image_ext(data: bytes) -> str:
    """按魔数嗅探图片格式，返回扩展名（png/jpg/webp/bin）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "bin"


def generate_image(
    *,
    prompt: str,
    width: int,
    height: int,
    model: str | None = None,
    api_key: str | None = None,
    seed: int | None = None,
    reference_paths: list[Path] | None = None,
    timeout_s: float | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
) -> GenerateImageResult:
    """调 OpenRouter ``POST /api/v1/images`` 生成一张图。

    Args:
        prompt: 图片提示词（必填）。
        width / height: 输出像素尺寸（必填），拼成 ``size`` 参数。
        model: 模型 slug；缺省用 ``OP_IMAGE_MODEL``。
        api_key: OpenRouter API key；缺省用 ``OP_IMAGE_API_KEY`` / ``LLM_API_KEY``。
        seed: 采样种子（provider 不保证确定性，只是尽量可复现）。
        reference_paths: 本地参考图（按顺序对应提示词里的 the first/second… reference image）。
        timeout_s: HTTP 超时（秒）；缺省 ``OP_IMAGE_TIMEOUT`` 或 300。
        aspect_ratio: 比例（降级重试时用）。
        resolution: 分辨率档（只有模型声明支持才发）。
        quality: 质量档（只有模型声明支持才发）。

    Raises:
        OpenRouterParamError: 参数缺失 / 非法。
        OpenRouterAuthError: 401 / 402 / 非内容策略的 403。
        OpenRouterFetchError: 网络失败，或 provider 用 403 拒绝违规 prompt。
        OpenRouterRateLimitError: 429。
        OpenRouterNotFoundError: 404。
        OpenRouterParseError: 响应缺 ``data[].b64_json`` 等关键结构。
    """
    model_id = resolve_model(model)
    key = resolve_api_key(api_key)
    if not key:
        raise OpenRouterParamError(
            "缺少 OpenRouter API Key：请设置 OP_IMAGE_API_KEY / LLM_API_KEY，或在「设置」页配置"
        )

    caps = model_capabilities(model_id)
    body = build_generate_body(
        prompt=prompt,
        model=model_id,
        width=width,
        height=height,
        seed=seed,
        reference_paths=reference_paths,
        capabilities=caps,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        quality=quality,
    )

    payload, body = _post_generate(
        body,
        key=key,
        timeout_s=timeout_s,
        model_id=model_id,
        fallback_aspect_ratio=aspect_ratio,
    )

    created = int(payload.get("created") or int(time.time()))
    raw_items = payload.get("data")
    if not isinstance(raw_items, list) or not raw_items:
        raise OpenRouterParseError(
            f"生图响应缺 data 数组：{json.dumps(payload, ensure_ascii=False)[:300]}"
        )
    images: list[bytes] = []
    media_types: list[str] = []
    for i, item in enumerate(raw_items):
        b64_text = item.get("b64_json") if isinstance(item, dict) else None
        if not isinstance(b64_text, str) or not b64_text:
            raise OpenRouterParseError(f"生图响应 data[{i}] 缺 b64_json 字段")
        try:
            images.append(base64.b64decode(b64_text))
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterParseError(f"b64_json 解码失败：{exc}") from exc
        media_types.append(str(item.get("media_type") or ""))

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    logger.info(
        "openrouter.image_generated | model=%s | n=%d | cost=%s",
        model_id,
        len(images),
        usage.get("cost"),
    )
    return GenerateImageResult(
        generation_id=f"{created}_{uuid.uuid4().hex[:8]}",
        created=created,
        images=images,
        media_types=media_types,
        usage=usage,
        request_body=body,
    )


def _post_generate(
    body: dict[str, Any],
    *,
    key: str,
    timeout_s: float | None,
    model_id: str,
    fallback_aspect_ratio: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """POST 生图；provider 拒了某个参数就摘掉它重试一次（自动降级）。

    降级顺序按"对画幅影响从小到大"：先摘 ``quality`` / ``resolution``
    （锦上添花），再摘 ``size`` 改用 ``aspect_ratio``（画幅从像素精确退化为
    比例枚举）。被摘掉的参数记进能力缓存，下次同一模型直接不发了。

    Returns:
        (响应 payload, 实际发出去并成功的请求体)
    """
    attempts: list[dict[str, Any]] = [dict(body)]
    tried: set[str] = set()

    while True:
        current = attempts[-1]
        try:
            with _http_client(resolve_timeout(timeout_s)) as client:
                resp = client.post(
                    _GENERATE_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=current,
                )
        except httpx.HTTPError as exc:
            raise OpenRouterFetchError(f"请求 OpenRouter 生图失败：{exc}") from exc

        if resp.status_code == 200:
            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise OpenRouterParseError(f"生图响应不是合法 JSON：{exc}") from exc
            if isinstance(payload, dict):
                return payload, current
            raise OpenRouterParseError("生图响应顶层不是 dict")

        error = _error_from_response(resp)
        # 只对"参数不被接受"降级；鉴权/限流/内容策略这类重试没意义
        if not isinstance(error, OpenRouterFetchError):
            raise error
        rejected = _rejected_parameter(str(error))
        if not rejected or rejected in tried:
            raise error
        tried.add(rejected)
        record_probe(model_id, rejected=rejected)
        logger.info(
            "openrouter.param_degraded | model=%s | rejected=%s", model_id, rejected
        )

        degraded = dict(current)
        degraded.pop(rejected, None)
        if rejected == "size" and fallback_aspect_ratio:
            # size 被拒 → 换成 aspect_ratio（画幅从像素精确退化为比例枚举）
            degraded["aspect_ratio"] = fallback_aspect_ratio
        attempts.append(degraded)

