"""下载 ポーズ集（同作者的姊妹站数据集）到本地姿势库。

同一作者的系列站点是同一套单页模板：首页 HTML 内嵌 `const D=[...]` 数组；
数据条目字段 id / 系統 / ポーズ番号 / 分類 / ポーズ名 / アングル / サムネ /
原寸（后两者为站内相对路径）。按 --source 选站点与落盘目录：

    python3 scripts/download_pose_db.py                       # pose-db-01（线稿）
    python3 scripts/download_pose_db.py --source pose-db-02   # depth 深度图

落盘布局：

    data/poses/<source>/
        catalog.json          # D 数组逐字保留（真源）
        img/{id}.png          # 原寸 900×1200
        thumbs/{id}.jpg       # 300×400
        README.md             # 来源与许可说明

幂等：已存在且魔数正确的文件跳过；失败（魔数不符/网络错）重试 3 次。
代理红线：必须 trust_env=False 绕过本机 ALL_PROXY（与 exporters 同款）。
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

# source_id → 站点（同作者系列站；新库 = 加一行 + 确认首页仍是同一模板）
SITES: dict[str, str] = {
    "pose-db-01": "https://ai-pose-gallery.pages.dev/",
    "pose-db-02": "https://ai-pose-depth-gallery.pages.dev/",
}

ROOT = Path(__file__).resolve().parent.parent

UA = {"user-agent": "charvar-pose-import/1.0 (personal reference library)"}
RETRIES = 3
WORKERS = 8

_print_lock = threading.Lock()


def _say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def target_dir(source: str) -> Path:
    return ROOT / "data" / "poses" / source


def fetch_catalog(client: httpx.Client, site: str) -> tuple[list[dict], str]:
    """拉首页 HTML，解出内嵌 `D=[...]` 数组。"""
    resp = client.get(site, timeout=90, headers=UA)
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


def write_readme(source: str, site: str, target: Path, count: int) -> None:
    (target / "README.md").write_text(
        f"""# 姿势库 {source}

来源：{site}，抓取时共 {count} 条。图片：人脸无细节的灰色人偶线稿/深度图
（白=近）；脸・服装・背景由提示词或其它参考图提供。

目录结构：
- catalog.json：站点内嵌 D 数组逐字保留（字段为日文键：系統/ポーズ番号/分類/
  ポーズ名/アングル/サムネ/原寸）。
- img/：原寸 PNG；thumbs/：JPEG 缩略图。

重抓/补抓：`python3 scripts/download_pose_db.py --source {source}`（幂等，
魔数正确即跳过）。入库：`python3 -m charvar.cli pose import data/poses/{source}`。

许可（站点「注意点」原文要点）：
- 無償で利用できます（作品制作での商用利用・編集・参考利用OK）；クレジット表記不要；無保証。
- 不得用于描绘实存人物或未成年（元はマネキンの画像で、実在の人物ではありません）。
- 随仓库分发：作者在 X 公告允许二次发布，本仓库据此把数据随仓库公开 ——
  详见仓库 README（许可说明由仓库持有人维护）。
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


def download_file(
    client: httpx.Client, site: str, rel: str, magic: bytes, target: Path
) -> str:
    """下载单个站内相对路径文件到本地同布局；返回 'ok' / 'skip' / 'fail'。"""
    url = site + rel
    dest = target / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _valid(dest, magic):
        return "skip"
    for attempt in range(RETRIES):
        try:
            resp = client.get(url, timeout=120, headers=UA)
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
    parser = argparse.ArgumentParser(description="下载ポーズ集到 data/poses/<source>")
    parser.add_argument("--source", default="pose-db-01", help=f"库 id（{'/'.join(SITES)}）")
    parser.add_argument("--site", default="", help="站点 URL 覆盖（默认按 source 查表）")
    args = parser.parse_args()
    source = str(args.source).strip()
    if source not in SITES:
        import sys as _sys

        _sys.exit(f"未知 source：{source}（可选 {'/'.join(SITES)}；新站先在 SITES 表登记）")
    site = str(args.site).strip() or SITES[source]
    if not site.endswith("/"):
        site += "/"
    target = target_dir(source)
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, follow_redirects=True) as client:
        catalog, _html = fetch_catalog(client, site)
        _say(f"catalog: {len(catalog)} items from {site} -> {target}")

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

        (target / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        write_readme(source, site, target, len(catalog))

        ok = skip = fail = 0
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(
                lambda job: download_file(client, site, job[0], job[1], target), jobs
            ):
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