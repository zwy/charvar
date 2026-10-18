"""训练 caption 导出：000_full_front.txt … 与图片同名（不含扩展名）。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from charvar.models import GenerateResult, PromptItem


def caption_filename(item: PromptItem) -> str:
    return f"{item.index:03d}_{item.variant_id}.txt"


def files(result: GenerateResult) -> dict[str, str]:
    return {caption_filename(item): item.caption + "\n" for item in result.items}


def write_dir(result: GenerateResult, dir_path: Path) -> Path:
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    for name, content in files(result).items():
        (dir_path / name).write_text(content, encoding="utf-8")
    return dir_path


def write_zip_bytes(result: GenerateResult) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files(result).items():
            zf.writestr(name, content)
    return buf.getvalue()
