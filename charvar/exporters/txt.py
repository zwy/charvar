"""TXT 导出：每行一条完整提示词，直接贴进 ComfyUI Text Multiline。"""
from __future__ import annotations

from pathlib import Path

from charvar.models import GenerateResult


def render(result: GenerateResult) -> str:
    return "\n".join(item.prompt for item in result.items) + ("\n" if result.items else "")


def write(result: GenerateResult, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(result), encoding="utf-8")
