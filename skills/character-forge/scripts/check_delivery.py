#!/usr/bin/env python3
"""交付文档体检：check the delivery Markdown, not the image quality.

这个脚本只检查**交付文档的结构与自洽性**，不判定角色身份是否正确、提示词
是否会被模型执行、槽位图好不好看。它能查的部分全部机器化，查不了的留在
references/quality-checks.md 的清单里。

检查项：

1. **篇幅** —— 整份文档 ≤ 上限（默认 10000 字符）。交付文档只写当前态，
   超长通常意味着把版本历史、证据台账写进来了。
2. **章节齐全** —— 识别锚点表 / 固定前缀 / 基准图 / 已知限制 四节必须存在。
   「识别锚点表」和「固定前缀」是 charvar `subject derive` 抽取锁定项时
   优先取的两节，缺了会降低抽取命中率。
3. **身份只写一次** —— 固定前缀正文不得在别的章节里再出现一遍。
4. **无版本历史** —— 不写版本号、更新日期、版本记录表、上一版对比。
5. **无外部依赖** —— 不写 ../ 路径、开发目录、绝对路径、「见上一轮」这类
   外部指代。交付文档必须自包含。
6. **围栏角色** —— 固定前缀是 `text component`（不单独提交），场景段是
   `text`（提交内容）。
7. **字符数自洽** —— 文档里声明的场景段字数、拼接后整段字数与实际一致。

用法：
    python3 scripts/check_delivery.py <交付文档.md> [--max-chars 10000] [--strict]
    python3 scripts/check_delivery.py <交付文档.md> --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_CHARS = 10_000

# 必须存在的二级章节（关键词 → 章节名里必须包含）
REQUIRED_SECTIONS = {
    "识别锚点表": ("识别锚点表",),
    "固定前缀": ("固定前缀",),
    "基准图": ("基准图",),
    "已知限制": ("已知限制",),
}

# 版本历史痕迹：行首元信息
_VERSION_LINE = re.compile(
    r"(?m)^[ \t>*\-]*(版本|版本号|交付版|更新日期|更新|日期|修订记录|版本记录|变更记录)"
    r"[ \t]*[：:].*$"
)
# 版本号形态：行首或竖线后的 V1 / V1.2 / v2.0
_VERSION_TAG = re.compile(r"(?m)^[ \t]*(?:[｜|]\s*)?[Vv]\d+(?:\.\d+)*\b")
# 「交付版 V1.3」这类不带冒号的写法
_VERSION_DELIVERED = re.compile(r"交付版[ \t]*[Vv]?\d")
# 版本叙事的关键词
_VERSION_WORDS = re.compile(
    r"(上一版|上一版本|前一版|早期版本|最初版本|历史版本|旧版本|本次修订|此次修订|"
    r"改前|改后|变更如下|改动如下|迭代记录)"
)
# 版本记录类章节标题
_VERSION_SECTION = re.compile(r"(?m)^#{1,6}[ \t]*(?:[0-9一二三四五六七八九十]+[.、]?[ \t]*)?"
                              r"(版本记录|修订记录|变更记录|版本历史|历史版本|迭代记录)")
# 日期（头部元信息里的日期也是版本历史的一种）
_DATE = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{4}[-/年]\d{1,2}月?)")

# 外部依赖：相对路径 / 开发目录 / 绝对路径
_EXTERNAL_PATH = re.compile(
    r"(\.\./|\.\.\\|提示词记录/|_角色开发/|项目状态\.md|/Users/|[A-Za-z]:\\\\)"
)
# 外部指代（自包含检查）
_EXTERNAL_REF = re.compile(
    r"(见上一轮|同上[一轮]?|同前文|见对话|见前文|见上文|见另[一1]个文件|"
    r"见 [A-Za-z_]+\.md|详见 [A-Za-z_]+\.md|在 prompt_|提示词在 )"
)

# 代码块围栏
_FENCE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<body>.*?)^```[^\S\n]*(?:\n|$)", re.M | re.S)
# 二级标题
_H2 = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.M)
# 声明的字符数
_DECLARED_SCENE = re.compile(r"场景段[ \t]*(\d+)[ \t]*字")
_DECLARED_TOTAL = re.compile(r"整段[ \t]*(\d+)[ \t]*字")
# 未填的占位符
_PLACEHOLDER = re.compile(r"\{\{[^{}\n]+\}\}")


def _role(info: str) -> str | None:
    """围栏角色：text → submit；text component/template 各自成类。"""
    parts = info.split()
    if not parts or parts[0] != "text":
        return None
    if len(parts) == 1:
        return "submit"
    return parts[1] if parts[1] in ("submit", "component", "template") else "submit"


def _sections(raw: str) -> list[tuple[str, int, int]]:
    """返回 [(二级标题, 起始偏移, 结束偏移)]。"""
    marks = list(_H2.finditer(raw))
    out: list[tuple[str, int, int]] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        out.append((m.group("title"), m.start(), end))
    return out


def _blocks(raw: str) -> list[tuple[str, str, int]]:
    """返回 [(role, body, 起始偏移)]。"""
    out = []
    for m in _FENCE.finditer(raw):
        role = _role(m.group("info"))
        if role is None:
            continue
        body = m.group("body")
        out.append((role, body[:-1] if body.endswith("\n") else body, m.start()))
    return out


def _sentences(text: str) -> list[str]:
    """按句号/换行切句，过短的丢掉（太短容易撞句）。"""
    parts = re.split(r"[。！？\n]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 12]


def _section_of(offset: int, secs: list[tuple[str, int, int]]) -> str:
    for title, start, end in secs:
        if start <= offset < end:
            return title
    return ""


def check(raw: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """跑全部检查，返回 [{name, status, detail}]。status ∈ pass / fail / warn。"""
    results: list[dict] = []
    secs = _sections(raw)
    blocks = _blocks(raw)

    # 1. 篇幅
    n = len(raw)
    results.append({
        "name": "篇幅",
        "status": "pass" if n <= max_chars else "fail",
        "detail": f"{n} 字符 / 上限 {max_chars}"
        + ("" if n <= max_chars else "（超限：多半把版本历史或证据台账写进交付了）"),
    })

    # 2. 章节齐全
    missing = []
    for label, keys in REQUIRED_SECTIONS.items():
        if not any(any(k in title for k in keys) for title, _, _ in secs):
            missing.append(label)
    results.append({
        "name": "章节齐全",
        "status": "pass" if not missing else "fail",
        "detail": "四节齐全" if not missing else f"缺少：{'、'.join(missing)}"
                  "（「识别锚点表」「固定前缀」是 charvar 抽取锁定项时优先取的两节）",
    })

    # 3. 身份只写一次
    prefix_block = None
    for role, body, offset in blocks:
        if role in ("component", "submit", "template") and "固定前缀" in _section_of(offset, secs):
            prefix_block = body
            break
    dup_lines: list[str] = []
    dup_blocks: list[str] = []
    if prefix_block:
        rest = raw.replace(prefix_block, "", 1)
        for line in (ln.strip() for ln in prefix_block.splitlines()):
            if len(line) < 20:
                continue
            if line in rest:
                dup_lines.append(line[:40])
        # 句级复现率：场景段整段复制身份（哪怕改了几个字）也要抓出来
        prefix_sentences = _sentences(prefix_block)
        if prefix_sentences:
            for role, body, offset in blocks:
                if body is prefix_block:
                    continue
                title = _section_of(offset, secs)
                if "固定前缀" in title:
                    continue
                hits = sum(1 for s in prefix_sentences if s in body)
                if hits / len(prefix_sentences) >= 0.5:
                    dup_blocks.append(f"{title or '未命名章节'}（{hits}/{len(prefix_sentences)} 句复现）")
    problems = []
    if dup_lines:
        problems.append(f"固定前缀有 {len(dup_lines)} 行在别处逐字重复")
    if dup_blocks:
        problems.append("身份被别处复述：" + "；".join(dup_blocks))
    results.append({
        "name": "身份只写一次",
        "status": "pass" if prefix_block and not problems else ("fail" if prefix_block else "warn"),
        "detail": ("固定前缀是身份的唯一出处" if prefix_block and not problems
                   else ("；".join(problems) if prefix_block else "找不到「固定前缀」章节下的代码块")),
    })

    # 4. 无版本历史
    hits = []
    for pat, label in ((_VERSION_LINE, "版本/日期行"), (_VERSION_TAG, "版本号标签"),
                       (_VERSION_DELIVERED, "交付版号"), (_VERSION_WORDS, "版本叙事词"),
                       (_VERSION_SECTION, "版本记录章节")):
        for m in pat.finditer(raw):
            hits.append(f"{label}：{m.group(0).strip()[:40]}")
    # 头部三行里的日期也算版本历史（交付文档不记日期）
    head = "\n".join(raw.splitlines()[:3])
    for m in _DATE.finditer(head):
        hits.append(f"头部日期：{m.group(0)}")
    results.append({
        "name": "无版本历史",
        "status": "pass" if not hits else "fail",
        "detail": "没有版本历史残留" if not hits else "；".join(hits[:5]),
    })

    # 5. 无外部依赖
    ext = []
    for pat, label in ((_EXTERNAL_PATH, "外部路径"), (_EXTERNAL_REF, "外部指代")):
        for m in pat.finditer(raw):
            ext.append(f"{label}：{m.group(0)[:40]}")
    results.append({
        "name": "无外部依赖",
        "status": "pass" if not ext else "fail",
        "detail": "文档自包含" if not ext else "；".join(ext[:5]),
    })

    # 6. 围栏角色
    role_issues = []
    role_warns = []
    prefix_role = None
    for role, body, offset in blocks:
        title = _section_of(offset, secs)
        if "固定前缀" in title:
            prefix_role = role
            if role != "component":
                role_issues.append(f"固定前缀围栏角色是 {role}，应为 component")
        elif "基准图" in title and role != "submit":
            if role == "template" and _PLACEHOLDER.search(body):
                role_warns.append("场景段仍是 text template（占位符未填）")
            else:
                role_issues.append(f"场景段围栏角色是 {role}，应为 text（提交内容）")
    if prefix_role is None:
        role_issues.append("「固定前缀」章节下没有 text 代码块")
    results.append({
        "name": "围栏角色",
        "status": "fail" if role_issues else ("warn" if role_warns else "pass"),
        "detail": ("固定前缀=component、场景段=submit" if not role_issues and not role_warns
                   else "；".join(role_issues or role_warns)),
    })

    # 7. 字符数自洽
    scene_declared = _DECLARED_SCENE.findall(raw)
    total_declared = _DECLARED_TOTAL.findall(raw)
    actual_scene = []
    for role, body, offset in blocks:
        if "基准图" in _section_of(offset, secs) and role == "submit":
            actual_scene.append(len(body))
    issues = []
    warns = []
    if scene_declared and len(scene_declared) == len(actual_scene):
        for decl, act in zip(scene_declared, actual_scene):
            if int(decl) != act:
                issues.append(f"场景段声明 {decl} 字，实际 {act} 字")
    elif scene_declared:
        issues.append(f"声明了 {len(scene_declared)} 处场景段字数，找到 {len(actual_scene)} 个场景段代码块")
    elif actual_scene:
        warns.append("场景段没有声明字数（应写「场景段 N 字」）")
    if total_declared and prefix_block:
        prefix_len = len(prefix_block)
        for decl, act in zip(total_declared, actual_scene):
            joined = prefix_len + 1 + act  # 前缀 + 一个换行 + 场景段
            if abs(int(decl) - joined) > 3:
                issues.append(f"整段声明 {decl} 字，实际拼接约 {joined} 字（前缀 {prefix_len} + 换行 + 场景段 {act}）")
    elif actual_scene and not total_declared:
        warns.append("没有声明拼接后整段字数（应写「整段 M 字」）")
    results.append({
        "name": "字符数自洽",
        "status": "fail" if issues else ("warn" if warns else "pass"),
        "detail": "；".join(issues or warns) or "声明字数与实际一致",
    })

    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="交付文档结构体检（不判定身份正确性或出图质量）")
    ap.add_argument("file", help="UTF-8 交付文档路径，或 - 读 stdin")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS, help=f"篇幅上限（默认 {MAX_CHARS}）")
    ap.add_argument("--strict", action="store_true", help="有 fail 项时返回 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    results = check(raw, args.max_chars)
    failed = any(r["status"] == "fail" for r in results)
    payload = {
        "file": args.file,
        "max_chars": args.max_chars,
        "passed": not failed,
        "items": results,
        "note": "只查结构与自洽性；身份正确性与出图质量需人工核",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = {"pass": "OK  ", "fail": "FAIL", "warn": "WARN"}[r["status"]]
            print(f"[{mark}] {r['name']}：{r['detail']}")
        print("—" * 40)
        print("通过" if not failed else "未通过")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
