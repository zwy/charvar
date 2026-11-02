"""抓取 衣装集 服饰库到本地（costume-db-01 / costume-anime-db-01，--source 选库）。

数据形态（站点单页 HTML 内嵌，与姿势库 pose-db-01 同作者同套路）：
- `const D=[...]` 数组；每条含 id / カテゴリ / キーワード（英文服装描述）/
  サムネ / 画像 / 原寸（后三者为站内相对路径）。
- `const JA={...}` 日文词→英文关键词的检索词表（站内检索的中日桥）。

落盘布局（本地图库素材目录，**不随 Git 仓库分发**——`data/costumes/` 在
.gitignore；现行条款与获取边界见仓库根 README「第三方参考资源」）：

    data/costumes/<source>/
        catalog.json          # D 数组逐字保留（真源）
        glossary.json         # JA 词表逐字保留（检索词映射）
        thumbs/{id}.webp      # 360×540
        img/{id}.webp         # 1366×2048（网页详情大图）
        png/{id}.png          # 原寸 1366×2048 无损（后续参考图细部用）
        README.md             # 来源与条款摘要

幂等：已存在且魔数正确的文件跳过；失败（魔数不符/网络错）重试 3 次，重跑续传。
代理：外网抓取要吃本机代理环境变量（ALL_PROXY/HTTPS_PROXY）—— 与 exporters 的
trust_env=False 红线不冲突，那条管的是**内网** ComfyUI 地址。

用法：python3 scripts/download_costume_db.py --source costume-db-01|costume-anime-db-01
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

# 库登记：source id → 站点 URL / README 用的条目短语。加新站 = 加一行
# （charvar/costumes.py 的 COSTUME_SOURCES 同步登记）。
SITES: dict[str, dict] = {
    "costume-db-01": {
        "url": "https://ai-costume-gallery.pages.dev/",
        "title": "衣装集 COSTUME-DB-01",
    },
    "costume-anime-db-01": {
        "url": "https://ai-costume-anime-gallery.pages.dev/",
        "title": "衣装集 アニメ版 COSTUME-ANIME-DB",
    },
}

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


# 站点现行条款摘要（2026-10-30 起各站统一口径；原文以各站「注意点」为准）：
# 程序不解释许可以外的许可，只记录原文要点与「本地使用、不随仓库分发」的项目边界。
TERMS_NOTE = """站点现行许可要点（本机抓取当天核对，原文以站内「注意点」为准）：
- 無償で利用できます（作品制作での商用利用・編集・参考利用OK）。
- 画像データそのもの（元サイズ・加工後を含む）の再配布・転載・再販は禁止。
- 一括ダウンロード（ツール等による自動取得を含む）と、取得した画像の
  再配布・転載・販売は禁止。「生成の参考に1枚ずつ使う分は、これまでどおり
  ご利用いただけます」（逐张参考使用不受影响）。
- クレジット表記不要（任意）；画像のメタデータは削除済み；無保証。
- AI で作った架空の衣装（全部 t2i）；実在人物・未成年を表す用途は不可。

项目边界：本目录是本地素材目录（data/ 在 .gitignore），不随 Git 仓库分发；
下载工具由仓库持有人在本地自行执行。获取与使用方式以仓库根 README
「第三方参考资源 / 用户自行获取与使用」为准，与上游现行规则冲突处以上游为准。
"""


def write_readme(target: Path, site: dict, count: int) -> None:
    (target / "README.md").write_text(
        f"""# 服装库 {target.name}

来源：{site['url']}（{site['title']}），抓取时共 {count} 条。
作者 X：https://x.com/giaaibusi

目录结构：
- catalog.json：站点内嵌 D 数组逐字保留（字段为日文键：カテゴリ/キーワード/サムネ/画像/原寸）。
- glossary.json：站点内嵌 JA 词表逐字保留（日文检索词 → 英文关键词）。
- thumbs/：360×540 webp 缩略图；img/：1366×2048 webp 详情图；png/：1366×2048 无损原寸。

{TERMS_NOTE}
重抓/补抓：`python3 scripts/download_costume_db.py --source {target.name}`（幂等，魔数正确即跳过，可直接续传）。
页面服务：web 顶栏「服饰」页签（后端 /costumes*，charvar/costumes.py）。
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


def download_file(client: httpx.Client, target: Path, url: str, rel: str) -> str:
    """下载单个站内相对路径文件到本地同布局；返回 'ok' / 'skip' / 'fail'。"""
    dest = target / rel
    magic_fn = MAGICS.get(dest.suffix.lower())
    if magic_fn is None:
        raise RuntimeError(f"未知扩展名：{rel}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _valid(dest, magic_fn):
        return "skip"
    for attempt in range(RETRIES):
        try:
            resp = client.get(url + rel, timeout=90)
            resp.raise_for_status()
            body = resp.content
            if not magic_fn(body[:512]):
                raise ValueError(f"魔数不符（预期图，得到其它内容）：{url}{rel}")
            dest.write_bytes(body)
            return "ok"
        except Exception as exc:  # noqa: BLE001 网络与校验错误统一重试
            if attempt == RETRIES - 1:
                _say(f"FAIL {rel}: {exc}")
                return "fail"
            time.sleep(1.5 * (attempt + 1))
    return "fail"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default="costume-db-01", choices=sorted(SITES),
                    help="要抓取的服饰库 id（缺省 costume-db-01）")
    args = ap.parse_args()
    site = SITES[args.source]
    url = site["url"]
    target = Path(__file__).resolve().parent.parent / "data" / "costumes" / args.source

    target.mkdir(parents=True, exist_ok=True)
    # 外网抓取要走本机代理；ALL_PROXY 是 socks5，httpx 缺 socksio 时会 ImportError，
    # 故优先显式用 HTTP(S)_PROXY 的 http 代理，都没有则直连。
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    with httpx.Client(headers=UA, follow_redirects=True, proxy=proxy or None) as client:
        resp = client.get(url, timeout=60)
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
        _say(f"catalog: {len(catalog)} items from {url}")

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

        (target / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if glossary:
            (target / "glossary.json").write_text(
                json.dumps(glossary, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        write_readme(target, site, len(catalog))

        ok = skip = fail = 0
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(lambda rel: download_file(client, target, url, rel), jobs):
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
    _say(f"data dir: {target}")
    if fail:
        print("有失败项，重跑本脚本可续传。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())