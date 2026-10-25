#!/usr/bin/env python3
"""Count prompt characters; optionally inspect Markdown fenced blocks.

Two kinds of fenced block are distinguished by their info string:

    ```text              a complete submittable prompt (role: submit)
    ```text component    an internal component, not submittable as-is
    ```text template     a template block whose placeholders are intentional

Only ``submit`` blocks are checked against the budget by default, so that
combination notes and internal components are never mistaken for prompts.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Rough expansion factor when equivalent Chinese is rewritten in English.
# Measured on 5 role-description pairs (observed 3.65-4.96). Planning aid only:
# it is not a billing figure and not a hard limit.
EN_EXPANSION = 4.16
EN_EXPANSION_BASIS = (
    "rough planning estimate from 5 zh/en sample pairs (observed 3.65-4.96); "
    "not a billing figure and not a hard limit"
)

# Info-string roles and whether the block counts against the budget.
# BUDGET_ROLES：计入字符预算的块。
# PLACEHOLDER_ROLES：占位符算失败的块 —— 只限 submit（内部组件与模板的占位符
# 本来就要保留，不能算交付失败）。
BUDGET_ROLES = ("submit",)
PLACEHOLDER_ROLES = ("submit",)

FENCE = re.compile(r"^```(?P<info>[^\n]*)\n(?P<body>.*?)^```[^\S\n]*(?:\n|$)", re.M | re.S)
PLACEHOLDER = re.compile(r"\{\{[^{}\n]+\}\}|【全局固定前缀】|\[GLOBAL_PREFIX\]")


def parse_role(info):
    parts = info.split()
    if not parts or parts[0] != "text":
        return None
    role = parts[1] if len(parts) > 1 else "submit"
    return role if role in ("submit", "component", "template") else "submit"


def inspect_text(text, budget=None, language=None, role="submit"):
    placeholders = PLACEHOLDER.findall(text)
    counts_against_budget = role in BUDGET_ROLES
    report = {
        "role": role,
        "characters": len(text),
        "counts_against_budget": counts_against_budget,
        "budget": budget if counts_against_budget else None,
        "within_budget": (len(text) <= budget) if (counts_against_budget and budget is not None) else None,
        "placeholder_candidates": placeholders,
    }
    if language == "en":
        report["language"] = "en"
        report["equivalent_chinese_characters"] = round(len(text) / EN_EXPANSION)
        report["estimate_basis"] = EN_EXPANSION_BASIS
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="UTF-8 prompt character checker, not a token or visual-quality checker")
    parser.add_argument("file", help="UTF-8 input file, or - for stdin")
    parser.add_argument("--budget", type=int, help="User-defined total character budget")
    parser.add_argument("--markdown", action="store_true", help="Inspect fenced text blocks instead of the whole file")
    parser.add_argument("--strict", action="store_true", help="Return 1 for over-budget text or placeholder candidates")
    parser.add_argument("--language", choices=["zh", "en"], help="Prompt language; en adds an equivalent-Chinese length estimate")
    parser.add_argument("--all-blocks", action="store_true", help="Count component and template blocks against the budget too")
    parser.add_argument("--allow-placeholders", action="store_true", help="Do not treat placeholders as a strict-mode failure")
    args = parser.parse_args(argv)
    if args.budget is not None and args.budget < 1:
        parser.error("--budget must be positive")
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        parser.exit(2, f"Cannot read UTF-8 input: {exc}\n")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if args.markdown:
        blocks = []
        for match in FENCE.finditer(raw):
            role = parse_role(match.group("info"))
            if role is None:
                continue
            body = match.group("body")
            blocks.append((role, body[:-1] if body.endswith("\n") else body))
        if not blocks:
            parser.exit(2, "No fenced text blocks found\n")
    else:
        blocks = [("submit", raw)]
    reports = []
    for index, (role, body) in enumerate(blocks, start=1):
        report = dict(index=index, **inspect_text(body, args.budget, args.language, role))
        if args.all_blocks and role != "submit":
            report["counts_against_budget"] = True
            report["budget"] = args.budget
            report["within_budget"] = len(body) <= args.budget if args.budget is not None else None
        reports.append(report)
    payload = {
        "counting": "Python len; line endings normalized to LF; not model tokens",
        "budget_scope": "only blocks marked submit are checked unless --all-blocks is given",
    }
    if args.language == "en":
        payload["estimate_basis"] = EN_EXPANSION_BASIS
    payload["items"] = reports
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    failed = False
    for item in reports:
        if item["within_budget"] is False:
            failed = True
        if item["placeholder_candidates"] and not args.allow_placeholders and item["role"] in PLACEHOLDER_ROLES:
            failed = True
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
