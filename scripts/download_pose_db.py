"""下载 ポーズ集 POSE-DB-01（https://ai-pose-gallery.pages.dev/）到本地姿势库。

数据形态（站点单页 HTML 内嵌）：
- `const D=[...]` 数组，1439 条；每条含 id / 系統 / ポーズ番号 / 分類 / ポーズ名 /
  アングル / サムネ / 原寸（后两者为站内相对路径）。

落盘布局（data/ 会被 .gitignore 忽略 —— 站点许可禁止再分发图片数据）：

    data/poses/pose-db-01/
        catalog.json          # D 数组逐字保留（真源）
        img/{id}.png          # 原寸 900×1200
        thumbs/{id}.jpg       # 300×400
        README.md             # 来源与许可说明

幂等：已存在且魔数正确的文件跳过；失败（魔数不符/网络错）重试 3 次。
代理红线：必须 trust_env=False 绕过本机 ALL_PROXY（与 exporters 同款）。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

SITE = "https://ai-pose-gallery.pages.dev/"
TARGET = Path(__file__).resolve().parent.parent / "data" / "poses" / "pose-db-01"

UA = {"user-agent": "charvar-pose-import/1.0 (personal reference library)"}
RETRIES = 3
WORKERS = 8

_print_lock = threading.Lock()


def _say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def fetch_catalog(client: httpx.Client) -> tuple[list[dict], str]:
    """拉首页 HTML，解出内嵌 `D=[...]` 数组。"""
    resp = client.get(SITE, timeout=60)
    resp.raise_for_status()
    html = resp.text
    marker = "D=["
    pos = html.index(marker) + len("D=")
    assert html[pos] == "["
    depth = 0
    in_str = None
    escaped = False
    i = pos
    while i < len(html):
        ch = html[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    data = json.loads(html[pos:i])
    if not isinstance(data, list) or not data:
        raise RuntimeError("目录数据为空或结构不对")
    return data, html


def write_readme(target: Path, count: int) -> None:
    (target / "README.md").write_text(
        f"""# 姿势库 pose-db-01（第三方数据，勿入 git）

来源：{SITE}（ポーズ集 POSE-DB-01），抓取时共 {count} 条。

站点许可（原文要点）：
- 作品制作可免费商用、可编辑、可参考；无需署名。
- **禁止再分发图片数据本身**（含元尺寸与加工后）—— 故本目录必须留在 data/ 下
  （.gitignore 覆盖），绝不能提交进仓库。
- 图像为灰色无脸人偶线稿；脸・服装・背景由提示词或其它参考图提供。
- 不得用于描绘真人或未成年。

目录结构：
- catalog.json：站点内嵌数据逐字保留（字段为日文键：系統/ポーズ番号/分類/
  ポーズ名/アングル/サムネ/原寸）。
- img/：原寸 PNG（900×1200）；thumbs/：JPEG 缩略图（300×400）。

重抓/补抓：`python3 scripts/download_pose_db.py`（幂等，魔数正确即跳过）。
入库：`python3 -m charvar.cli pose import data/poses/pose-db-01`。
""",
        encoding="utf-8",
    )


def _valid(path: Path, magic: bytes) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        return path.read_bytes()[: len(magic)] == magic
    except OSError:
        return False


def download_file(client: httpx.Client, rel: str, magic: bytes) -> str:
    """下载单个站内相对路径文件到本地同布局；返回 'ok' / 'skip' / 'fail'。"""
    url = SITE + rel
    dest = TARGET / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _valid(dest, magic):
        return "skip"
    for attempt in range(RETRIES):
        try:
            resp = client.get(url, timeout=90)
            resp.raise_for_status()
            body = resp.content
            if body[: len(magic)] != magic:
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
    TARGET.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, headers=UA, follow_redirects=True) as client:
        catalog, html = fetch_catalog(client)
        _say(f"catalog: {len(catalog)} items from {SITE}")

        jobs: list[tuple[str, bytes]] = []
        for d in catalog:
            thumb = str(d.get("サムネ") or "").strip()
            full = str(d.get("原寸") or "").strip()
            if thumb and not thumb.startswith(("http://", "https://")):
                jobs.append((thumb, b"\xff\xd8"))  # JPEG
            if full and not full.startswith(("http://", "https://")):
                jobs.append((full, b"\x89PNG"))
        missing_paths = [rel for rel, _ in jobs if not re.fullmatch(
            r"(?:img|thumbs)/[A-Za-z0-9._-]+\.(?:png|jpg)", rel)]
        if missing_paths:
            _say(f"发现 {len(missing_paths)} 条路径不符合预期布局，已拒绝：{missing_paths[:5]}")
            return 1

        (TARGET / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        write_readme(TARGET, len(catalog))

        ok = skip = fail = 0
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(lambda job: download_file(client, job[0], job[1]), jobs):
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
    _say(f"data dir: {TARGET}")
    if fail:
        print("有失败项，重跑本脚本可续传。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())