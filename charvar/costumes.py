"""服装库（costume_items + costume_prompts 双表，仿 pose 双表分层）。

- ``costume_items``：可重灌索引层 —— 站点快照（costume-db-01）与自建条目
  （studio）通用（display_type / keywords / 图路径 / subject_policy / caption_tags）。
- ``costume_prompts``：用户文本层 —— 每条一条文生图提示词（source=manual/ai_draft）；
  catalog 重灌绝不碰（pose_actions 同红线）。
- 磁盘 ``data/costumes/costume-db-01/``（catalog.json + glossary.json + 图三档）
  只剩「导入源」角色（``charvar costume import`` 幂等重灌索引层）；display_types.json
  侧车降级为 import 期种子。自建条目走 ``charvar costume import-outfits`` 迁入
  与后续 CLI 编辑（DB 是运行时真源，``charvar costume export`` 快照回 presets/）。

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
import sqlite3
from pathlib import Path

from charvar import paths

# 库 id（磁盘目录名与 URL source 段）；studio = 自建条目（outfits 迁移 / 手工新增）
SOURCE_ID = "costume-db-01"
STUDIO_SOURCE_ID = "studio"

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


def display_types_path() -> Path:
    return source_dir() / "display_types.json"


# 展示形态（display_type）：ghost = 隐形模特（ghost mannequin）——无头无身、服装保持
# 穿着的立体形态悬空陈列；flat_lay = 平铺展示——俯视平铺陈列。站点 catalog 没有这个
# 字段且 1407 条实测全 ghost（缺省缺条 = ghost）；侧车文件只记要覆盖的条目。
# catalog.json 逐字保留红线不动，类型数据进侧车（display_types.json 同目录）。
DISPLAY_TYPES: tuple[str, ...] = ("ghost", "flat_lay")
_display_types_cache: dict = {"mtime_size": None, "map": {}}


def _load_display_types() -> dict[str, str]:
    """读侧车 display_types.json（mtime+size 失效）；坏条目整批拒绝 —— 自家文件要严。"""
    stamp = _mtime_size(display_types_path())
    if stamp == _display_types_cache["mtime_size"]:
        return _display_types_cache["map"]
    mapping: dict[str, str] = {}
    if stamp is not None:
        try:
            raw = json.loads(display_types_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CostumeError(f"display_types.json 读取失败：{exc}") from exc
        if not isinstance(raw, dict):
            raise CostumeError("display_types.json 必须是 {item_id: 类型} 对象")
        for item_id, value in raw.items():
            if not ITEM_ID_RE.fullmatch(str(item_id)) or value not in DISPLAY_TYPES:
                raise CostumeError(f"display_types.json 条目不合法：{item_id}: {value}")
            mapping[item_id] = value
    _display_types_cache["mtime_size"] = stamp
    _display_types_cache["map"] = mapping
    return mapping


class CostumeError(Exception):
    """服装库错误（catalog 缺失/损坏 / 查不到该条 / 文件缺失）。"""


def _is_png(path: Path) -> bool:
    """PNG 魔数（与 poses._is_png 同判法）。"""
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _is_webp(path: Path) -> bool:
    """WEBP 魔数（RIFF…WEBP）。"""
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def costume_root(source_id: str = SOURCE_ID) -> Path:
    """某服装 source 的磁盘目录（data/costumes/<source_id>/）；图与 _ref_cache 都在里面。"""
    return paths.data_dir() / "costumes" / (str(source_id or SOURCE_ID))


def _now() -> str:
    from charvar import storage  # 延迟导入，poses 同理由

    return storage.utcnow()


# 落盘相对路径只认这个布局（与下载脚本同款；catalog 字段逐字来自站点，这里挡
# 住 path 漂移与 ../ 逃逸 —— item_id 会在 _load_catalog 里再对一遍文件名）。
_REL_PATH_RE = re.compile(r"^(?:thumbs|img|png)/[A-Za-z0-9._-]+\.(?:webp|png|jpg|jpeg)$")

# catalog 每条 → 归一化行：{item_id, category, keywords, thumb_rel, image_rel, hay, display_type}
_catalog_cache: dict = {"mtime_size": None, "items": []}
_glossary_cache: dict = {"mtime_size": None, "ja": {}}


def _mtime_size(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _load_catalog() -> list[dict]:
    """catalog.json → 归一化行（mtime+size 失效判定含侧车 display_types.json；坏行整批拒绝）。"""
    stamp = (_mtime_size(catalog_path()), _mtime_size(display_types_path()))
    if stamp != _catalog_cache["mtime_size"]:
        _cache_rows(stamp, _parse_catalog())
    for it in _catalog_cache["items"]:
        it["display_type"] = _load_display_types().get(it["item_id"], "ghost")
    return _catalog_cache["items"]


def _cache_rows(stamp, items: list[dict]) -> None:
    _catalog_cache["mtime_size"] = stamp
    _catalog_cache["items"] = items


def _parse_catalog() -> list[dict]:
    """解析 + 整批校验（不含 display_type —— 那在 _load_catalog 里并侧车合并）。"""
    try:
        raw = json.loads(catalog_path().read_text(encoding="utf-8"))
    except OSError as exc:
        raise CostumeError(
            f"找不到 catalog.json：{catalog_path()}（先跑 scripts/download_costume_db.py）"
        ) from exc
    except json.JSONDecodeError as exc:
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
    return items


# ---------- 导入（磁盘 catalog.json + 侧车 → costume_items；用户文本层不碰） ----------

def import_catalog(conn: "sqlite3.Connection", source_id: str = SOURCE_ID) -> dict:
    """catalog.json（+ display_types.json 侧车）逐条 upsert 进 costume_items（幂等）。

    校验从紧（姿势库同款）：解析层管 id/相对路径布局；这里再验图在盘 + 魔数
    （thumbs/img webp 必须在；png 可选、在则验）—— 灌一半的索引比空库难排查，
    缺图整批拒绝（先补跑下载脚本）。costume_prompts（用户文本层）绝不碰。
    """
    rows_raw = _load_catalog()  # 触发整批校验并与侧车合并 display_type
    src = costume_root(source_id)
    missing: list[str] = []
    rows: list[tuple] = []
    for it in rows_raw:
        if not _is_webp(src / it["thumb_rel"]) or not _is_webp(src / it["image_rel"]):
            missing.append(f"{it['item_id']}（thumbs/img 缺失或魔数不符）")
            continue
        if it["png_rel"] and (src / it["png_rel"]).exists() and not _is_png(src / it["png_rel"]):
            raise CostumeError(f"原寸 png 魔数不符：{it['png_rel']}")
        rows.append(
            (
                source_id, it["item_id"], it["display_type"], it["category"], "",
                it["keywords"], it["png_rel"], it["image_rel"], it["thumb_rel"],
                "", "", _now(), _now(),
            )
        )
    if missing:
        raise CostumeError(
            f"{len(missing)} 条图片缺失（如 {missing[0]}…）—— 先补跑 scripts/download_costume_db.py 再 import"
        )
    conn.executemany(
        """INSERT INTO costume_items
           (source_id, item_id, display_type, category, label, keywords,
            file_path, image_path, thumb_path, subject_policy, caption_tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id, item_id) DO UPDATE SET
             display_type=excluded.display_type, category=excluded.category,
             label=excluded.label, keywords=excluded.keywords,
             file_path=excluded.file_path, image_path=excluded.image_path,
             thumb_path=excluded.thumb_path, updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()
    return {"source_id": source_id, "items": len(rows)}

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


def list_items(
    conn: sqlite3.Connection,
    *,
    source_id: str = "",
    category: str = "",
    display_type: str = "",
    q: str = "",
) -> dict:
    """服装清单 + 目录元数据 + 检索词表，服装浏览页一次拉全（读 DB；未导入报错提示 import）。

    category/display_type 按存储原文过滤；q 逐词 AND（与前端 costumegallery.js 的
    过滤同语义）：对 id/英文关键词/中日目录名大小写不敏感，日文词经 glossary 扩写
    成英文一并命中。prompt_text 由 LEFT JOIN costume_prompts 带出。
    """
    n_rows = conn.execute("SELECT COUNT(*) FROM costume_items").fetchone()[0]
    if not n_rows:
        raise CostumeError("服装库还是空的 —— 先跑 charvar costume import")
    glossary = _load_glossary()
    tokens = [t for t in str(q or "").strip().lower().split() if t]

    rows = conn.execute(
        "SELECT ci.source_id, ci.item_id, ci.display_type, ci.category, ci.label,"
        " ci.keywords, ci.thumb_path, ci.image_path, ci.file_path,"
        " cp.prompt_text"
        " FROM costume_items AS ci"
        " LEFT JOIN costume_prompts AS cp ON cp.source_id = ci.source_id AND cp.item_id = ci.item_id"
    ).fetchall()

    sources: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    out: list[dict] = []
    for r in rows:
        sources[r["source_id"]] = sources.get(r["source_id"], 0) + 1
        category_counts[r["category"]] = category_counts.get(r["category"], 0) + 1
        if source_id and r["source_id"] != source_id:
            continue
        if category and r["category"] != category:
            continue
        if display_type and r["display_type"] != display_type:
            continue
        hay = " ".join(
            (r["item_id"], r["keywords"], r["label"], r["category"], category_zh(r["category"]))
        ).lower()
        if tokens and not all(_token_hits(t, hay, glossary) for t in tokens):
            continue
        out.append(
            {
                "source_id": r["source_id"],
                "item_id": r["item_id"],
                "category": r["category"],
                "category_zh": category_zh(r["category"]),
                "label": r["label"],
                "keywords": r["keywords"],
                "display_type": r["display_type"],
                "prompt_text": r["prompt_text"] or "",
                "thumb_url": f"/costumes/image/{r['source_id']}/{r['item_id']}/thumb",
                "image_url": f"/costumes/image/{r['source_id']}/{r['item_id']}/full",
            }
        )
    # 目录按站点固定次序（CATEGORY_ORDER）排；未收录原文退居其后按字符串排（自建条目如此）
    categories: list[dict] = []
    for cat in CATEGORY_ORDER:
        if cat in category_counts:
            categories.append({"id": cat, "zh": category_zh(cat), "count": category_counts[cat]})
    for cat, n in sorted(category_counts.items()):
        if cat not in CATEGORY_ORDER:
            categories.append({"id": cat, "zh": category_zh(cat), "count": n})
    return {
        "source": SOURCE_ID,
        "sources": [{"id": sid, "count": n} for sid, n in sources.items()],
        "categories": categories,
        "glossary": glossary,
        "items": out,
    }


# ---------- 条目解析与文件定位（DB 行 → 磁盘；浏览器永远不给路径） ----------

def resolve_ref(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """条目引用 → costume_items 行；支持 ``source/item`` 或裸 ``item``（唯一时）。

    裸 id 先查站点库（costume-db-01），不唯一（跨库重名）时必须写全 source 前缀。
    """
    spec = str(ref or "").strip()
    if not spec:
        raise CostumeError("缺少服装条目 id（形如 SPR-0001 或 costume-db-01/SPR-0001）")
    if "/" in spec:
        source_id, _, item_id = spec.partition("/")
        source_id, item_id = source_id.strip(), item_id.strip()
        if not source_id or not ITEM_ID_RE.fullmatch(item_id):
            raise CostumeError(f"非法服装引用：{spec[:60]}")
    else:
        item_id = spec
        source_id = ""
        if not ITEM_ID_RE.fullmatch(item_id):
            raise CostumeError(f"非法服装引用：{spec[:60]}")
        hits = conn.execute(
            "SELECT source_id FROM costume_items WHERE item_id = ?",
            (item_id,),
        ).fetchall()
        if not hits:
            raise CostumeError(f"服装条目不存在（先 charvar costume import）：{item_id}")
        if len(hits) > 1:
            raise CostumeError(f"item_id 跨库不唯一，请写全 source/item：{item_id}")
        source_id = hits[0]["source_id"]
    row = conn.execute(
        "SELECT ci.*, cp.prompt_text AS prompt_text"
        " FROM costume_items AS ci LEFT JOIN costume_prompts AS cp"
        " ON cp.source_id = ci.source_id AND cp.item_id = ci.item_id"
        " WHERE ci.source_id = ? AND ci.item_id = ?",
        (source_id, item_id),
    ).fetchone()
    if row is None:
        raise CostumeError(f"服装条目不存在（先 charvar costume import）：{spec}")
    return row


def item_row(conn: sqlite3.Connection, ref: str) -> dict:
    """条目引用 → dict 行（keywords / display_type 等 accessor 共用）。"""
    return dict(resolve_ref(conn, ref))


def item_keywords(conn: sqlite3.Connection, ref: str) -> str:
    """条目的关键词（生图「服装要点」注记的原料），文本层有 prompt_text 时它优先 ——
    见 note_text；未知条目报 CostumeError。"""
    return item_row(conn, ref)["keywords"]


def item_display_type(conn: sqlite3.Connection, ref: str) -> str:
    """条目的展示形态（ghost / flat_lay，站点缺省 ghost）；未知条目报 CostumeError。"""
    return item_row(conn, ref)["display_type"]


def note_text(conn: sqlite3.Connection, ref: str) -> str:
    """生图「服装要点」注记的取材：prompt_text（用户文本层）优先，缺省回退 keywords。"""
    row = item_row(conn, ref)
    return str(row.get("prompt_text") or "").strip() or str(row["keywords"] or "").strip()


def image_file(ref: str, variant: str, db_path: Path | None = None) -> Path:
    """条目引用 → 缩略图（thumb）/ 详情图（full）路径（文件名由服务端从 DB 行定）。"""
    from charvar import storage  # 延迟导入

    with storage.connect(db_path) as conn:
        row = item_row(conn, ref)
    rel = str(row["thumb_path"] if variant == "thumb" else row["image_path"] or "").strip()
    if not rel:
        suffix = "缩略图" if variant == "thumb" else "详情图"
        raise CostumeError(
            f"这条服装没有{suffix}（纯文本条目或该档位缺文件）：{row['source_id']}/{row['item_id']}"
        )
    path = costume_root(row["source_id"]) / rel
    if not _is_webp(path):
        raise CostumeError(f"图片文件缺失：{rel}（重跑 charvar costume import / 下载脚本）")
    return path


# ---------- 生图参考图（role=costume：只锚服装，不锚身份/姿势） ----------

def raw_reference_file(ref: str, db_path: Path | None = None) -> Path:
    """参考图源文件：无损原寸 png 优先（仅一步有损），缺失回退详情 webp；纯文本条目报错。"""
    from charvar import storage  # 延迟导入

    with storage.connect(db_path) as conn:
        row = item_row(conn, ref)
    png_rel = str(row["file_path"] or "").strip()
    if png_rel:
        png = costume_root(row["source_id"]) / png_rel
        if _is_png(png):
            return png
    return image_file(f"{row['source_id']}/{row['item_id']}", "full", db_path)


def prepared_reference_file(ref: str, db_path: Path | None = None) -> Path:
    """参考图发送用：压缩版（长边 1536 / JPEG q88 + 白底合成），缓存于该 source 目录。

    缓存文件名 = costume_{item_id}.jpg，每个 source 各自 _ref_cache/ 目录，互不串；
    与主体图 / 姿势库同一套 mtime+size 失效判定。
    """
    from charvar import reference_images  # 延迟导入避免循环
    from charvar import storage

    with storage.connect(db_path) as conn:
        row = item_row(conn, ref)
    cache_dir = costume_root(row["source_id"])
    return reference_images.prepared_reference(
        raw_reference_file(ref, db_path), cache_dir, f"costume_{row['item_id']}"
    )


# ---------- 用户文本层（costume_prompts：每条一句文生图提示词） ----------

def get_costume_prompt(conn: sqlite3.Connection, source_id: str, item_id: str) -> dict:
    """单条文本层行；没写过 = prompt_text 空串（不 404，前端好渲染空态）。"""
    row = conn.execute(
        "SELECT prompt_text, source, updated_at FROM costume_prompts"
        " WHERE source_id = ? AND item_id = ?",
        (str(source_id or ""), str(item_id or "")),
    ).fetchone()
    return {
        "source_id": str(source_id or ""),
        "item_id": str(item_id or ""),
        "prompt_text": row["prompt_text"] if row else "",
        "prompt_source": row["source"] if row else "",
        "updated_at": row["updated_at"] if row else "",
    }


def set_costume_prompt(
    conn: sqlite3.Connection, source_id: str, item_id: str, text: str, source: str = "manual"
) -> dict:
    """写/清（空串 = 删行）一条服装的文生图提示词；条目必须先在索引层（不悬空）。"""
    if item_row(conn, f"{source_id}/{item_id}")["item_id"] != str(item_id or ""):
        raise CostumeError(f"服装条目不存在（先导入索引层）：{source_id}/{item_id}")
    text = str(text or "").strip()
    now = _now()
    if text:
        conn.execute(
            """INSERT INTO costume_prompts (source_id, item_id, prompt_text, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id, item_id) DO UPDATE SET
                 prompt_text=excluded.prompt_text, source=excluded.source, updated_at=excluded.updated_at""",
            (str(source_id), str(item_id), text, str(source or "manual")[:30], now, now),
        )
    else:
        conn.execute(
            "DELETE FROM costume_prompts WHERE source_id = ? AND item_id = ?",
            (str(source_id), str(item_id)),
        )
    conn.commit()
    return get_costume_prompt(conn, source_id, item_id)


# ---------- 自建条目（outfits 迁移 → studio；display_type=flat_lay） ----------

def import_outfit_presets(conn: sqlite3.Connection) -> dict:
    """presets/outfits/*.yaml → 自建服装行（source_id=studio，display_type=flat_lay）。

    item_id = ``{preset.name}_{item.id}``（家族+条目双前缀防跨家族重名）；text 双写
    keywords 与 costume_prompts.prompt_text（source=manual）—— 它本来就是合法的
    文生图服装提示词，正好当新表结构的首批真实数据。纯文本条目（无图，合法状态；
    参考图后补）。已存在的 item **整条跳过**（重跑迁移不许覆盖 DB 里改过的文本；
    家族新增条目重跑即补）。
    """
    from charvar import outfit as outfit_service

    added = 0
    skipped = 0
    presets = outfit_service.list_outfit_presets()
    for entry in presets:
        preset = outfit_service.load_outfit(entry["name"])
        tags = dict(preset.caption_tags or {})
        for it in preset.items:
            item_id = f"{preset.name}_{it.id}"
            if not ITEM_ID_RE.fullmatch(item_id) or not ITEM_ID_RE.fullmatch(it.id):
                raise CostumeError(f"家族 {preset.name} 条目 id 不合法：{it.id[:60]}")
            exists = conn.execute(
                "SELECT 1 FROM costume_items WHERE source_id = ? AND item_id = ?",
                (STUDIO_SOURCE_ID, item_id),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            policy = ""
            if it.subject_policy is not None:
                data = it.subject_policy
                if hasattr(data, "model_dump"):
                    data = data.model_dump()
                policy = json.dumps(data, ensure_ascii=False) if data else ""
            now = _now()
            conn.execute(
                """INSERT INTO costume_items
                   (source_id, item_id, display_type, category, label, keywords,
                    file_path, image_path, thumb_path, subject_policy, caption_tags, created_at, updated_at)
                   VALUES (?, ?, 'flat_lay', ?, ?, ?, '', '', '', ?, ?, ?, ?)""",
                (
                    STUDIO_SOURCE_ID, item_id, preset.name, it.label, it.text,
                    policy, json.dumps(tags, ensure_ascii=False) if tags else "", now, now,
                ),
            )
            conn.execute(
                """INSERT INTO costume_prompts (source_id, item_id, prompt_text, source, created_at, updated_at)
                   VALUES (?, ?, ?, 'manual', ?, ?)""",
                (STUDIO_SOURCE_ID, item_id, it.text, now, now),
            )
            added += 1
    conn.commit()
    return {"added": added, "skipped": skipped}


# ---------- DB → 仓库镜像（仿主体档案 export；DB 是运行时真源） ----------

def export_item(conn: sqlite3.Connection, ref: str, out_dir: Path | None = None) -> Path:
    """单条服装（含文本层）快照回 presets/costumes/<source_id>/<item_id>.yaml。"""
    import yaml

    row = resolve_ref(conn, ref)
    prompt = get_costume_prompt(conn, row["source_id"], row["item_id"])
    doc = {
        "item_id": row["item_id"],
        "source_id": row["source_id"],
        "display_type": row["display_type"],
        "category": row["category"],
        "label": row["label"],
        "keywords": row["keywords"],
        "prompt_text": prompt["prompt_text"],
        "prompt_source": prompt["prompt_source"],
        "subject_policy": json.loads(row["subject_policy"]) if row["subject_policy"] else {},
        "caption_tags": json.loads(row["caption_tags"]) if row["caption_tags"] else {},
        "images": {
            "file_path": row["file_path"],
            "image_path": row["image_path"],
            "thumb_path": row["thumb_path"],
        },
    }
    target = (Path(out_dir) if out_dir else paths.presets_dir() / "costumes") / row["source_id"]
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{row['item_id']}.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
