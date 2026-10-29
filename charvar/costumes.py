"""服装库（第三方数据 costume-db-01）：浏览 / 文件定位。

数据真源在磁盘 ``data/costumes/costume-db-01/``（catalog.json + glossary.json +
thumbs/ + img/ + png/，由 ``scripts/download_costume_db.py`` 抓取）。与姿势库
pose-db-01 同作者同套路（站点单页 HTML 内嵌 `const D=[...]`），但 **v1 不建
DB 表**：浏览页一次拉全 1407 条小字段，磁盘直读即可；生图流程接入需要索引时
再补 ``costume import``（姿势库同款 import_catalog 模式）。

图片是 t2i 生成的架空服装（无人物、2:3、白底）。许可同姿势库：作品制作可免费
商用、**禁止再分发图片数据本身** —— 目录留在 data/（.gitignore 覆盖），绝不进 git。

与姿势库的差异（后续集成时注意）：
- 每条一张图，无视角双键；
- 无名称字段，``キーワード`` 英文服装描述就是提示词原料（站点「説明文をコピー」语义）；
- catalog 的 ``原寸`` png 与 ``画像`` webp 同像素（1366×2048），网页展示走 webp，
  png 是无损版，留给后续参考图的细部来源。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from charvar import paths

# 库 id（磁盘目录名与 URL source 段；同型可扩 v2 库）
SOURCE_ID = "costume-db-01"

# item_id / source 是浏览器发来的关键字（拼进查找 URL 与文件定位），只放行安全字符。
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 站点目录次序（页签次序与站点一致；未收录的原文排在收录项之后按字符串排）
CATEGORY_ORDER = ["春", "夏", "秋", "冬", "ランジェリー", "パーティ", "水着", "セクシー"]

CATEGORY_ZH: dict[str, str] = {
    "春": "春装",
    "夏": "夏装",
    "秋": "秋装",
    "冬": "冬装",
    "ランジェリー": "内衣",
    "パーティ": "派对",
    "水着": "泳装",
    "セクシー": "性感系",
}


def category_zh(category: str) -> str:
    return CATEGORY_ZH.get(category, category)


def source_dir() -> Path:
    return paths.data_dir() / "costumes" / SOURCE_ID


def catalog_path() -> Path:
    return source_dir() / "catalog.json"


def glossary_path() -> Path:
    return source_dir() / "glossary.json"


class CostumeError(Exception):
    """服装库错误（catalog 缺失/损坏 / 查不到该条 / 文件缺失）。"""


# 落盘相对路径只认这个布局（与下载脚本同款；catalog 字段逐字来自站点，这里挡
# 住 path 漂移与 ../ 逃逸 —— item_id 会在 _load_catalog 里再对一遍文件名）。
_REL_PATH_RE = re.compile(r"^(?:thumbs|img|png)/[A-Za-z0-9._-]+\.(?:webp|png|jpg|jpeg)$")

# catalog 每条 → 归一化行：{item_id, category, keywords, thumb_rel, image_rel, hay}
_catalog_cache: dict = {"mtime_size": None, "items": []}
_glossary_cache: dict = {"mtime_size": None, "ja": {}}


def _mtime_size(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _load_catalog() -> list[dict]:
    """读 catalog.json（mtime+size 失效判定）并整批校验，坏一条就整批报错。"""
    stamp = _mtime_size(catalog_path())
    if stamp is not None and stamp == _catalog_cache["mtime_size"]:
        return _catalog_cache["items"]
    if stamp is None:
        raise CostumeError(
            f"找不到 catalog.json：{catalog_path()}（先跑 scripts/download_costume_db.py）"
        )
    try:
        raw = json.loads(catalog_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CostumeError(f"catalog.json 读取失败：{exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise CostumeError("catalog.json 为空或不是数组")

    items: list[dict] = []
    bad: list[str] = []
    for entry in raw:
        item_id = str(entry.get("id") or "").strip()
        keywords = str(entry.get("キーワード") or "").strip()
        category = str(entry.get("カテゴリ") or "").strip()
        thumb_rel = str(entry.get("サムネ") or "").strip()
        image_rel = str(entry.get("画像") or "").strip()
        png_rel = str(entry.get("原寸") or "").strip()
        if not ITEM_ID_RE.fullmatch(item_id):
            bad.append(item_id[:60] or "(空)")
            continue
        if not _REL_PATH_RE.fullmatch(thumb_rel) or Path(thumb_rel).stem != item_id:
            raise CostumeError(f"サムネ 路径不合法：{thumb_rel[:60]}")
        if not _REL_PATH_RE.fullmatch(image_rel) or Path(image_rel).stem != item_id:
            raise CostumeError(f"画像 路径不合法：{image_rel[:60]}")
        if png_rel and (not _REL_PATH_RE.fullmatch(png_rel) or Path(png_rel).stem != item_id):
            raise CostumeError(f"原寸 路径不合法：{png_rel[:60]}")
        items.append(
            {
                "item_id": item_id,
                "category": category,
                "keywords": keywords,
                "thumb_rel": thumb_rel,
                "image_rel": image_rel,
                "png_rel": png_rel,
                # 检索 haystack：id + 英文关键词 + 日文目录原文 + 中文目录名
                "hay": " ".join((item_id, keywords, category, category_zh(category))).lower(),
            }
        )
    if bad:
        raise CostumeError(f"发现 {len(bad)} 条 id 不合法（如 {bad[0]}…），整批拒绝")
    _catalog_cache["mtime_size"] = stamp
    _catalog_cache["items"] = items
    return items


def _load_glossary() -> dict[str, str]:
    """站点 JA 词表（日文检索词 → 英文关键词）；找不到/读不改空，搜索退化为纯英文。"""
    stamp = _mtime_size(glossary_path())
    if stamp is not None and stamp == _glossary_cache["mtime_size"]:
        return _glossary_cache["ja"]
    ja: dict[str, str] = {}
    if stamp is not None:
        try:
            data = json.loads(glossary_path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ja = {str(k): str(v) for k, v in data.items() if k and v}
        except (OSError, json.JSONDecodeError):
            ja = {}
    _glossary_cache["mtime_size"] = stamp
    _glossary_cache["ja"] = ja
    return ja


def _token_hits(token: str, hay: str, glossary: dict[str, str]) -> bool:
    """单词命中：词在 haystack 里，或词里包含日文词表键且其英文值在 haystack 里。

    「黒レース」整词不在 hay 也放行 —— キー「黒」含在词里、其英文值 black 在 hay。
    """
    if token in hay:
        return True
    for ja, en in glossary.items():
        if ja.lower() in token:
            en_l = en.strip().lower()
            if en_l and en_l in hay:
                return True
    return False


def list_items(*, category: str = "", q: str = "") -> dict:
    """服装清单 + 目录元数据 + 检索词表，服装浏览页一次拉全（客户端过滤）。

    category 按存储原文过滤；q 逐词 AND（与前端 costumegallery.js 的过滤同语义）：
    对 id/英文关键词/中日目录名大小写不敏感，日文词经 glossary 扩写成英文一并命中。
    """
    items = _load_catalog()
    glossary = _load_glossary()
    tokens = [t for t in str(q or "").strip().lower().split() if t]

    category_counts: dict[str, int] = {}
    out: list[dict] = []
    for it in items:
        category_counts[it["category"]] = category_counts.get(it["category"], 0) + 1
        if category and it["category"] != category:
            continue
        if tokens and not all(_token_hits(t, it["hay"], glossary) for t in tokens):
            continue
        out.append(
            {
                "item_id": it["item_id"],
                "category": it["category"],
                "category_zh": category_zh(it["category"]),
                "keywords": it["keywords"],
                "thumb_url": f"/costumes/image/{SOURCE_ID}/{it['item_id']}/thumb",
                "image_url": f"/costumes/image/{SOURCE_ID}/{it['item_id']}/full",
            }
        )
    # 目录按站点固定次序（CATEGORY_ORDER）排；未收录原文退居其后按字符串排
    categories: list[dict] = []
    for cat in CATEGORY_ORDER:
        if cat in category_counts:
            categories.append({"id": cat, "zh": category_zh(cat), "count": category_counts[cat]})
    for cat, n in sorted(category_counts.items()):
        if cat not in CATEGORY_ORDER:
            categories.append({"id": cat, "zh": category_zh(cat), "count": n})
    return {
        "source": SOURCE_ID,
        "categories": categories,
        "glossary": glossary,
        "items": out,
    }


def image_file(item_id: str, variant: str) -> Path:
    """(item_id, thumb|full) → 缩略图 / 详情图路径。

    只认 catalog 里的条目 —— 文件名永远由服务端从 catalog 行定（与主体槽位 /
    姿势库同一原则：浏览器不给路径）。thumb = webp 缩略图，full = webp 详情图
    （1366×2048；无损原寸 png 不上网页，留给后续参考图通路）。
    """
    item_id = str(item_id or "").strip()
    if not ITEM_ID_RE.fullmatch(item_id):
        raise CostumeError(f"非法服装 id：{item_id[:60]}")
    rel = None
    for it in _load_catalog():
        if it["item_id"] == item_id:
            rel = it["thumb_rel"] if variant == "thumb" else it["image_rel"]
            break
    if rel is None:
        raise CostumeError(f"服装库没有这条服装：{item_id}")
    path = source_dir() / rel
    if not path.is_file():
        raise CostumeError(f"图片文件缺失：{rel}（重跑 scripts/download_costume_db.py）")
    return path
# ---------- 生图参考图（role=costume：只锚服装，不锚身份/姿势） ----------

def item_keywords(item_id: str) -> str:
    """条目的英文关键词（生图「服装要点」注记的原料）；未知 id 报 CostumeError。"""
    item_id = str(item_id or "").strip()
    if not ITEM_ID_RE.fullmatch(item_id):
        raise CostumeError(f"非法服装 id：{item_id[:60]}")
    for it in _load_catalog():
        if it["item_id"] == item_id:
            return it["keywords"]
    raise CostumeError(f"服装库没有这条服装：{item_id}")


def raw_reference_file(item_id: str) -> Path:
    """参考图源文件：无损原寸 png 优先（仅一步有损），缺失回退详情 webp。

    img 与 png 同像素（1366×2048），只差编码；catalog 校验过路径但文件在不在
    这里才见真章（png_rel 有值而 png 文件缺失 → 回退 webp，不报错）。
    """
    item = next((it for it in _load_catalog() if it["item_id"] == item_id), None)
    if item is None:
        raise CostumeError(f"服装库没有这条服装：{item_id}")
    if item["png_rel"]:
        png = source_dir() / item["png_rel"]
        if png.is_file():
            return png
    return image_file(item_id, "full")


def prepared_reference_file(item_id: str) -> Path:
    """参考图发送用：压缩版（长边 1536 / JPEG q88 + 白底合成），缓存于服装库目录。

    缓存文件名 = costume_{item_id}.jpg，与主体图 / 姿势库同一套 mtime+size 失效判定。
    """
    from charvar import reference_images  # 延迟导入避免循环

    return reference_images.prepared_reference(
        raw_reference_file(item_id), source_dir(), f"costume_{item_id}"
    )
