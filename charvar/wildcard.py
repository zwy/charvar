"""通配符：种子稳定的 {a|b|c} 内联展开 + 子种子派生（设计文档 §6.1）。

- 不用全局 random，一切随机都从稳定的子种子派生；
- 子种子 = sha256(f"{seed}:{key}") 前 8 字节，进程间稳定，
  删除/新增变体不影响其他条目的背景/光照选择。
- 只展开含 "|" 的花括号块（兼容 Impact Pack 写法，选项两端 trim），
  纯占位符（{subject_noun} 等）留给模板渲染。
"""
from __future__ import annotations

import hashlib
import re
from random import Random

# {a|b|c}：不含 "{}" 且含至少一个 "|" 的花括号块
_WILDCARD_WITH_ALT_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def seed_for(seed: int, key: str) -> int:
    """稳定子种子：同 (seed, key) 在任何进程得到同一值。"""
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def rng_for(seed: int, key: str) -> Random:
    return Random(seed_for(seed, key))


def parse_options(body: str) -> list[str]:
    """拆 {a|b|c} 选项，两端 trim。"""
    return [part.strip() for part in body.split("|")]


def expand_inline(text: str, rng: Random) -> tuple[str, int]:
    """把文本内联通配符按 rng 展开，返回 (展开后文本, 展开次数)。"""
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        options = parse_options(match.group(1))
        if not options:
            return match.group(0)
        count += 1
        return rng.choice(options)

    return _WILDCARD_WITH_ALT_RE.sub(_sub, text), count
