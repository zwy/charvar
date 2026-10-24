#!/usr/bin/env python3
"""Static checks for the character-forge Skill.

Covers the automatable subset of the review spec's section 10.1:
  - Python syntax and the budget script's own behaviour
  - Markdown internal links resolve to existing files
  - No leftover placeholders in a marked-submit block
  - Marked-submit blocks respect the stated budget
  - Both required base images have a location in the delivery template
  - No mutually conflicting legacy delivery conditions remain
  - Corrected length/cost statements are present, wrong ones are gone

Semantic review (same character across views, evidence wording, dual-mode
separation) is NOT checked here. Those stay manual.

Exit code 0 when every check passes, 1 otherwise.

Usage:
    python3 tests/run_checks.py            # run checks, report only
    python3 tests/run_checks.py --record   # also write the result into manifest.json
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "prompt_budget.py"
MANIFEST = ROOT / "manifest.json"
BUDGET = 2000

failures = []
notes = []


def check(condition, label, detail=""):
    if condition:
        notes.append(f"  PASS  {label}")
    else:
        failures.append(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def run_budget(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=ROOT,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main():
    parser = argparse.ArgumentParser(description="Static checks for the character-forge Skill")
    parser.add_argument("--record", action="store_true", help="Write the run result into manifest.json")
    args = parser.parse_args()

    md_files = sorted(ROOT.rglob("*.md"))
    md_files = [p for p in md_files if ".git" not in p.parts]

    # --- 1. Python syntax -------------------------------------------------
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT), str(Path(__file__).resolve())],
        capture_output=True, text=True, cwd=ROOT,
    )
    check(proc.returncode == 0, "Python syntax compiles", proc.stderr.strip())

    # --- 2. Markdown internal links resolve ------------------------------
    broken = []
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        for target in link_re.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = target.split("#")[0].strip()
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    check(not broken, "Markdown internal links resolve", "; ".join(broken))

    # --- 3/4. Budget script behaviour on fixtures -------------------------
    code, out, err = run_budget(["tests/fixtures/over-budget-submit.md", "--markdown", "--budget", "200", "--strict"])
    check(code == 1, "over-budget submit block fails --strict", f"exit={code} {err.strip()}")

    code, out, err = run_budget(["tests/fixtures/placeholder-submit.md", "--markdown", "--budget", str(BUDGET), "--strict"])
    check(code == 1, "submit block with placeholders fails --strict", f"exit={code} {err.strip()}")

    code, out, err = run_budget(["tests/fixtures/sample-character-asset.md", "--markdown", "--budget", str(BUDGET), "--strict"])
    check(code == 0, "sample asset passes --strict at budget 2000", f"exit={code} {err.strip()}")

    # Template blocks keep their placeholders; that is not a delivery failure.
    code, out, err = run_budget(["templates/character-document.md", "--markdown", "--budget", str(BUDGET), "--strict"])
    check(code == 0, "template placeholders do not count as delivery failure", f"exit={code} {err.strip()}")

    # Component blocks are not checked against the budget by default.
    code, out, err = run_budget(["templates/character-document.md", "--markdown", "--budget", "1", "--strict"])
    check(code == 0, "component/template blocks excluded from budget by default", f"exit={code} {err.strip()}")

    # The submit role is reported and scoped.
    code, out, err = run_budget(["tests/fixtures/sample-character-asset.md", "--markdown", "--budget", str(BUDGET)])
    check("budget_scope" in out and '"submit"' in out, "budget scope is reported per block")

    # --- 5. Both required base images have a location ---------------------
    tmpl = read("templates/character-document.md")
    for needle, label in [
        ("全身正面定妆", "full-body front base has a slot in the delivery template"),
        ("同图半身", "same-image half-body + 3-view board has a slot in the delivery template"),
    ]:
        check(needle in tmpl, label)

    # --- 6. No conflicting legacy delivery conditions ---------------------
    # A legacy phrase is only a violation when stated as an ACTIVE rule.
    # Mentions that mark it as superseded/obsolete are allowed.
    legacy = [
        "至少一个视角变化或跨场景认可",
        "至少一个视角或场景变化",
    ]
    deprecation_markers = ("废止", "不再适用", "旧", "旧的", "不同时保留", "改为", "不适用")
    all_text = "\n".join(read(p.relative_to(ROOT)) for p in md_files)
    active = []
    for needle in legacy:
        for path in md_files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if needle in line and not any(marker in line for marker in deprecation_markers):
                    active.append(f"{path.relative_to(ROOT)}:{number}")
    check(not active, "legacy delivery condition not stated as an active rule", ", ".join(active))

    # --- 7. Corrected length / cost statements ---------------------------
    wrong = [
        "2000字符的中文预算约等于480字符的等义英文",
        "2000 字符的中文预算约等于 480 字符的等义英文",
        "英文同样内容贵四倍",
        "贵四倍",
    ]
    for needle in wrong:
        check(needle not in all_text, f"incorrect conversion/cost claim removed: {needle!r}")

    required = [
        "即梦 2000 字符",
    ]
    for needle in required:
        check(needle in all_text, f"platform default still recorded: {needle!r}")

    # --- 8. Evidence model present ---------------------------------------
    skill = read("SKILL.md")
    for needle in ["execution_status", "user_approval", "assistant_image_review", "batch_validation"]:
        check(needle in all_text, f"evidence field documented: {needle}")

    # --- 9. Two independent modes ----------------------------------------
    for needle in ["模式 A", "模式 B", "素材1", "#图1"]:
        check(needle in skill, f"dual-mode entry documented in SKILL.md: {needle}")

    # --- 10. Files exist and are non-empty --------------------------------
    for rel in [
        "SKILL.md", "README.md",
        "references/workflow.md", "references/input-modes.md",
        "references/quality-checks.md", "references/length-and-language.md",
        "templates/character-document.md", "templates/project-state.md",
        "templates/prompt-record.md", "templates/round-response.md",
        "examples/tiluo-case.md", "scripts/prompt_budget.py",
    ]:
        path = ROOT / rel
        check(path.exists() and path.stat().st_size > 0, f"exists and non-empty: {rel}")

    # --- 11. No replacement characters (encoding damage) ------------------
    damaged = [p.relative_to(ROOT) for p in md_files if "�" in p.read_text(encoding="utf-8")]
    check(not damaged, "no replacement characters in Markdown", ", ".join(str(d) for d in damaged))

    # --- 12. Every text fence declares a role ----------------------------
    fence_re = re.compile(r"^```(?P<info>[^\n]*)\n(?P<body>.*?)^```[^\S\n]*(?:\n|$)", re.M | re.S)
    unlabelled = []
    for path in md_files:
        for match in fence_re.finditer(path.read_text(encoding="utf-8")):
            info = match.group("info").split()
            if info and info[0] == "text":
                if len(info) < 2 or info[1] not in ("submit", "component", "template"):
                    unlabelled.append(f"{path.relative_to(ROOT)}: {match.group('info')!r}")
    check(not unlabelled, "every text fence declares submit/component/template", "; ".join(unlabelled))

    # --- 13. Manifest is consistent with the repo -------------------------
    manifest_path = ROOT / "manifest.json"
    check(manifest_path.exists(), "manifest.json exists")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        listed = set(manifest.get("files", {}))
        actual = {str(p.relative_to(ROOT)) for p in md_files}
        actual |= {str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if ".git" not in p.parts}
        actual.add("manifest.json")
        missing = actual - listed
        extra = listed - actual
        check(not missing, "every repo file is listed in the manifest", ", ".join(sorted(missing)))
        check(not extra, "manifest lists no missing files", ", ".join(sorted(extra)))
        check(manifest.get("entrypoint") in listed, "manifest entrypoint exists")

    # --- 14. Version numbers agree across SKILL.md, README.md, manifest ----
    skill_version = re.search(r"^版本：([\d.]+)", read("SKILL.md"), re.M)
    readme_version = re.search(r"^版本：([\d.]+)", read("README.md"), re.M)
    manifest_version = manifest.get("version") if manifest_path.exists() else None
    check(skill_version and readme_version and skill_version.group(1) == readme_version.group(1) == manifest_version,
          "version numbers agree across SKILL.md, README.md and manifest.json",
          f"skill={skill_version.group(1) if skill_version else None} "
          f"readme={readme_version.group(1) if readme_version else None} manifest={manifest_version}")

    # --- 15. Stated character counts match actual measurement --------------
    # Walks the sample asset: every "- 字符数：N" line must match the
    # measured length of the submit block it describes.
    asset_path = ROOT / "tests/fixtures/sample-character-asset.md"
    asset_text = asset_path.read_text(encoding="utf-8")
    code, out, err = run_budget(["tests/fixtures/sample-character-asset.md", "--markdown", "--budget", str(BUDGET)])
    measured = [item["characters"] for item in json.loads(out)["items"] if item["role"] == "submit"]
    stated = [int(n) for n in re.findall(r"字符数：(\d+)", asset_text)]
    normal = re.search(r"正常版 (\d+)；精简版 (\d+)", asset_text)
    if normal:
        stated += [int(normal.group(1)), int(normal.group(2))]
    check(stated == measured,
          "stated character counts in the sample asset match the measured counts",
          f"stated={stated} measured={measured}")

    print("\n".join(notes))
    if failures:
        print("\n".join(failures))
        print(f"\n{len(failures)} check(s) failed, {len(notes)} passed")
        record(args.record, passed=len(notes), failed=len(failures), result="failed")
        return 1
    print(f"\nAll {len(notes)} checks passed")
    record(args.record, passed=len(notes), failed=0, result="passed")
    return 0


def record(write, passed, failed, result):
    """Write the real run outcome into manifest.json. Never invents a result."""
    if not write or not MANIFEST.exists():
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks = manifest.setdefault("checks", {})
    checks["last_run"] = datetime.date.today().isoformat()
    checks["result"] = result
    checks["passed"] = passed
    checks["failed"] = failed
    checks["note"] = "由 tests/run_checks.py --record 在实际运行时写入。"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded result '{result}' in manifest.json")


if __name__ == "__main__":
    raise SystemExit(main())
