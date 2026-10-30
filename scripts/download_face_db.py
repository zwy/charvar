"""下载 AIモデル顔データベース（https://ai-portrait-gallery.pages.dev/）到本地人脸库。

数据形态（与姿势库同套路的单页站点，但目录在独立 JS 里）：
- `data/images.js`：`window.GALLERY_IMAGES = [...]`，1499 条。每条含
  id / section（face=正面向き「フェイス」・styling=服装发型雰囲気「スタイリング」）/
  hair（发型 4 档：ショート/ミディアム/セミロング/ロング）/ style（日文发型细注,
  多数空）/ w・h・bytes（原寸 PNG 尺寸与体积）/ full・thumb（站内相对路径）。
- 图片：AI 生成の架空人物（実在しない）。

落盘布局（本地使用 —— 站方请求「画像データそのものの再配布・転載はご遠慮」，
data/faces/ 不进 git；详见 README）：

    data/faces/<source>/
        catalog.json          # GALLERY_IMAGES 数组逐字保留（真源）
        img/full/{id}.png     # 原寸（face 1122×1402 / styling 1597×2000，6 张横版 2000×1120）
        img/thumb/{id}.webp   # 400×500 缩略图
        README.md             # 来源与许可说明

幂等：已存在且魔数正确的文件跳过；失败（魔数不符/网络错）重试 3 次，重跑续传。
代理红线：与 pose 脚本同款 trust_env=False 直连（pages.dev 可直连，且更快）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

# source_id → 站点（新库 = 加一行 + 确认 data/images.js 仍是同一模板）
SITES: dict[str, str] = {
    "face-db-01": "https://ai-portrait-gallery.pages.dev/",
}

ROOT = Path(__file__).resolve().parent.parent

UA = {"user-agent": "charvar-face-import/1.0 (personal reference library)"}
RETRIES = 3
WORKERS = 8

# webp：RIFF + 4 字节长度 + WEBP；png 用常规魔数。
MAGICS = {
    ".webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    ".png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
}

_print_lock = threading.Lock()


def _say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def target_dir(source: str) -> Path:
    return ROOT / "data" / "faces" / source


def fetch_catalog(client: httpx.Client, site: str) -> list[dict]:
    """拉 data/images.js，解出 `window.GALLERY_IMAGES = [...]` 数组。"""
    resp = client.get(site + "data/images.js", timeout=90, headers=UA)
    resp.raise_for_status()
    raw = resp.text
    marker = "window.GALLERY_IMAGES"
    pos = raw.index(marker) + len(marker)
    while raw[pos] in " \t\r\n":
        pos += 1
    if raw[pos] == "=":
        pos += 1
        while raw[pos] in " \t\r\n":
            pos += 1
    if raw[pos] != "[":
        raise RuntimeError("images.js 顶部不是预期的数组字面量")
    depth = 0
    in_str = None
    escaped = False
    i = pos
    while i < len(raw):
        ch = raw[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    if depth != 0:
        raise RuntimeError("GALLERY_IMAGES … 解析失败（括号不配平）")
    data = json.loads(raw[pos:i])
    if not isinstance(data, list) or not data:
        raise RuntimeError("目录数据为空或结构不对")
    return data


def write_readme(source: str, site: str, target: Path, count: int) -> None:
    (target / "README.md").write_text(
        f"""# 人脸库 {source}

来源：{site}（AIモデル顔データベース），抓取时共 {count} 条。
作者/运营 X：https://x.com/agi_aibusi（あぎ）

数据形态：
- section=face（フェイス）：正面向き参考图，用于给 AI 图片/视频里的虚拟角色统一脸型。
- section=styling（スタイリング）：同批虚构人物的全身 styling 参考图（脸+发型+服装氛围）。
- hair：发型 4 档（ショート/ミディアム/セミロング/ロング）；style：日文发型细注（多数空）。

目录结构：
- catalog.json：站点 `window.GALLERY_IMAGES` 数组逐字保留（真源）。
- img/full/：原寸 PNG（face 1122×1402；styling 1597×2000，横版 6 张 2000×1120）。
- img/thumb/：400×500 webp 缩略图。

重抓/补抓：`python3 scripts/download_face_db.py`（幂等，魔数正确即跳过，可直接续传）。
入库：`python3 -m charvar.cli face import data/faces/{source}`。

许可（站点「利用ルール」原文要点）：
- CC0 1.0（https://creativecommons.org/publicdomain/zero/1.0/deed.ja）：
  自由利用，商用利用・編集・参考利用OK；クレジット表記不要；無保証。
- AI 生成の架空の人物で、実在しません。
- 実在人物のなりすましや公序良俗に反する用途は不可。
- 画像データそのものの再配布・転載はご遠慮ください（站方请求「お願い」，
  非禁令）。本仓库策略据此把本目录留在本地（git 忽略），不随仓库公开 ——
  是否随仓库分发由仓库持有人拍板；详见仓库 README。
""",
        encoding="utf-8",
    )


def _valid(path: Path, magic_fn) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        return magic_fn(path.read_bytes()[:512])
    except OSError:
        return False


def download_file(client: httpx.Client, site: str, rel: str, target: Path) -> str:
    """下载单个站内相对路径文件到本地同布局；返回 'ok' / 'skip' / 'fail'。"""
    url = site + rel
    dest = target / rel
    magic_fn = MAGICS.get(dest.suffix.lower())
    if magic_fn is None:
        raise RuntimeError(f"未知扩展名：{rel}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _valid(dest, magic_fn):
        return "skip"
    for attempt in range(RETRIES):
        try:
            resp = client.get(url, timeout=300, headers=UA)
            resp.raise_for_status()
            body = resp.content
            if not magic_fn(body[:512]):
                raise ValueError(f"魔数不符（预期图，得到其它内容）：{url}")
            dest.write_bytes(body)
            return "ok"
        except Exception as exc:  # noqa: BLE001 网络与校验错误统一重试
            if attempt == RETRIES - 1:
                _say(f"FAIL {rel}: {exc}")
                return "fail"
            time.sleep(1.5 * (attempt + 1))
    return "fail"


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 AIモデル顔データベース到 data/faces/<source>")
    parser.add_argument("--source", default="face-db-01", help=f"库 id（{'/'.join(SITES)}）")
    parser.add_argument("--site", default="", help="站点 URL 覆盖（默认按 source 查表）")
    args = parser.parse_args()
    source = str(args.source).strip()
    if source not in SITES:
        sys.exit(f"未知 source：{source}（可选 {'/'.join(SITES)}；新站先在 SITES 表登记）")
    site = str(args.site).strip() or SITES[source]
    if not site.endswith("/"):
        site += "/"
    target = target_dir(source)
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, follow_redirects=True, headers=UA) as client:
        catalog = fetch_catalog(client, site)
        _say(f"catalog: {len(catalog)} items from {site} -> {target}")

        jobs: list[str] = []
        for d in catalog:
            for key in ("thumb", "full"):
                rel = str(d.get(key) or "").strip()
                if rel and not rel.startswith(("http://", "https://")):
                    jobs.append(rel)
        missing_paths = [
            rel
            for rel in jobs
            if not re.fullmatch(r"img/(?:full|thumb)/[A-Za-z0-9._-]+\.(?:png|webp)", rel)
        ]
        if missing_paths:
            _say(f"发现 {len(missing_paths)} 条路径不符合预期布局，已拒绝：{missing_paths[:5]}")
            return 1

        (target / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        write_readme(source, site, target, len(catalog))

        sec_counts: dict[str, int] = {}
        for d in catalog:
            sec_counts[d.get("section", "?")] = sec_counts.get(d.get("section", "?"), 0) + 1
        _say("sections: " + ", ".join(f"{k}={v}" for k, v in sorted(sec_counts.items())))

        ok = skip = fail = 0
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(lambda rel: download_file(client, site, rel, target), jobs):
                done += 1
                if result == "ok":
                    ok += 1
                elif result == "skip":
                    skip += 1
                else:
                    fail += 1
                if done % 200 == 0:
                    _say(f"progress: {done}/{len(jobs)} (ok={ok} skip={skip} fail={fail})")

    _say(f"done: total={len(jobs)} downloaded={ok} skipped={skip} failed={fail}")
    _say(f"data dir: {target}")
    if fail:
        print("有失败项，重跑本脚本可续传。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())