"""Run 图片资产：从 ComfyUI outputs 同步、生成本地缩略图和 contact sheet。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from charvar import paths, storage
from charvar.models import GenerateResult

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_THUMB_MAX = 512
_SHEET_CELL_WIDTH = 320
_SHEET_GAP = 10
_SHEET_LABEL_HEIGHT = 22


def safe_run_id(run_id: str) -> str:
    if not _SAFE_RUN_ID.fullmatch(str(run_id)):
        raise ValueError("非法 run_id")
    return str(run_id)


def safe_filename(name: str) -> str:
    n = str(name)
    if not _SAFE_FILENAME.fullmatch(n):
        raise ValueError("非法文件名")
    return n


def run_assets_dir(run_id: str) -> Path:
    return paths.data_dir() / "generated" / safe_run_id(run_id)


def original_dir(run_id: str) -> Path:
    return run_assets_dir(run_id) / "original"


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def list_originals(run_id: str) -> list[Path]:
    """run 的原图列表（按文件名排序 = 条目顺序）。

    给「参考图从最近生成加入」用：按 index 取第几张。不认 run_id 以外的路径。
    """
    directory = original_dir(safe_run_id(run_id))
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )


def thumb_dir(run_id: str) -> Path:
    return run_assets_dir(run_id) / "thumbs"


def sheet_dir(run_id: str) -> Path:
    return run_assets_dir(run_id) / "sheets"


def run_manifest_path(run_id: str) -> Path:
    return run_assets_dir(run_id) / "image_manifest.json"


def thumb_path(run_id: str, item_index: int) -> Path:
    return thumb_dir(run_id) / f"{item_index:06d}.webp"

def sheet_path(run_id: str, kind: str) -> Path:
    if kind not in {"contact", "face"}:
        raise ValueError("非法 sheet 类型")
    return sheet_dir(run_id) / f"{kind}_sheet.png"


def resolve_run_asset(run_id: str, scene: str, filename: str) -> Path:
    if scene not in {"original", "thumbs", "sheets", "videos", "labeled"}:
        raise ValueError("非法资产目录")
    path = run_assets_dir(run_id) / scene / safe_filename(filename)
    if not path.exists():
        raise FileNotFoundError(filename)
    return path


def _original_path(run_id: str, item_index: int, output_file: dict[str, str]) -> Path:
    suffix = Path(str(output_file.get("filename", ""))).suffix or ".png"
    return original_dir(run_id) / f"{item_index:06d}{suffix}"


def _download_output(client, base: str, file: dict[str, str], destination: Path) -> Path:
    suffix = Path(file.get("filename", "")).suffix or ".png"
    target = destination.with_suffix(suffix)
    params = {
        "filename": file.get("filename", ""),
        "subfolder": file.get("subfolder", ""),
        "type": file.get("type", "output"),
    }
    resp = client.get(f"{base.rstrip('/')}/view", params=params, timeout=15)
    resp.raise_for_status()
    if not resp.content:
        raise RuntimeError(f"ComfyUI 输出为空：{file}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resp.content)
    return target


def ensure_thumb(run_id: str, item_index: int) -> Path | None:
    """按 item_index 补齐 WebP 缩略图，返回可用的本地缩略图路径。"""
    for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
        source = original_dir(run_id) / f"{item_index:06d}{suffix}"
        if not source.exists():
            continue
        destination = thumb_path(run_id, item_index)
        if not destination.exists():
            make_thumb(source, destination)
        return destination
    return None


def make_thumb(source: Path, destination: Path, max_side: int = _THUMB_MAX) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        image.convert("RGB").save(destination, format="WEBP", quality=84, optimize=True)
    return destination


def save_run_image(run_id: str, item_index: int, data: bytes, suffix: str) -> Path:
    """把内存里的图片 bytes 落成 run 资产（OpenRouter 通路用）。

    产物落 ``original/{item_index:06d}{suffix}`` 并补缩略图，与 ComfyUI
    同步下来的图同目录同命名 → 图片墙 / contact sheet / 合成视频全部复用。
    同一条目换格式时清掉旧后缀文件（否则 image_entry 按固定后缀序取图会取到旧图）。
    """
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    target = original_dir(run_id) / f"{item_index:06d}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    for other in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = original_dir(run_id) / f"{item_index:06d}{other}"
        if other != suffix and candidate.exists():
            candidate.unlink()
    make_thumb(target, thumb_path(run_id, item_index))
    return target


def _draw_label(image: Image.Image, label: str) -> Image.Image:
    label_box = Image.new("RGB", (image.width, _SHEET_LABEL_HEIGHT), (24, 32, 42))
    draw = ImageDraw.Draw(label_box)
    draw.text((8, 3), label, font=ImageFont.load_default(size=17), fill=(240, 244, 249))
    cell = Image.new("RGB", (image.width, image.height + _SHEET_LABEL_HEIGHT), (246, 247, 249))
    cell.paste(image, (0, 0))
    cell.paste(label_box, (0, image.height))
    return cell


def make_contact_sheet(
    run_id: str,
    result: GenerateResult,
    records: list[dict[str, Any]],
    *,
    mode: str = "contact",
) -> Path:
    by_index = {int(record.get("item_index", -1)): record for record in records}
    images: list[tuple[Path, str]] = []
    for item in result.items:
        record = by_index.get(item.index)
        files = (record or {}).get("output_files") or []
        if files:
            output_file = files[0]
            path = original_dir(run_id) / f"{item.index:06d}{Path(str(output_file.get('filename'))).suffix or '.png'}"
        else:
            for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
                path = original_dir(run_id) / f"{item.index:06d}{suffix}"
                if path.exists():
                    break
            else:
                path = None
        if path and path.exists():
            images.append((path, f"#{item.index:03d} {item.variant_id}"))
    if not images:
        raise FileNotFoundError("没有可用图片")
    first_path = images[0][0]
    with Image.open(first_path) as first_image:
        sample = first_image.convert("RGB")
        aspect = sample.height / sample.width
    cell_width = 320
    cell_height = int(cell_width * aspect) + _SHEET_LABEL_HEIGHT
    columns = 6
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (
        columns * cell_width + (columns + 1) * _SHEET_GAP,
        rows * cell_height + (rows + 1) * _SHEET_GAP,
    ), (246, 247, 249))
    for index, (path, label) in enumerate(images):
        with Image.open(path) as src:
            image = src.convert("RGB")
        image.thumbnail((cell_width, cell_height - _SHEET_LABEL_HEIGHT), Image.Resampling.LANCZOS)
        sheet.paste(_draw_label(image, label), (_SHEET_GAP + (index % columns) * (cell_width + _SHEET_GAP), _SHEET_GAP + (index // columns) * (cell_height + _SHEET_GAP)))
    out = sheet_path(run_id, mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, format="PNG", optimize=True)
    return out


def sync_run_outputs(
    conn: sqlite3.Connection,
    result: GenerateResult,
    base: str,
    records: list[dict[str, Any]] | None = None,
    *,
    build_sheets: bool = True,
) -> dict[str, Any]:
    """把 ComfyUI outputs 固化到本地，方便后续展示和 contact sheet。"""
    from charvar.exporters import comfyui as comfyui_exporter

    if records is None:
        records = storage.list_comfy_submissions(conn, result.run_id)
    downloaded = 0
    for record in records:
        prompt_id = str(record.get("prompt_id") or "")
        files = record.get("output_files") or []
        item_index = int(record.get("item_index", -1))
        if not files and prompt_id:
            with comfyui_exporter._httpx_client(timeout=15) as client:
                entry = history_entry(client, base, prompt_id, 15)
            if entry:
                files = comfyui_exporter._output_filenames(entry, None)
                if files:
                    record["output_files"] = files
                    storage.save_comfy_submission(
                        conn,
                        result.run_id,
                        item_index,
                        str(record["variant_id"]),
                        prompt_id,
                        str(record.get("filename_prefix") or ""),
                        files,
                    )
                    conn.commit()  # 补拉结果当场生效（close() 不落 commit 会回滚）
        files = record.get("output_files") or []
        if files and prompt_id:
            output_file = files[0]
            destination = _original_path(result.run_id, item_index, output_file)
            # 重新生成场景：记录是最近一次提交（submitted_at 比已落盘文件新）→ 覆盖下载
            # 刷新；同一条正常首次同步/重复同步（submitted_at ≤ 文件 mtime）仍走缓存不重下。
            submitted = _utc_epoch(record.get("submitted_at"))
            stale = (
                destination.exists()
                and submitted is not None
                and submitted > destination.stat().st_mtime
            )
            downloaded_new_file = False
            if not destination.exists() or stale:
                with comfyui_exporter._httpx_client(timeout=15) as client:
                    destination = _download_output(client, base, output_file, destination)
                downloaded += 1
                downloaded_new_file = True
            thumbnail = thumb_dir(result.run_id) / f"{item_index:06d}.webp"
            if not thumbnail.exists() or downloaded_new_file:
                make_thumb(destination, thumbnail)
                if not downloaded_new_file:
                    downloaded += 1
    if build_sheets:
        make_contact_sheet(result.run_id, result, records)
    return {
        "completed": sum(1 for record in records if record.get("output_files")),
        "downloaded": downloaded,
    }


def history_entry(client, base: str, prompt_id: str, timeout: float = 15.0) -> dict[str, Any] | None:
    response = client.get(f"{base.rstrip('/')}/history/{prompt_id}", timeout=timeout)
    response.raise_for_status()
    return (response.json() or {}).get(prompt_id)


def _utc_epoch(value: Any) -> float | None:
    """解析 storage.utcnow() 格式（"%Y-%m-%d %H:%M:%S"，UTC）→ epoch 秒；失败返回 None。"""
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return parsed.timestamp()


def image_entry(
    run_id: str,
    item_index: int,
    variant_id: str,
    records: list[dict[str, Any]],
    fallback_text: str | None = None,
) -> dict[str, Any]:
    record = next(
        (record for record in records if int(record.get("item_index", -1)) == item_index),
        None,
    )
    files = record.get("output_files") or [] if record else []
    original = None
    for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
        candidate = original_dir(run_id) / f"{item_index:06d}{suffix}"
        if candidate.exists():
            original = candidate
            break
    # 缓存穿透：URL 挂上 prompt_id——重新生成换记录后同路径参数变化，浏览器会重新拉图
    version = str((record or {}).get("prompt_id") or "")
    suffix_qs = f"?v={version}" if version else ""
    labeled = None
    labeled_candidate = run_assets_dir(run_id) / "labeled" / f"{item_index:06d}.png"
    if labeled_candidate.exists():
        labeled = labeled_candidate
    return {
        "item_index": item_index,
        "variant_id": variant_id,
        "prompt_id": record.get("prompt_id") if record else None,
        "filename_prefix": record.get("filename_prefix") if record else None,
        "status": "done" if (files or original) else "queued",
        "files": files,
        "primary_url": f"/runs/{safe_run_id(run_id)}/comfy-image/{item_index}{suffix_qs}" if (files or original) else None,
        "thumb_url": f"/runs/{safe_run_id(run_id)}/assets/thumbs/{item_index:06d}.webp{suffix_qs}" if (thumb_dir(run_id) / f"{item_index:06d}.webp").exists() else None,
        "original_url": f"/runs/{safe_run_id(run_id)}/assets/original/{original.name}{suffix_qs}" if original else None,
        "labeled_url": f"/runs/{safe_run_id(run_id)}/assets/labeled/{labeled.name}{suffix_qs}" if labeled else None,
        "text": fallback_text,
        "record": record,
    }
