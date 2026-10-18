"""JSONL 导出：每行一条 PromptItem 完整结构。"""
from __future__ import annotations

import json
from pathlib import Path

from charvar.models import GenerateResult, PromptItem


def render_item(item: PromptItem) -> str:
    return json.dumps(item.model_dump(), ensure_ascii=False)


def render(result: GenerateResult) -> str:
    return "\n".join(render_item(item) for item in result.items) + ("\n" if result.items else "")


def write(result: GenerateResult, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(result), encoding="utf-8")
