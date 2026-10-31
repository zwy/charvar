"""人脸库（第三方/自建，多库）：导入 / 浏览 / 解析参考图来源。

数据真源在磁盘 ``data/faces/<source>/``（catalog.json + img/，由
``scripts/download_face_db.py --source <id>`` 抓取），DB 表 ``face_items``
只是索引 —— 丢失/损坏用 ``charvar face import data/faces/<source>`` 一次重灌。
库登记在 ``FACE_SOURCES``（新库/自建 = 加一行 + 下载脚本 SITES 表加站点；
磁盘目录跟随 source 命名，DB 主键 (source, face_id)）。

图片是 AI 生成的虚构人物（実在しない），每条一张正面参考图。展示形态
（display_type 语义）= 站点 section，随**条目**存（与 pose 的随库不同）：
- ``face``（フェイス）：正面向き参考图 —— 只锚这个人的脸（脸部身份，含发色/
  发型）；服装/姿势/取景不锚。
- ``styling``（スタイリング）：同一人物的整体造型图 —— 锚脸 + 发型 + 成套服装；
  姿势/取景/背景不锚。LLM 锚定分句按它二选一（llm_prompts.face 两个键）。

许可：face-db-01 是 CC0（free/commercial/edit OK，非实存人物）；站方**请求**
「画像データそのものの再配布・転載はご遠慮」（お願い，非禁令）—— 与 pose/
costume 的「作者公告二次发布」前提不同，data/faces/ 不随仓库分发（git 忽略），
详见 data/faces/<source>/README.md 与仓库 README。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from charvar import paths

# 缺省库（CLI 兼容入口与旧调用回退到它）
SOURCE_ID = "face-db-01"

# 库登记表：source_id → 中文标签。都是同一运营（あぎ，x.com/agi_aibusi）的
# 站点；新库/自建库 = 加一行 + download 脚本 SITES 表加站点 + 磁盘目录对齐。
# 自建库条目的 section/hair 词表沿用（FACE/SOURCE 都按条目存，不冲突）。
FACE_SOURCES: dict[str, dict] = {
    "face-db-01": {"zh": "AI 顔データベース"},
    "face-db-02-cn": {"zh": "自建中国成年女性人脸库（image-engine，虚构人物）"},
    "face-db-03-cn": {"zh": "华语女艺人姓名测试脸库（image-engine，合成非实存人物）"},
}

# face_id 是浏览器发来的关键字（拼进查找 URL 与文件定位），只放行安全字符。
FACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 展示形态（display_type 语义，随条目存 = 站点 section）：face = 正面向き、
# 只锚脸；styling = 整体造型（脸+发型+服装）。LLM 锚定分句按键二选一 ——
# 新增形态需要先配分句（llm_prompts），import 按它把守。
SECTION_TYPES: tuple[str, ...] = ("face", "styling")

SECTION_ZH: dict[str, str] = {
    "face": "人脸",
    "styling": "造型",
}

# 发型类别次序（浏览页下拉次序与站点一致；未收录的原文排在收录项之后按字符串排）
HAIR_ORDER: list[str] = ["ショート", "ミディアム", "セミロング", "ロング"]

HAIR_ZH: dict[str, str] = {
    "ショート": "短发",
    "ミディアム": "中发",
    "セミロング": "中长发",
    "ロング": "长发",
}


class FaceError(Exception):
    """人脸库错误（导入校验失败 / 查不到该条 / 文件缺失）。"""


def section_zh(section: str) -> str:
    return SECTION_ZH.get(section, section)


def hair_zh(hair: str) -> str:
    return HAIR_ZH.get(hair, hair)


def source_dir(source: str = SOURCE_ID) -> Path:
    return paths.data_dir() / "faces" / (source or SOURCE_ID)


def catalog_path(source: str = SOURCE_ID) -> Path:
    return source_dir(source) / "catalog.json"


def _source_or_raise(source: str) -> dict:
    """库 id 必须在登记表里（未知库显式报错，不静默当空库）。"""
    reg = FACE_SOURCES.get(str(source or "").strip())
    if reg is None:
        raise FaceError(f"没有登记这个人脸库：{str(source)[:40]}（known={'/'.join(FACE_SOURCES)}）")
    return reg


# ---------- 导入（磁盘 catalog.json → DB 索引） ----------

def import_catalog(conn: sqlite3.Connection, source: str = SOURCE_ID, origin: Path | None = None) -> dict:
    """解析磁盘 catalog.json，逐条写入 face_items（幂等 upsert）。

    source：库 id（FACE_SOURCES 登记）。origin：下载目录覆盖（缺省
    data/faces/<source>/）。校验从紧：id 字符集、section 枚举、图片齐全与
    魔数 —— 任何一条异常整批拒绝（灌一半的索引比空库更难排查）。本表是
    可重灌索引层；收藏在 face_favorites（用户层，重灌不碰），文案未来单独建表。
    返回 {"source", "faces", "catalog"}。
    """
    source = str(source or SOURCE_ID).strip()
    _source_or_raise(source)
    src = Path(origin) if origin else source_dir(source)
    catalog_file = src / "catalog.json"
    if not catalog_file.exists():
        raise FaceError(f"找不到 catalog.json：{catalog_file}（先跑 scripts/download_face_db.py）")
    try:
        items = json.loads(catalog_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FaceError(f"catalog.json 读取失败：{exc}") from exc
    if not isinstance(items, list) or not items:
        raise FaceError("catalog.json 为空或不是数组")

    bad_ids: list[str] = []
    bad_sections: set[str] = set()
    rows: list[tuple] = []
    for seq, item in enumerate(items):
        face_id = str(item.get("id") or "").strip()
        if not FACE_ID_RE.fullmatch(face_id):
            bad_ids.append(face_id[:60])
            continue
        section = str(item.get("section") or "").strip()
        if section not in SECTION_TYPES:
            bad_sections.add(section[:30] or "(空)")
            continue
        rel_full = str(item.get("full") or "").strip()
        rel_thumb = str(item.get("thumb") or "").strip()
        full_file = src / rel_full
        thumb_file = src / rel_thumb
        if not _is_png(full_file) or not _is_webp(thumb_file):
            raise FaceError(
                f"图片缺失或魔数不符：{rel_full} / {rel_thumb}（先补跑 scripts/download_face_db.py）"
            )
        if Path(rel_full).stem != face_id or Path(rel_thumb).stem != face_id:
            raise FaceError(f"文件名与 id 不一致：{rel_full} / {rel_thumb}")
        rows.append(
            (
                source,
                face_id,
                section,
                str(item.get("hair") or ""),
                str(item.get("style") or ""),
                int(item.get("w") or 0),
                int(item.get("h") or 0),
                int(item.get("bytes") or 0),
                rel_full,
                rel_thumb,
                seq,
            )
        )

    if bad_ids:
        raise FaceError(f"发现 {len(bad_ids)} 条 id 不合法（如 {bad_ids[0]}…），整批拒绝")
    if bad_sections:
        raise FaceError(f"发现未知 section：{' / '.join(sorted(bad_sections))}，整批拒绝")

    from charvar import storage  # 延迟导入（utcnow），poses 同理由

    now = storage.utcnow()
    conn.executemany(
        """INSERT INTO face_items
           (source, face_id, section, hair, style, width, height, bytes,
            file_path, thumb_path, sort_seq, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, face_id) DO UPDATE SET
             section=excluded.section, hair=excluded.hair, style=excluded.style,
             width=excluded.width, height=excluded.height, bytes=excluded.bytes,
             file_path=excluded.file_path, thumb_path=excluded.thumb_path,
             sort_seq=excluded.sort_seq, updated_at=excluded.updated_at""",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], now) for r in rows],
    )
    conn.commit()
    return {"source": source, "faces": len(rows), "catalog": str(catalog_file)}


def _is_png(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _is_webp(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


# ---------- 浏览 ----------

def list_faces(
    conn: sqlite3.Connection,
    *,
    source: str = "",
    section: str = "",
    hair: str = "",
    q: str = "",
) -> dict:
    """人脸清单 + 库/形态/发型元数据，人脸库浏览页一次拉全。

    q 对 style/hair/face_id 大小写不敏感（LIKE）。items 按 站点 catalog 数组
    次序排（sort_seq）—— 与站点分组展示一致。section/hair 的页签过滤在
    客户端做（列表一次拉全，1499 条不大）。
    """
    n_rows = conn.execute("SELECT COUNT(*) FROM face_items").fetchone()[0]
    if not n_rows:
        raise FaceError("人脸库还是空的 —— 先跑 charvar face import")
    where: list[str] = []
    params: list[str] = []
    if source:
        where.append("fi.source = ?")
        params.append(source)
    if section:
        where.append("fi.section = ?")
        params.append(section)
    if hair:
        where.append("fi.hair = ?")
        params.append(hair)
    if q:
        where.append("(fi.style LIKE ? OR fi.hair LIKE ? OR fi.face_id LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    rows = conn.execute(
        "SELECT fi.source, fi.face_id, fi.section, fi.hair, fi.style, fi.width, fi.height,"
        " fi.bytes, fi.thumb_path, fi.updated_at, fk.face_id AS fav_face"
        " FROM face_items AS fi"
        " LEFT JOIN face_favorites AS fk ON fk.source_id = fi.source AND fk.face_id = fi.face_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY CASE fi.source "
        + " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(FACE_SOURCES))
        + " ELSE 99 END, fi.sort_seq, fi.face_id",
        params,
    ).fetchall()
    items = [
        {
            "source": r["source"],
            "face_id": r["face_id"],
            "section": r["section"],
            "section_zh": section_zh(r["section"]),
            "hair": r["hair"],
            "hair_zh": hair_zh(r["hair"]),
            "style": r["style"],
            "width": r["width"],
            "height": r["height"],
            # URL 带 updated_at 当版本参 —— 重灌后浏览器缓存自然失效
            "thumb_url": f"/faces/image/{r['source']}/{r['face_id']}/thumb?v={r['updated_at']}",
            "full_url": f"/faces/image/{r['source']}/{r['face_id']}/full?v={r['updated_at']}",
            # 收藏态（face_favorites 用户层；face_items 重灌绝不碰）—— ♥ 筛选的数据源
            "favorited": r["fav_face"] is not None,
        }
        for r in rows
    ]

    # 元数据按库分组嵌套给出（页签计数随所选库联动；zh 映射服务端做）
    sections: dict[str, list] = {}
    for r in conn.execute(
        "SELECT source, section, COUNT(*) AS n FROM face_items GROUP BY source, section"
    ):
        sections.setdefault(r["source"], []).append(
            {"id": r["section"], "zh": section_zh(r["section"]), "count": r["n"]}
        )
    for lst in sections.values():
        lst.sort(
            key=lambda l: (SECTION_TYPES.index(l["id"]) if l["id"] in SECTION_TYPES else 99, l["id"])
        )
    hairs: dict[str, list] = {}
    for r in conn.execute(
        "SELECT source, hair, COUNT(*) AS n FROM face_items GROUP BY source, hair"
    ):
        hairs.setdefault(r["source"], []).append(
            {"id": r["hair"], "zh": hair_zh(r["hair"]), "count": r["n"]}
        )
    for lst in hairs.values():
        lst.sort(
            key=lambda l: (HAIR_ORDER.index(l["id"]) if l["id"] in HAIR_ORDER else len(HAIR_ORDER), l["id"])
        )
    sources = [
        {"id": s["source"], "zh": FACE_SOURCES.get(s["source"], {}).get("zh", s["source"]), "count": s["n"]}
        for s in conn.execute("SELECT source, COUNT(*) AS n FROM face_items GROUP BY source")
    ]
    sources.sort(key=lambda s: (list(FACE_SOURCES).index(s["id"]) if s["id"] in FACE_SOURCES else 99, s["id"]))
    return {
        "source": SOURCE_ID,
        "sources": sources,
        "sections": sections,
        "hairs": hairs,
        "items": items,
    }


def resolve_face(conn: sqlite3.Connection, ref: str) -> tuple[str, str]:
    """face 引用 → (source, face_id)；支持 ``source/id`` 或裸 ``id``（含唯一前缀）。

    face id 是 16 位乱码，CLI 手敲困难 —— 裸 id 先精确查（唯一即用），不中再按
    前缀查：唯一命中即用；多命中列出候选（让用户拉长前缀，前缀合法字符集同
    FACE_ID_RE，LIKE 无通配注入面）；零命中提示 ``charvar face list --q``。
    带 ``source/`` 前缀时只在该库内解析。跨库同 id 且没写前缀 → 显式报错要求
    写全（跨库同 id 是登记表允许的状态）。
    """
    spec = str(ref or "").strip()
    if not spec:
        raise FaceError("缺少人脸条目 id（如 f71f980dde3 或 face-db-01/f71f980dde3）")
    source_scope, had_prefix, bare = spec.partition("/")
    if had_prefix:
        source_scope = source_scope.strip()
        if source_scope and source_scope not in FACE_SOURCES:
            raise FaceError(
                f"没有登记这个人脸库：{source_scope[:40]}（known={'/'.join(FACE_SOURCES)}）"
            )
        spec_id = bare.strip()
    else:
        spec_id = spec
        source_scope = ""
    if not FACE_ID_RE.fullmatch(spec_id):
        raise FaceError(f"非法人脸条目 id：{spec_id[:60]}")
    order = (
        " ORDER BY CASE source "
        + " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(FACE_SOURCES))
        + " ELSE 99 END, sort_seq, face_id"
    )
    exact_sql = "SELECT source FROM face_items WHERE face_id = ?" + (
        " AND source = ?" if source_scope else ""
    )
    exact = conn.execute(exact_sql, (spec_id, source_scope) if source_scope else (spec_id,)).fetchall()
    if len(exact) == 1:
        return str(exact[0]["source"]), spec_id
    if len(exact) > 1:
        raise FaceError(
            f"face id 跨库不唯一，请写全 source/id：{spec_id[:60]}（{'/'.join(str(r['source']) for r in exact)}）"
        )
    prefix_sql = "SELECT source, face_id FROM face_items WHERE face_id LIKE ?" + (
        " AND source = ?" if source_scope else ""
    )
    rows = conn.execute(
        prefix_sql + order, (spec_id + "%", source_scope) if source_scope else (spec_id + "%",)
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["source"]), str(rows[0]["face_id"])
    if not rows:
        raise FaceError(
            f"人脸库没有这个条目：{spec[:60]}（先 charvar face list --q {spec_id[:20]} 查）"
        )
    shown = "、".join(str(r["face_id"])[:8] for r in rows[:5])
    raise FaceError(
        f"face 前缀命中 {len(rows)} 条：{shown}{'…' if len(rows) > 5 else ''} —— 给更长前缀"
    )


# ---------- 收藏（face_favorites：用户态；face_items 重灌绝不碰） ----------

def toggle_favorite(
    conn: sqlite3.Connection, face_id: str, *, favorite: bool | None = None, source: str = SOURCE_ID
) -> dict:
    """收藏/取消收藏（favorite=None = 切换；显式 bool = 设定）。独立用户态表，重灌不碰；
    source_id 必须指明是哪个库（库间同 face_id 时收藏互不串）。"""
    face_id = str(face_id or "").strip()
    if not FACE_ID_RE.fullmatch(face_id):
        raise FaceError(f"非法人脸条目 id：{face_id[:60]}")
    _source_or_raise(source)
    if (
        conn.execute(
            "SELECT 1 FROM face_items WHERE source = ? AND face_id = ?", (source, face_id)
        ).fetchone()
        is None
    ):
        raise FaceError(f"人脸库没有这个条目：{source} / {face_id[:60]}")
    exists = conn.execute(
        "SELECT 1 FROM face_favorites WHERE source_id = ? AND face_id = ?",
        (source, face_id),
    ).fetchone()
    state = not bool(exists) if favorite is None else bool(favorite)
    from charvar import storage  # 延迟导入（utcnow），poses 同理由

    if state and not exists:
        conn.execute(
            "INSERT INTO face_favorites (source_id, face_id, created_at) VALUES (?, ?, ?)",
            (source, face_id, storage.utcnow()),
        )
    elif not state and exists:
        conn.execute(
            "DELETE FROM face_favorites WHERE source_id = ? AND face_id = ?",
            (source, face_id),
        )
    conn.commit()
    return {"face_id": face_id, "favorite": state}


# ---------- 条目与文件定位（DB 行 → 磁盘；浏览器永远不给路径） ----------

def item_section(conn: sqlite3.Connection, face_id: str, source: str = SOURCE_ID) -> str:
    """条目的展示形态（section：face / styling）—— LLM 锚定分句按键二选一。

    独立小函数：_reference_paths_from_specs 只给回路径，/generate 的
    face_types（下标 → section）另查这里 —— 单条读，连接由调用方传。
    """
    face_id = str(face_id or "").strip()
    if not FACE_ID_RE.fullmatch(face_id):
        raise FaceError(f"非法人脸条目 id：{face_id[:60]}")
    _source_or_raise(source)
    row = conn.execute(
        "SELECT section FROM face_items WHERE source = ? AND face_id = ?", (source, face_id)
    ).fetchone()
    if row is None:
        raise FaceError(f"人脸库没有这个条目（先 charvar face import）：{source} / {face_id[:60]}")
    return str(row["section"])


def _locate(face_id: str, db_path=None, source: str = SOURCE_ID) -> tuple[Path, Path]:
    """(库, face_id) → (原寸图路径, 缩略图路径)；文件名永远由服务端从 DB 行定。"""
    face_id = str(face_id or "").strip()
    if not FACE_ID_RE.fullmatch(face_id):
        raise FaceError(f"非法人脸条目 id：{face_id[:60]}")
    _source_or_raise(source)
    from charvar import storage  # 延迟导入，避免 storage 侧的初始化顺序问题

    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path, thumb_path FROM face_items WHERE source = ? AND face_id = ?",
            (source, face_id),
        ).fetchone()
    if row is None:
        raise FaceError(f"人脸库没有这个条目：{source} / {face_id[:60]}")
    full = source_dir(source) / str(row["file_path"])
    thumb = source_dir(source) / str(row["thumb_path"])
    if not _is_png(full) or not _is_webp(thumb):
        raise FaceError(f"图片文件缺失：{row['file_path']} / {row['thumb_path']}（重跑 scripts/download_face_db.py）")
    return full, thumb


def full_image_file(face_id: str, db_path=None, source: str = SOURCE_ID) -> Path:
    """原寸 PNG 路径（网页取图端点用）。"""
    return _locate(face_id, db_path, source)[0]


def thumb_image_file(face_id: str, db_path=None, source: str = SOURCE_ID) -> Path:
    """缩略图 webp 路径（网页取图端点用）。"""
    return _locate(face_id, db_path, source)[1]


# ---------- 参考图预处理（复用 reference_images 的统一规格） ----------

def prepared_reference_file(face_id: str, db_path=None, source: str = SOURCE_ID) -> Path:
    """参考图发送用：压缩版（长边 1536 / JPEG q88），缓存于人脸库目录。

    原寸 face 段 1122×1402 不过 1536 线直通转 JPEG；styling 段 1597×2000
    收到长边 1536。缓存键 face_{face_id}，与主体图/姿势库同一套
    mtime+size 失效判定。
    """
    from charvar import reference_images  # 延迟导入避免循环

    full, _thumb = _locate(face_id, db_path, source)
    return reference_images.prepared_reference(full, source_dir(source), f"face_{face_id}")