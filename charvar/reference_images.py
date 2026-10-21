"""参考图统一预处理：缩放 + 格式转换，ComfyUI 与 OpenRouter 共用同一份。

为什么要单独一层：主体档案里的参考图是全尺寸 PNG（实测 2.4–3.5MB /
2048×1152 起），直接喂给 ComfyUI 是原图直传，喂给 OpenRouter 则要把
几 MB 转成 data URL —— 请求体能涨到 17MB，单张出图多花 30s。

统一规格（长边 1536 / JPEG q88）实测：3460KB → 192KB，请求体 517KB → 283KB，
出图耗时基本不变（18.7s vs 19.6s），脸部与服装纹理保得住。

透明通道：主体档案的参考图普遍带真 alpha（实测多数 RGBA 且真透明），
直接 ``convert("RGB")`` 会把透明区域变黑，污染参考图内容。所以透明图
先白底合成再转 JPEG。
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 统一压缩规格
MAX_SIDE = 1536
JPEG_QUALITY = 88

_CACHE_DIRNAME = "_ref_cache"


def _cache_dir(subject_dir: Path) -> Path:
    return subject_dir / _CACHE_DIRNAME


def cache_path(subject_dir: Path, kind: str) -> Path:
    """预处理后的参考图落盘位置：``{subject_dir}/_ref_cache/{kind}.jpg``。"""
    return _cache_dir(subject_dir) / f"{kind}.jpg"


def flatten_on_white(image: Image.Image) -> Image.Image:
    """透明区域白底合成（透明不是黑，参考图不能被污染）。"""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] >= 250:
        # 整张不透明，直接用 RGB 更快
        return rgba.convert("RGB")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def prepare_reference(source: Path, destination: Path) -> Path:
    """把参考图压成统一规格并落盘，返回目标路径。

    - 长边超过 ``MAX_SIDE`` 才缩放（原图小的保持原尺寸，避免放大劣化）
    - 有透明通道 → 白底合成后转 JPEG
    - 无透明通道 → 直接转 JPEG
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.load()
        flat = flatten_on_white(image)
        if max(flat.size) > MAX_SIDE:
            flat.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        flat.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    destination.write_bytes(buffer.getvalue())
    return destination


def prepared_reference(source: Path, subject_dir: Path, kind: str) -> Path:
    """取（或生成）主体档案某张图的预处理版本。

    缓存按源文件 mtime + size 判定失效：参考图更新后自动重压。
    """
    destination = cache_path(subject_dir, kind)
    try:
        stat = source.stat()
    except OSError:
        return source
    stamp = f"{stat.st_mtime_ns}-{stat.st_size}"
    marker = destination.with_suffix(".stamp")
    try:
        if destination.exists() and marker.read_text(encoding="utf-8").strip() == stamp:
            return destination
    except OSError:
        pass
    try:
        prepare_reference(source, destination)
    except OSError as exc:
        logger.warning("reference.prepare_failed | src=%s | err=%s", source, exc)
        return source
    try:
        marker.write_text(stamp, encoding="utf-8")
    except OSError:
        pass
    return destination
