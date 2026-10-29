"""下载 衣装集 COSTUME-DB-01（https://ai-costume-gallery.pages.dev/）到本地服装库。

数据形态（站点单页 HTML 内嵌，与姿势库 pose-db-01 同作者同套路）：
- `const D=[...]` 数组，1407 条；每条含 id / カテゴリ / キーワード（英文服装描述）/
  サムネ / 画像 / 原寸（后三者为站内相对路径）。
- `const JA={...}` 日文词→英文关键词的检索词表（站内检索的中日桥）。

落盘布局（data/ 由 .gitignore 忽略 —— 站点许可禁止再分发图片数据）：

    data/costumes/costume-db-01/
        catalog.json          # D 数组逐字保留（真源）
        glossary.json         # JA 词表逐字保留（检索词映射）
        thumbs/{id}.webp      # 360×540
        img/{id}.webp         # 1366×2048（网页详情大图）
        png/{id}.png          # 原寸 1366×2048 无损（后续参考图细部用）
        README.md             # 来源与许可说明

幂等：已存在且魔数正确的文件跳过；失败（魔数不符/网络错）重试 3 次，重跑续传。
代理：外网抓取要吃本机代理环境变量（ALL_PROXY/HTTPS_PROXY）—— 与 exporters 的
trust_env=False 红线不冲突，那条管的是**内网** ComfyUI 地址。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

SITE = "https://ai-costume-gallery.pages.dev/"
TARGET = Path(__file__).resolve().parent.parent / "data" / "costumes" / "costume-db-01"

UA = {"user-agent": "charvar-costume-import/1.0 (personal reference library)"}
RETRIES = 3
WORKERS = 8

# webp：RIFF + 4 字节长度 + WEBP；png / jpeg 用常规魔数。
MAGICS = {
    ".webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    ".png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    ".jpg": lambda b: b[:2] == b"\xff\xd8",
    ".jpeg": lambda b: b[:2] == b"\xff\xd8",
}

_print_lock = threading.Lock()


def _say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def extract_js_value(html: str, marker: str) -> object:
    """解出 `const NAME=<JSON>;` 的值（栈配平、字符串感知，与 pose 下载脚本同款）。"""
    pos = html.index(marker)
    i = pos + len(marker)
    if html[i] == "=":
        i += 1
    depth = 0
    in_str = None
    escaped = False
    start = i
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
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    if depth != 0:
        raise RuntimeError(f"站点数据 {marker}… 解析失败（括号不配平）")
    return json.loads(html[start:i])


def write_readme(target: Path, count: int) -> None:
    (target / "README.md").write_text(
        f"""# 服装库 costume-db-01（第三方数据，勿入 git）

来源：{SITE}（衣装集 COSTUME-DB-01），抓取时共 {count} 条。
作者 X：https://x.com/giaaibusi

站点许可（原文要点，站内「注意点」）：
- 無償で利用できます（作品制作での商用利用・編集・参考利用OK）。
  （可无偿使用：作品制作的商用利用・编辑・参考 OK）
- **画像データそのもの（元サイズ・加工後を含む）の再配布・転載・再販は禁止です。**
  （禁止再分发图片数据本身，含原尺寸与加工后——故本目录必须留在 data/ 下，
  .gitignore 覆盖，绝不能提交进仓库。仅可再分发自己的作品。）
- クレジット表記は不要です（任意）。画像のメタデータは削除済みです。無保証でご利用ください。
- AI で作った架空の衣装（全部 t2i 生成）；掲載前に既存ブランドとの類似チェックは行っているが、
  他社の権利を侵害しないことを保証するものではありません。
- 実在人物や未成年を表す用途、公序良俗に反する用途には使わないでください。

目录结构：
- catalog.json：站点内嵌 D 数组逐字保留（字段为日文键：カテゴリ/キーワード/サムネ/画像/原寸）。
- glossary.json：站点内嵌 JA 词表逐字保留（日文检索词 → 英文关键词）。
- thumbs/：360×540 webp 缩略图；img/：1366×2048 webp 详情图；png/：1366×2048 无损原寸。

重抓/补抓：`python3 scripts/download_costume_db.py`（幂等，魔数正确即跳过，可直接续传）。
页面服务：web 顶栏「服装」页签（后端 /costumes*，charvar/costumes.py）。
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


def download_file(client: httpx.Client, rel: str) -> str:
    """下载单个站内相对路径文件到本地同布局；返回 'ok' / 'skip' / 'fail'。"""
    url = SITE + rel
    dest = TARGET / rel
    magic_fn = MAGICS.get(dest.suffix.lower())
    if magic_fn is None:
        raise RuntimeError(f"未知扩展名：{rel}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _valid(dest, magic_fn):
        return "skip"
    for attempt in range(RETRIES):
        try:
            resp = client.get(url, timeout=90)
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
    TARGET.mkdir(parents=True, exist_ok=True)
    # 外网抓取要走本机代理；ALL_PROXY 是 socks5，httpx 缺 socksio 时会 ImportError，
    # 故优先显式用 HTTP(S)_PROXY 的 http 代理，都没有则直连。
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    with httpx.Client(headers=UA, follow_redirects=True, proxy=proxy or None) as client:
        resp = client.get(SITE, timeout=60)
        resp.raise_for_status()
        html = resp.text
        try:
            catalog = extract_js_value(html, "const D=")
            glossary = extract_js_value(html, "const JA=")
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            _say(f"catalog 解析失败：{exc}")
            return 1
        if not isinstance(catalog, list) or not catalog:
            _say("目录数据为空或结构不对")
            return 1
        if not isinstance(glossary, dict) or not glossary:
            _say("检索词表为空或结构不对（继续，只影响日文搜索）")
            glossary = {}
        _say(f"catalog: {len(catalog)} items from {SITE}")

        jobs: list[str] = []
        for d in catalog:
            for key in ("サムネ", "画像", "原寸"):
                rel = str(d.get(key) or "").strip()
                if rel and not rel.startswith(("http://", "https://")):
                    jobs.append(rel)
        missing_paths = [
            rel
            for rel in jobs
            if not re.fullmatch(r"(?:thumbs|img|png)/[A-Za-z0-9._-]+\.(?:webp|png|jpg)", rel)
        ]
        if missing_paths:
            _say(f"发现 {len(missing_paths)} 条路径不符合预期布局，已拒绝：{missing_paths[:5]}")
            return 1

        (TARGET / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if glossary:
            (TARGET / "glossary.json").write_text(
                json.dumps(glossary, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        write_readme(TARGET, len(catalog))

        ok = skip = fail = 0
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(lambda rel: download_file(client, rel), jobs):
                done += 1
                if result == "ok":
                    ok += 1
                elif result == "skip":
                    skip += 1
                else:
                    fail += 1
                if done % 300 == 0:
                    _say(f"progress: {done}/{len(jobs)} (ok={ok} skip={skip} fail={fail})")

    _say(f"done: total={len(jobs)} downloaded={ok} skipped={skip} failed={fail}")
    _say(f"data dir: {TARGET}")
    if fail:
        print("有失败项，重跑本脚本可续传。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())