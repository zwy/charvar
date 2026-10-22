"""临时生图：给一段提示词（+ 可选参考图）直接出一张图。

这是给「本项目之外」用的一条通路：用户档案的文档生成环节里，
提示词是手工复制到即梦 AI / 本地 ComfyUI 出图的；这里把它变成一条命令。

与既有两条通路的区别：
- 主体档案 5 槽位（``charvar.subject_images``）——槽位固定、依赖 character_sheet 先就绪；
- 预设批量（``charvar.api`` 的 /runs/{id}/comfyui）——一次几十条变体；
- 本模块——一次一张、提示词原样使用、不依赖档案也不落 runs 表。

落盘：``data/generated/adhoc/{id}/``（original/ + thumbs/ + manifest.json）。
manifest 记完整请求与结果，便于复现；产物不落 runs 表（临时性质，
图片墙/contact sheet 不会扫到，这是预期的）。

红线（见 AGENTS.md「实现红线」）：
- ComfyUI / OpenRouter 请求一律走既有客户端（``trust_env=False``，绕 shell 代理）；
- ComfyUI 节点 id 与参考图槽位名都从用户 mapping.json 读，本模块不出现任何节点 id；
- 参考图统一走 ``charvar.reference_images`` 预处理（长边 1536 / JPEG q88 / 白底合成）；
- OpenRouter 画幅走显式像素（provider 的 aspect_ratio 枚举不含 21:9），seed 由
  ``build_generate_body`` 内部收敛到 2^53-1；
- 不引入随机源：seed 一律由调用方显式给定。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from charvar import assets, openrouter_image, paths, reference_images, settings_service, storage
from charvar.exporters import comfyui as comfyui_exporter
from charvar.models import GenerateResult, PromptItem

Backend = Literal["comfyui", "openrouter"]

# 参考图上限：OpenRouter 的 input_references 最多 4 张（openrouter_image._MAX_REFERENCES），
# ComfyUI edit workflow 的 LoadImage 槽位也是 4 个。两边取小的。
_MAX_REFERENCES = 4

# 画幅档位与 comfyui_exporter.SUPPORTED_ASPECT_RATIOS 一致（去掉 auto —— adhoc 没有景别概念）
_ASPECT_RATIOS = ("1:1", "3:4", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9")
_RESOLUTIONS = ("1k", "2k")


class AdhocError(ValueError):
    """参数或环境错误（CLI 层转成友好提示）。"""


class AdhocRequest(BaseModel):
    """一次临时生图的请求。"""

    prompt: str
    backend: Backend
    negative_prompt: str | None = None
    reference_paths: list[Path] = Field(default_factory=list)
    aspect_ratio: str = "3:4"
    resolution: str = ""  # 空 = 设置页/env 的模式默认（OpenRouter）/ 1k（ComfyUI）；1k/2k 或 provider 枚举值
    seed: int = 0
    model: str | None = None
    quality: str | None = None
    comfy_url: str | None = None
    workflow: str | None = None
    wait: bool = True
    out: Path | None = None


class AdhocResult(BaseModel):
    """一次临时生图的结果。"""

    image_path: str
    thumb_path: str | None = None
    provider: Backend
    manifest_path: str
    width: int
    height: int
    seed: int
    model: str | None = None
    prompt_id: str | None = None
    cost: float | None = None
    duration_ms: int = 0
    status: Literal["ready", "queued"] = "ready"


# ---------- 落盘目录 ----------

def adhoc_root() -> Path:
    """临时生图根目录：``<data>/generated/adhoc/``。"""
    return paths.data_dir() / "generated" / "adhoc"


def refs_dir() -> Path:
    """网页上传的参考图暂存目录（``_refs/``）—— 出了图就没用了，可随时清。"""
    return adhoc_root() / "_refs"


def _new_run_id() -> str:
    """本次生成的 id：只含 ``[A-Za-z0-9_.-]``（assets.safe_run_id 的字符集约束）。"""
    return f"adhoc_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"


# run_id 必须是「adhoc_ 前缀 + 安全字符集」。比 assets.safe_run_id 严：
# safe_run_id 的正则放行 ".."，而这里 run_id 直接拼进路径（拼 ``..`` 就穿出去了）。
_RUN_ID_RE = re.compile(r"^adhoc_[A-Za-z0-9_.-]+$")


def _run_dir(run_id: str) -> Path:
    return adhoc_root() / run_id


def run_dir(run_id: str) -> Path:
    """公开的 run 目录（对外服务产物用；非法 id 直接报错，不拼路径）。"""
    if not _RUN_ID_RE.fullmatch(str(run_id)) or ".." in str(run_id):
        raise AdhocError(f"非法 run_id：{run_id}")
    return _run_dir(run_id)


# ---------- 参考图暂存（网页上传用） ----------

_STAGED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def save_staged_reference(data: bytes, filename: str = "") -> Path:
    """把上传的参考图存到暂存目录，返回路径。

    后缀按**实际图片格式**判（用 PIL 读），不信任上传时的文件名 ——
    "a.png" 里装的是 JPEG 就按 .jpg 存，否则下游按后缀走会出错。
    """
    import io

    from PIL import Image

    if not data:
        raise AdhocError("参考图是空的")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or "").upper()
    except Exception as exc:  # noqa: BLE001
        raise AdhocError(f"参考图不是可识别的图片：{exc}") from exc
    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(fmt)
    if suffix is None:
        raise AdhocError(f"不支持的参考图格式：{fmt or '未知'}")
    target = refs_dir() / f"{uuid.uuid4().hex}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def staged_reference(ref_id: str) -> Path:
    """按 id 取暂存的参考图路径（非法 id 报错）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", str(ref_id or "")):
        raise AdhocError(f"非法参考图 id：{ref_id}")
    matches = sorted(refs_dir().glob(f"{ref_id}.*"))
    for path in matches:
        if path.suffix.lower() in _STAGED_SUFFIXES and path.is_file():
            return path
    raise AdhocError(f"参考图不存在或已清理：{ref_id}")


def clear_staged_references(ref_ids: list[str]) -> None:
    """删掉用完的暂存参考图（出图成功后调用，失败时保留以便重试）。"""
    for ref_id in ref_ids or []:
        try:
            staged_reference(ref_id).unlink(missing_ok=True)
        except AdhocError:
            continue


def prune_staged_references(max_age_s: float = 24 * 3600) -> int:
    """清掉过期未用的暂存参考图，返回删掉几个。

    用户上传了参考图却没点出图就直接关页面，暂存文件会一直留着 ——
    按 mtime 过期清理（出图成功时本来就会立刻删掉，这里兜底）。
    """
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


# ---------- 产物 ----------

def original_file(run_id: str) -> Path:
    """某次临时生图的原图（``original/`` 下唯一一张）。"""
    directory = run_dir(run_id) / "original"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有产物：{run_id}")


def thumb_file(run_id: str) -> Path:
    """某次临时生图的缩略图。"""
    directory = run_dir(run_id) / "thumbs"
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if path.is_file():
            return path
    raise FileNotFoundError(f"没有缩略图：{run_id}")


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    """最近的临时生图记录（读 manifest.json，按时间倒序）。

    manifest 是唯一留痕（不落 runs 表），所以这里就是扫目录。
    """
    root = adhoc_root()
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
        result = manifest.get("result") or {}
        request = manifest.get("request") or {}
        status = "error" if manifest.get("error") else (result.get("status") or "ready")
        thumbs = directory / "thumbs"
        has_image = status == "ready" and thumbs.is_dir() and any(thumbs.iterdir())
        records.append(
            {
                "run_id": directory.name,
                "created_at": manifest.get("created_at") or "",
                "backend": request.get("backend") or result.get("provider") or "",
                "prompt": request.get("prompt") or "",
                "aspect_ratio": request.get("aspect_ratio") or "",
                "seed": request.get("seed"),
                "width": result.get("width") or 0,
                "height": result.get("height") or 0,
                "status": status,
                "error": manifest.get("error") or "",
                # 只入队（--no-wait）与失败的记录没有产物 → 前端别去取图（否则是破图）
                "has_image": has_image,
            }
        )
    records.sort(key=lambda r: r["run_id"], reverse=True)
    return records[: max(1, int(limit))]



# ---------- 校验 ----------

# 分辨率两种写法都收：charvar 档位（1k/2k）或 provider 清单的枚举值（512/1K/1.5K/2K/4K…）。
# 枚举值能不能发由 openrouter_image 按所选模型 supported_parameters 动态校验。
_PROVIDER_RES_RE = re.compile(r"^\d+(?:\.\d+)?\s*[kK]?$")


def _validate_resolution(value: str | None) -> str | None:
    """分辨率格式校验；空 = 跟随设置页/env 的模式默认，放行。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower() in _RESOLUTIONS or _PROVIDER_RES_RE.fullmatch(raw):
        return raw
    raise AdhocError(
        f"分辨率只支持 {'/'.join(_RESOLUTIONS)} 或模型声明的枚举值"
        f"（512 / 1K / 1.5K / 2K / 4K…），当前：{value}"
    )


def validate(req: AdhocRequest) -> None:
    """请求自检：能在发请求前发现的错就别浪费一次出图。"""
    if not req.prompt.strip():
        raise AdhocError("提示词为空")
    if req.aspect_ratio not in _ASPECT_RATIOS:
        raise AdhocError(f"画幅只支持 {'/'.join(_ASPECT_RATIOS)}，当前：{req.aspect_ratio}")
    _validate_resolution(req.resolution)
    if len(req.reference_paths) > _MAX_REFERENCES:
        raise AdhocError(f"参考图最多 {_MAX_REFERENCES} 张，收到 {len(req.reference_paths)} 张")
    for path in req.reference_paths:
        if not Path(path).exists():
            raise AdhocError(f"参考图不存在：{path}")
    if req.comfy_url and not req.comfy_url.startswith(("http://", "https://")):
        # 缺 scheme 会拼出 "192.168.1.174:8188/prompt" 这种非法 URL，
        # httpx 抛 UnsupportedProtocol —— 报给用户很难懂，这里先拦下。
        raise AdhocError(f"ComfyUI 地址必须带 http:// 或 https://：{req.comfy_url}")


# ---------- 临时 GenerateResult ----------

def to_generate_result(req: AdhocRequest, run_id: str) -> GenerateResult:
    """构造一次性的 GenerateResult（不落 runs 表）。

    仿 ``charvar.subject_images._synthetic_result``：单 item，index 必须是 0 ——
    ComfyUI 的 seed 走 mapping 的 ``mode: "increment"``（``result.seed + index``），
    index=0 时注入的 seed 恰好等于用户给的 seed。
    """
    item = PromptItem(
        index=0,
        variant_id="adhoc",
        prompt=req.prompt.strip(),
        master="",
        variant_text="",
        background=None,
        lighting=None,
        caption="",
        meta={"preferred_aspect_ratio": req.aspect_ratio, "adhoc": True},
    )
    return GenerateResult(
        run_id=run_id,
        subject_id=run_id,
        preset="adhoc",
        seed=req.seed,
        items=[item],
    )


# ---------- 参考图 ----------

def _prepare_references(req: AdhocRequest, run_dir: Path) -> list[Path]:
    """参考图统一预处理（长边 1536 / JPEG q88 / 透明白底合成）。

    走 ``charvar.reference_images`` —— ComfyUI 上传与 OpenRouter 发送共用同一份压缩规格，
    别再写新的压缩逻辑。adhoc 每次是新目录，没有跨次缓存的必要，直接 prepare_reference。
    """
    if not req.reference_paths:
        return []
    cache_dir = run_dir / "_ref_cache"
    prepared: list[Path] = []
    for index, source in enumerate(req.reference_paths):
        destination = cache_dir / f"ref{index}.jpg"
        prepared.append(reference_images.prepare_reference(Path(source), destination))
    return prepared


def _image_slots(mapping: dict[str, Any]) -> list[str]:
    """参考图槽位名（从 mapping 读，不硬编码节点 id）。

    edit workflow 的 mapping 里是 image / image2 / image3 / image4 四个字段，
    各自指向一个 LoadImage 节点。读 mapping 而不是写死，换 workflow 不会碎。
    """
    slots: list[str] = []
    for entry in mapping.get("mappings", []):
        if not isinstance(entry, dict):
            continue
        field = str(entry.get("field") or "")
        if field == "image" or (field.startswith("image") and field[len("image"):].isdigit()):
            slots.append(field)
    # 保持 mapping 里的声明顺序（image → image2 → image3 → image4）
    return slots


# ---------- ComfyUI ----------

def _generate_comfyui(
    req: AdhocRequest, run_id: str, run_dir: Path, db_path: Path | None = None
) -> AdhocResult:
    t2i_workflow, edit_workflow = _default_workflows(db_path)
    workflow_name = req.workflow or (
        edit_workflow if req.reference_paths else t2i_workflow
    )
    try:
        workflow, mapping = comfyui_exporter.load_workflow_pair(workflow_name)
    except FileNotFoundError as exc:
        raise AdhocError(str(exc)) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdhocError(f"workflow/mapping 无效：{exc}") from exc

    prepared = _prepare_references(req, run_dir)
    overrides: dict[str, Any] = {}
    if prepared:
        slots = _image_slots(mapping)
        if not slots:
            raise AdhocError(f"workflow {workflow_name} 没有参考图槽位，无法带参考图")
        if len(prepared) > len(slots):
            raise AdhocError(
                f"workflow {workflow_name} 只支持 {len(slots)} 张参考图，收到 {len(prepared)} 张"
            )
        base = paths.comfy_base_url(req.comfy_url)
        for index, image in enumerate(prepared):
            # remote_name 必须唯一：upload_image 发 overwrite=true，ComfyUI 侧同名即覆盖。
            # 固定名会让第二次调用覆盖第一次的图，队列里上一轮未执行的 prompt 会取到新图。
            remote = comfyui_exporter.upload_image(
                base, image, remote_name=f"{run_id}_ref{index}{image.suffix}"
            )
            overrides[slots[index]] = remote
    if req.negative_prompt is not None:
        # 显式值（含空串）生效：空串 = 明确不要负向词，别被 "if not value" 吞掉
        overrides["negative_prompt"] = req.negative_prompt

    result = to_generate_result(req, run_id)
    base = paths.comfy_base_url(req.comfy_url)
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
    entry = submissions[0] if submissions else {}
    if not entry.get("ok"):
        raise AdhocError(str(entry.get("error") or "ComfyUI 提交失败"))
    prompt_id = str(entry.get("prompt_id") or "") or None

    if not req.wait:
        return AdhocResult(
            image_path="",
            provider="comfyui",
            manifest_path="",
            width=0,
            height=0,
            seed=req.seed,
            prompt_id=prompt_id,
            status="queued",
        )

    files = entry.get("files") or []
    if not files:
        raise AdhocError("ComfyUI 未返回输出文件")
    # 走 comfyui_exporter 的客户端（trust_env=False，绕 shell 代理）——
    # 别自己 httpx.Client()，否则 ALL_PROXY 会劫持内网地址。
    with comfyui_exporter._httpx_client(timeout=30.0) as client:
        content = _download_bytes(client, base, files[0])
    suffix = Path(str(files[0].get("filename") or "")).suffix or ".png"
    destination = run_dir / "original" / f"000000{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    thumb = assets.make_thumb(destination, run_dir / "thumbs" / "000000.webp")
    width, height = _image_size(content)
    return AdhocResult(
        image_path=str(destination),
        thumb_path=str(thumb),
        provider="comfyui",
        manifest_path="",
        width=width,
        height=height,
        seed=req.seed,
        prompt_id=prompt_id,
    )


def _download_bytes(client: httpx.Client, base: str, file: dict[str, str]) -> bytes:
    params = {
        "filename": file.get("filename", ""),
        "subfolder": file.get("subfolder", ""),
        "type": file.get("type", "output"),
    }
    resp = client.get(f"{base.rstrip('/')}/view", params=params, timeout=15)
    resp.raise_for_status()
    if not resp.content:
        raise AdhocError(f"ComfyUI 输出为空：{file}")
    return resp.content


# ---------- OpenRouter ----------

def _generate_openrouter(
    req: AdhocRequest, run_id: str, run_dir: Path, db_path: Path | None = None
) -> AdhocResult:
    prepared = _prepare_references(req, run_dir)
    with _conn(db_path) as conn:
        modes = settings_service.openrouter_mode_config(conn)
        api_key = openrouter_image.resolve_api_key(settings_service.get_settings(conn).openrouter.api_key or None)
    # 模式对齐 ComfyUI：带参考图 = 图片编辑（i2i / edit workflow），否则文生图（t2i）
    mode = "i2i" if prepared else "t2i"
    mcfg = modes[mode]
    model = openrouter_image.resolve_model((req.model or "").strip() or mcfg["model"], mode=mode)
    # 参数默认链：请求显式 > 设置页/env 的模式默认；枚举校验在 openrouter_image 里做
    resolution = (req.resolution or "").strip() or mcfg["resolution"]
    quality = (req.quality or "").strip() or mcfg["quality"]
    if not api_key:
        raise AdhocError(
            "缺少 OpenRouter API Key：请设置 OP_IMAGE_API_KEY / LLM_API_KEY，或在「设置」页配置"
        )

    result = to_generate_result(req, run_id)
    item = result.items[0]
    # OpenRouter 跑的是 muse/grok/gpt-image，不是 Qwen —— 不走官方 2K 像素表；
    # 档位收 charvar 档位与 provider 枚举值（1K/1.5K/4K…）
    pixel_tier = openrouter_image.pixel_resolution_tier(model, resolution)
    width, height = comfyui_exporter.dimensions_for_item(
        req.aspect_ratio, pixel_tier, item, use_model_native=False
    )

    # 有参考图时提示词里的 <imageN> 换成序数词；没有参考图时整段摘掉
    # （留着会变成 "as ." 这种残句）。同 charvar.api._openrouter_prompt。
    prompt = (
        openrouter_image.rewrite_reference_tags(req.prompt.strip())
        if prepared
        else openrouter_image.strip_reference_tags(req.prompt.strip())
    )
    try:
        op_result = openrouter_image.generate_image(
            prompt=prompt,
            width=width,
            height=height,
            model=model,
            api_key=api_key,
            seed=req.seed,
            reference_paths=prepared,
            aspect_ratio=req.aspect_ratio,
            resolution=resolution,
            quality=quality,
        )
    except openrouter_image.OpenRouterImageError as exc:
        raise AdhocError(f"OpenRouter 生图失败：{exc}") from exc
    if not op_result.images:
        raise AdhocError("OpenRouter 未返回图片")

    data = op_result.images[0]
    suffix = openrouter_image.sniff_image_ext(data)
    if suffix == ".bin":
        raise AdhocError("OpenRouter 返回的不是可识别的图片格式")
    destination = run_dir / "original" / f"000000.{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    thumb = assets.make_thumb(destination, run_dir / "thumbs" / "000000.webp")
    actual_w, actual_h = _image_size(data)
    return AdhocResult(
        image_path=str(destination),
        thumb_path=str(thumb),
        provider="openrouter",
        manifest_path="",
        width=actual_w or width,
        height=actual_h or height,
        seed=openrouter_image.normalize_seed(req.seed),
        model=model,
        cost=op_result.cost,
    )


# ---------- 公共工具 ----------

def _conn(db_path: Path | None = None):
    return storage.connect(db_path)


def _settings(db_path: Path | None = None):
    with _conn(db_path) as conn:
        return settings_service.get_settings(conn)


def _default_workflows(db_path: Path | None = None) -> tuple[str, str]:
    """（文生图 workflow, 图像编辑 workflow）—— 从设置页取。"""
    settings = _settings(db_path)
    return settings.comfy_t2i_workflow, settings.comfy_edit_workflow


def _image_size(data: bytes) -> tuple[int, int]:
    """读图片实际像素尺寸（失败返 0,0）。"""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - 尺寸读不到不该让整次生图失败
        return 0, 0


def _write_out(result: AdhocResult, out: Path | None) -> None:
    """``--out`` 指定路径时再落一份出去。

    自己写而不是走 ``assets._download_output``：那个函数会
    ``destination.with_suffix(suffix)`` 改写后缀（--out foo.png 可能落成 foo.webp），
    而用户指定 --out 就是指定那个文件。

    后端给的字节格式与用户要的后缀可能对不上（OpenRouter 的 muse-image 回 webp，
    用户却写了 --out x.png）—— 那样文件后缀就在骗人了，下游按后缀读会出错。
    所以后缀与实际格式不一致时转一下格式，转不了才原样写。
    """
    if out is None:
        return
    source = Path(result.image_path)
    if not source.exists():
        raise AdhocError(f"产物不存在，无法复制到 {out}：{source}")
    destination = Path(out).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.suffix:
        destination = destination.with_suffix(source.suffix)
    data = source.read_bytes()
    converted = _convert_format(data, destination.suffix)
    destination.write_bytes(converted if converted is not None else data)
    result.image_path = str(destination)


# 用户可以指定的后缀 → Pillow 的格式名
_OUT_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}


def _convert_format(data: bytes, suffix: str) -> bytes | None:
    """把图片转成目标后缀对应的格式；不需要转或转不了时返 None（调用方原样写）。"""
    target = _OUT_FORMATS.get(suffix.lower())
    if target is None:
        return None
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format and image.format.upper() == target:
                return None  # 格式本来就对，不用转
            buffer = io.BytesIO()
            # JPEG 不支持透明通道，先白底合成（与参考图预处理同一策略）
            if target == "JPEG" and image.mode in ("RGBA", "LA", "P"):
                image = reference_images.flatten_on_white(image.convert("RGBA"))
            image.save(buffer, format=target)
    except Exception:  # noqa: BLE001 - 转不了就原样写，不让出图失败
        return None
    return buffer.getvalue()


def _write_manifest(
    run_dir: Path,
    req: AdhocRequest,
    result: AdhocResult,
    error: str = "",
) -> Path:
    manifest = {
        "run_id": run_dir.name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "request": {
            "backend": req.backend,
            "prompt": req.prompt,
            "negative_prompt": req.negative_prompt,
            "reference_paths": [str(p) for p in req.reference_paths],
            "aspect_ratio": req.aspect_ratio,
            "resolution": req.resolution,
            "seed": req.seed,
            "model": req.model,
            "quality": req.quality,
            "workflow": req.workflow,
        },
        "result": result.model_dump(),
        "error": error,
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------- 对外入口 ----------

def generate(req: AdhocRequest, db_path: Path | None = None) -> AdhocResult:
    """跑一次临时生图，返回结果（含落盘路径与 manifest 路径）。

    ``db_path`` 传了就用那个库读设置（测试隔离用）；不传走默认库。
    """
    validate(req)
    run_id = _new_run_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        if req.backend == "comfyui":
            result = _generate_comfyui(req, run_id, run_dir, db_path)
        else:
            result = _generate_openrouter(req, run_id, run_dir, db_path)
    except Exception as exc:
        # 失败也要留痕：manifest 记下失败原因，方便回头查（出图常在别处失败）
        _write_manifest(
            run_dir,
            req,
            AdhocResult(
                image_path="", provider=req.backend, manifest_path="",
                width=0, height=0, seed=req.seed, status="queued",
            ),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    result.duration_ms = int((time.time() - started) * 1000)
    _write_out(result, req.out)
    manifest_path = _write_manifest(run_dir, req, result)
    result.manifest_path = str(manifest_path)
    return result
