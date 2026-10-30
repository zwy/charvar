"""姿势库（第三方数据，多库）：导入 / 浏览 / 解析参考图来源。

数据真源在磁盘 ``data/poses/<source>/``（catalog.json + img/ + thumbs/，由
``scripts/download_pose_db.py --source <id>`` 抓取），DB 表 ``pose_images``
只是索引 —— 丢失/损坏用 ``charvar pose import data/poses/<source>`` 一次重灌，
重建成本接近零。库登记在 ``POSE_SOURCES``（新库 = 加一行 + 下载脚本 SITES 表
加站点；磁盘目录跟随 source 命名，DB 主键 (source, pose_id, angle)）。

图片是无细节灰人偶（线稿 line / 深度图 depth）：**只锚姿势构图，不锚任何身份
外观**（脸/发型/服装/配色都来自主体档案或其它参考图）—— 参考图解析走新角色
``pose``（LLM 锚定句见 ``llm_prompts.REFERENCE_ROLE_CLAUSES`` 的 pose 分句；
depth 库的分句按 display_type 换深度图版）。

站点许可：作品制作可免费商用（编辑/参考 OK）；二次发布按作者 X 公告执行，
随仓库公开 —— 详见 data/poses/<source>/README.md。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from charvar import paths

# 库登记表：source_id → 展示形态（display_type）。都是同作者的姊妹站、同一页
# 模板（pose-db-02 是线稿库同一 pose 集的深度图版 —— id/词表全同，翻译与
# 系統/分類次序表直接共用；未来异源库的新词表走 zh 回退原文）。磁盘目录 =
# data/poses/<source_id>/；新库 = 库表加一行 + scripts/download_pose_db.py
# 的 SITES 加站点 + 下载目录确认同模板。
SOURCE_ID = "pose-db-01"  # 缺省库（CLI 兼容入口与旧调用回退到它）

POSE_SOURCES: dict[str, dict] = {
    "pose-db-01": {"display_type": "line", "zh": "线稿库"},
    "pose-db-02": {"display_type": "depth", "zh": "深度库"},
}

# pose_id / 视角是浏览器发来的关键字（拼进查找与缓存文件名），只放行安全字符。
POSE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 站点视角枚举（存储原文；一个动作最多 5 视角，个别动作缺 1 张）
ANGLE_ORDER = ["正面", "上から", "横", "斜め", "下から"]

# 站点系統次序（浏览页页签次序与站点一致；未收录的原文排在收录项之后按字符串排）
LINE_ORDER = ["全身1人・セクシー系", "全身1人・標準系", "上半身1人", "2人絡み"]

ANGLE_ZH: dict[str, str] = {
    "正面": "正面",
    "上から": "俯视",
    "横": "侧面",
    "斜め": "斜侧",
    "下から": "仰视",
}

LINE_ZH: dict[str, str] = {
    "全身1人・セクシー系": "全身单人・性感系",
    "全身1人・標準系": "全身单人・标准系",
    "上半身1人": "上半身单人",
    "2人絡み": "双人",
}

CATEGORY_ZH: dict[str, str] = {
    "立ち": "站立",
    "座り": "坐姿",
    "寝そべり": "躺卧",
    "しゃがみ": "蹲姿",
    "前かがみ": "前倾",
    "膝立ち": "跪立",
    "膝立ち・四つん這い": "跪立·四肢跪趴",
    "横座り・女の子座り": "侧坐·女孩坐",
    "のけぞり・反り": "后仰·反弓",
    "ひねり": "扭转",
    "立位": "站位",
    "立位密着": "站位贴身",
    "座位": "坐位",
    "座位密着": "坐位贴身",
    "対面座位": "对坐位",
    "背面座位": "背坐位",
    "側位": "侧入位",
    "正常位": "正常位",
    "後背位": "后背位",
    "騎乗位": "骑乘位",
    "密着": "贴身",
    "顔寄せ系": "面部贴近系",
    "かわいい系": "可爱系",
    "胸寄せ・谷間強調系": "胸前手位·乳沟强调系",
    "セクシー・露骨寄り系": "性感·直露系",
    "特殊": "特殊",
}


def angle_zh(angle: str) -> str:
    return ANGLE_ZH.get(angle, angle)


def line_zh(line: str) -> str:
    return LINE_ZH.get(line, line)


def category_zh(category: str) -> str:
    return CATEGORY_ZH.get(category, category)


def title_zh(title: str, pose_id: str = "") -> str:
    """动作名中文翻译（中日对照展示用）；无译文回退原文。"""
    from charvar.pose_translations import POSE_TITLE_ZH

    mapped = POSE_TITLE_ZH.get(str(pose_id or ""))
    return str(mapped).strip() if mapped and str(mapped).strip() else title


def source_dir(source: str = SOURCE_ID) -> Path:
    return paths.data_dir() / "poses" / (source or SOURCE_ID)


def catalog_path(source: str = SOURCE_ID) -> Path:
    return source_dir(source) / "catalog.json"


def _source_or_raise(source: str) -> dict:
    """库 id 必须在登记表里（未知库显式报错，不静默当空库）。"""
    reg = POSE_SOURCES.get(str(source or "").strip())
    if reg is None:
        raise PoseError(f"没有登记这个姿势库：{str(source)[:40]}（known={'/'.join(POSE_SOURCES)}）")
    return reg


def pose_id_from_item_id(item_id: str) -> str:
    """站点条目 id（``full_sexy_P001_front``）→ 动作 id（``full_sexy_P001``）。

    站点条目 id = `{pose_id}` + `_` + 视角词，视角词不含下划线 —— 尾段即视角。
    """
    return str(item_id).rsplit("_", 1)[0]


class PoseError(Exception):
    """姿势库错误（导入校验失败 / 查不到该动作视角）。"""


# ---------- 导入（磁盘 catalog.json → DB 索引） ----------

def import_catalog(conn: sqlite3.Connection, source: str = SOURCE_ID, origin: Path | None = None) -> dict:
    """解析磁盘 catalog.json，逐条写入 pose_images（幂等 upsert）。

    source：库 id（POSE_SOURCES 登记；display_type 按登记的缺省写入）。
    origin：下载目录覆盖（缺省 data/poses/<source>/）。
    校验从紧：id 字符集、视角枚举、img/thumbs 文件齐全与魔数 —— 任何一条
    异常整批拒绝（DB 可以重灌，灌一半的索引比空库更难排查）。
    返回 {"source", "poses", "images", "catalog"}。
    """
    source = str(source or SOURCE_ID).strip()
    reg = _source_or_raise(source)
    src = Path(origin) if origin else source_dir(source)
    catalog_file = src / "catalog.json"
    if not catalog_file.exists():
        raise PoseError(f"找不到 catalog.json：{catalog_file}（先跑 scripts/download_pose_db.py）")
    try:
        items = json.loads(catalog_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseError(f"catalog.json 读取失败：{exc}") from exc
    if not isinstance(items, list) or not items:
        raise PoseError("catalog.json 为空或不是数组")

    unknown_angles: set[str] = set()
    bad_ids: list[str] = []
    rows: list[tuple] = []
    for item in items:
        item_id = str(item.get("id") or "").strip()
        if not POSE_ID_RE.fullmatch(item_id) or "_" not in item_id:
            bad_ids.append(item_id[:60])
            continue
        angle = str(item.get("アングル") or "").strip()
        if angle not in ANGLE_ORDER:
            unknown_angles.add(angle[:30] or "(空)")
            continue
        rel_full = str(item.get("原寸") or "").strip()
        rel_thumb = str(item.get("サムネ") or "").strip()
        full_file = src / rel_full
        thumb_file = src / rel_thumb
        if not _is_png(full_file) or not _is_jpeg(thumb_file):
            raise PoseError(f"产物缺失或魔数不符：{rel_full} / {rel_thumb}（先补跑下载脚本）")
        pose_id = pose_id_from_item_id(item_id)
        rows.append(
            (
                source,
                pose_id,
                angle,
                str(item.get("系統") or ""),
                str(item.get("ポーズ番号")or ""),
                str(item.get("ポーズ名") or ""),
                str(item.get("分類") or ""),
                rel_full,
                rel_thumb,
                str(reg["display_type"]),
            )
        )

    if bad_ids:
        raise PoseError(f"发现 {len(bad_ids)} 条 id 不合法（如 {bad_ids[0]}…），整批拒绝")
    if unknown_angles:
        raise PoseError(f"发现未知视角：{' / '.join(sorted(unknown_angles))}，整批拒绝")

    conn.executemany(
        """INSERT INTO pose_images
           (source, pose_id, angle, line, number, title, category, file_path, thumb_path, display_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, pose_id, angle) DO UPDATE SET
             line=excluded.line, number=excluded.number, title=excluded.title,
             category=excluded.category, file_path=excluded.file_path,
             thumb_path=excluded.thumb_path, display_type=excluded.display_type""",
        rows,
    )
    conn.commit()
    poses = {r[1] for r in rows}
    return {
        "source": source,
        "poses": len(poses),
        "images": len(rows),
        "catalog": str(catalog_file),
    }


def _is_png(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _is_jpeg(path: Path) -> bool:
    try:
        return path.read_bytes()[:2] == b"\xff\xd8"
    except OSError:
        return False


# ---------- 浏览 ----------

_META_CACHE: dict[str, dict] = {}


def list_poses(
    conn: sqlite3.Connection,
    *,
    line: str = "",
    category: str = "",
    q: str = "",
) -> dict:
    """动作清单（按动作分组）+ 库/系統/分類元数据，姿势库浏览页一次拉全。

    q 对 title/number/pose_id 大小写不敏感；视角在返回里全量给出（来源/系統/
    分類的页签过滤都在客户端做，动作列表不大 —— 每库 288 条，切库不重拉）。
    用户层（描述/收藏）按 pose 的 source 精确挂靠 —— depth 库与线稿库同 id，
    描述与收藏互不串库。
    """
    where: list[str] = []
    params: list[str] = []
    if line:
        where.append("pi.line = ?")
        params.append(line)
    if category:
        where.append("pi.category = ?")
        params.append(category)
    if q:
        where.append("(pi.title LIKE ? OR pi.number LIKE ? OR pi.pose_id LIKE ? OR pa.action_text LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    # 视角排序在 Python 端按 ANGLE_ORDER 固定次序做（SQL 排视角用索引更绕）。
    rows = conn.execute(
        "SELECT pi.source, pi.pose_id, pi.angle, pi.line, pi.number, pi.title, pi.category,"
        " pi.thumb_path, pi.file_path, pi.display_type, pa.action_text,"
        " pf.pose_id AS fav_pose"
        " FROM pose_images AS pi"
        " LEFT JOIN pose_actions AS pa ON pa.source_id = pi.source AND pa.pose_id = pi.pose_id AND pa.angle = pi.angle"
        " LEFT JOIN pose_favorites AS pf ON pf.source_id = pi.source AND pf.pose_id = pi.pose_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY pi.source, pi.line, pi.number, pi.pose_id",
        params,
    ).fetchall()

    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        pose = grouped.setdefault(
            (row["source"], row["pose_id"]),
            {
                "source": row["source"],
                "display_type": "",
                "pose_id": row["pose_id"],
                "line": row["line"],
                "line_zh": line_zh(row["line"]),
                "number": row["number"],
                "title": row["title"],
                # 中日对照展示：翻译来自 pose_translations（自有内容，进 git）；缺项回退原文
                "title_zh": title_zh(row["title"], row["pose_id"]),
                "category": row["category"],
                "category_zh": category_zh(row["category"]),
                "image_count": 0,
                "images": [],
                "favorited": row["fav_pose"] is not None,
            },
        )
        pose["display_type"] = row["display_type"] or pose["display_type"]
        pose["images"].append(
            {
                "angle": row["angle"],
                "angle_zh": angle_zh(row["angle"]),
                "thumb_url": f"/poses/image/{row['source']}/{row['pose_id']}/{row['angle']}/thumb",
                "full_url": f"/poses/image/{row['source']}/{row['pose_id']}/{row['angle']}/full",
                "action_text": row["action_text"] or "",
            }
        )
        pose["image_count"] += 1
    for pose in grouped.values():
        pose["images"].sort(key=lambda im: ANGLE_ORDER.index(im["angle"]) if im["angle"] in ANGLE_ORDER else 99)
    # 动作按 库 → 站点系統次序 → 编号 排（与浏览页页签一致；未收录系統按字符串退居其后）
    poses_list = sorted(
        grouped.values(),
        key=lambda p: (
            list(POSE_SOURCES).index(p["source"]) if p["source"] in POSE_SOURCES else len(POSE_SOURCES),
            LINE_ORDER.index(p["line"]) if p["line"] in LINE_ORDER else len(LINE_ORDER),
            p["number"],
            p["pose_id"],
        ),
    )

    # 元数据按库分组嵌套给出（页签计数随所选库联动；zh 映射服务端做）
    lines: dict[str, list] = {}
    for r in conn.execute(
        "SELECT source, line, COUNT(DISTINCT pose_id) AS n FROM pose_images GROUP BY source, line"
    ):
        lines.setdefault(r["source"], []).append({"id": r["line"], "zh": line_zh(r["line"]), "poses": r["n"]})
    for lst in lines.values():
        lst.sort(
            key=lambda l: (LINE_ORDER.index(l["id"]) if l["id"] in LINE_ORDER else len(LINE_ORDER), l["id"])
        )
    categories: dict[str, list] = {}
    for r in conn.execute(
        "SELECT source, category, COUNT(DISTINCT pose_id) AS n FROM pose_images"
        " GROUP BY source, category ORDER BY COUNT(DISTINCT pose_id) DESC, category"
    ):
        categories.setdefault(r["source"], []).append(
            {"id": r["category"], "zh": category_zh(r["category"]), "poses": r["n"]}
        )
    sources = [
        {
            "id": r["source"],
            "zh": POSE_SOURCES.get(r["source"], {}).get("zh", r["source"]),
            "display_type": POSE_SOURCES.get(r["source"], {}).get("display_type", ""),
            "poses": r["n"],
        }
        for r in conn.execute(
            "SELECT source, COUNT(DISTINCT pose_id) AS n FROM pose_images GROUP BY source"
        )
    ]
    sources.sort(
        key=lambda s: (list(POSE_SOURCES).index(s["id"]) if s["id"] in POSE_SOURCES else len(POSE_SOURCES), s["id"])
    )
    return {
        "source": SOURCE_ID,
        "sources": sources,
        "lines": lines,
        "categories": categories,
        "poses": poses_list,
    }


# ---------- 动作描述（pose_actions：库×动作×视角 键；用户文案，不进可重灌的 pose_images 索引） ----------

def _require_pose_angle(conn: sqlite3.Connection, pose_id: str, angle: str | None, source: str = SOURCE_ID) -> str:
    """(库, 动作, 视角) 必须是库里的真实组合 —— pose_actions 挂靠在 pose_images 上，不悬空。

    描述里「画面右侧的手臂」这类机位事实只对该视角成立，跟参考图必须同视角
    才不打架，所以不给「切视角时回退其它视角」——没写就是没写。angle 缺省正面。
    同 id 多库共存（depth 库复用线稿库的 id），用户文案必须带库 id 分键。
    """
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    _source_or_raise(source)
    angle_key = str(angle or "").strip() or ANGLE_ORDER[0]
    if angle_key not in ANGLE_ORDER:
        raise PoseError(f"未知视角：{angle_key[:30]}")
    row = conn.execute(
        "SELECT 1 FROM pose_images WHERE source = ? AND pose_id = ? AND angle = ?",
        (source, pose_id, angle_key),
    ).fetchone()
    if row is None:
        raise PoseError(f"姿势库没有这个动作视角：{source} / {pose_id} / {angle_key}")
    return angle_key


def pose_action(
    conn: sqlite3.Connection, pose_id: str, angle: str | None = None, source: str = SOURCE_ID
) -> dict:
    """单动作某视角的动作描述；没写过 → action_text 空串（不 404，前端好渲染空态）。"""
    angle_key = _require_pose_angle(conn, pose_id, angle, source)
    row = conn.execute(
        "SELECT pose_id, angle, action_text, source, updated_at FROM pose_actions"
        " WHERE source_id = ? AND pose_id = ? AND angle = ?",
        (source, str(pose_id or "").strip(), angle_key),
    ).fetchone()
    return {
        "pose_id": str(pose_id or "").strip(),
        "angle": angle_key,
        "action_text": row["action_text"] if row else "",
        "source": row["source"] if row else "",
        "updated_at": row["updated_at"] if row else "",
    }


def set_pose_action(
    conn: sqlite3.Connection,
    pose_id: str,
    angle: str | None,
    action_text: str,
    source: str = "manual",
    *,
    source_id: str = SOURCE_ID,
) -> dict:
    """写/清（action_text 空串 = 清掉该视角条目）一个动作某视角的动作描述。

    source_id = 库 id；source = manual/ai_draft（两码事，别混）。
    """
    pose_id = str(pose_id or "").strip()
    angle_key = _require_pose_angle(conn, pose_id, angle, source_id)
    text = str(action_text or "").strip()
    if text:
        from charvar import storage  # 延迟导入，与 _locate 同理由

        now = storage.utcnow()
        conn.execute(
            """INSERT INTO pose_actions (source_id, pose_id, angle, action_text, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id, pose_id, angle) DO UPDATE SET
                 action_text=excluded.action_text, source=excluded.source, updated_at=excluded.updated_at""",
            (source_id, pose_id, angle_key, text, str(source or "manual")[:30], now, now),
        )
    else:
        conn.execute(
            "DELETE FROM pose_actions WHERE source_id = ? AND pose_id = ? AND angle = ?",
            (source_id, pose_id, angle_key),
        )
    conn.commit()
    return pose_action(conn, pose_id, angle_key, source_id)


# ---------- 收藏（pose_favorites：用户态；pose_images 重灌绝不碰） ----------

def toggle_favorite(
    conn: sqlite3.Connection, pose_id: str, *, favorite: bool | None = None, source: str = SOURCE_ID
) -> dict:
    """收藏/取消收藏（favorite=None = 切换；显式 bool = 设定）。独立用户态表，重灌不碰；
    source_id 必须指明是哪个库（depth 库与线稿库同 id，收藏各自独立）。"""
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    _source_or_raise(source)
    if (
        conn.execute(
            "SELECT 1 FROM pose_images WHERE source = ? AND pose_id = ?", (source, pose_id)
        ).fetchone()
        is None
    ):
        raise PoseError(f"姿势库没有这个动作：{source} / {pose_id[:60]}")
    exists = conn.execute(
        "SELECT 1 FROM pose_favorites WHERE source_id = ? AND pose_id = ?",
        (source, pose_id),
    ).fetchone()
    state = not bool(exists) if favorite is None else bool(favorite)
    from charvar import storage  # 延迟导入（utcnow），与 set_pose_action 同理由

    if state and not exists:
        conn.execute(
            "INSERT INTO pose_favorites (source_id, pose_id, created_at) VALUES (?, ?, ?)",
            (source, pose_id, storage.utcnow()),
        )
    elif not state and exists:
        conn.execute(
            "DELETE FROM pose_favorites WHERE source_id = ? AND pose_id = ?",
            (source, pose_id),
        )
    conn.commit()
    return {"pose_id": pose_id, "favorite": state}


# ---------- 参考图来源解析 ----------

def _locate(pose_id: str, angle: str | None, db_path=None, source: str = SOURCE_ID) -> tuple[Path, str]:
    """(库, pose_id, angle) → (原寸图路径, 缓存键)。

    只认 DB 里有记录的组合 —— 文件名永远由服务端从 DB 行定（与主体槽位/
    Playground run 同一原则：浏览器不给路径）。angle 缺省正面；source 必须给
    （同 id 多库共存，depth 库与线稿库靠它分文件）。
    缓存键 = 站点条目 id（取自落盘文件名，纯 ASCII 天然唯一 —— 视角原文是
    CJK，映射成下划线会互相相撞；缓存目录按库分，键不再跨库冲突）。
    db_path：build_app 隔离库时由调用方传入（缺省 = 默认库）。
    """
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    _source_or_raise(source)
    angle_key = str(angle or "").strip() or ANGLE_ORDER[0]
    from charvar import storage  # 延迟导入，避免 storage 侧的初始化顺序问题

    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path, angle FROM pose_images WHERE source = ? AND pose_id = ? AND angle = ?",
            (source, pose_id, angle_key),
        ).fetchone()
    if row is None:
        raise PoseError(f"姿势库没有这个动作视角：{source} / {pose_id} / {angle_key}")
    full = source_dir(source) / row["file_path"]
    if not _is_png(full):
        raise PoseError(f"参考图文件缺失：{row['file_path']}（重跑 scripts/download_pose_db.py）")
    return full, f"pose_{Path(str(row['file_path'])).stem}"


def full_image_file(pose_id: str, angle: str | None, db_path=None, source: str = SOURCE_ID) -> Path:
    """原寸 PNG 路径（网页取图端点用）。"""
    return _locate(pose_id, angle, db_path, source)[0]


def thumb_image_file(pose_id: str, angle: str | None, db_path=None, source: str = SOURCE_ID) -> Path:
    """缩略图 JPEG 路径（网页取图端点用）。"""
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    _source_or_raise(source)
    angle_key = str(angle or "").strip() or ANGLE_ORDER[0]
    from charvar import storage  # 延迟导入，避免 storage 侧的初始化顺序问题

    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT thumb_path, angle FROM pose_images WHERE source = ? AND pose_id = ? AND angle = ?",
            (source, pose_id, angle_key),
        ).fetchone()
    if row is None:
        raise PoseError(f"姿势库没有这个动作视角：{source} / {pose_id} / {angle_key}")
    thumb = source_dir(source) / row["thumb_path"]
    if not _is_jpeg(thumb):
        raise PoseError(f"缩略图文件缺失：{row['thumb_path']}（重跑 scripts/download_pose_db.py）")
    return thumb


# ---------- 参考图预处理（复用 reference_images 的统一规格） ----------

def prepared_reference_file(
    pose_id: str, angle: str | None, db_path=None, source: str = SOURCE_ID
) -> Path:
    """参考图发送用：压缩版（长边 1536 / JPEG q88），缓存于姿势库目录。

    原寸 900×1200 不过 1536 线，通常直通转 JPEG；缓存键用站点条目 id
    （pose_id_view），与主体图的 kind 缓存同一套 mtime+size 失效判定。
    """
    from charvar import reference_images  # 延迟导入避免循环

    full, cache_kind = _locate(pose_id, angle, db_path, source)
    return reference_images.prepared_reference(full, source_dir(source), cache_kind)


def subject_slot_for_prompt(
    conn: sqlite3.Connection, subject_id: str, line: str
) -> tuple[str, dict | None]:
    """姿势出图的主体参考图槽位：上半身动作取 headshot，缺则回退 front。

    半身构图里 pose 线稿只有躯干，front 全身图的脸太小、身份特征弱；
    headshot 档位图脸大、特征全。返回（槽位名, subject_images 行或 None）；
    全身/双人动作恒用 front。
    """
    from charvar import pose_prompt as pose_prompt_module
    from charvar import storage

    label = "headshot" if pose_prompt_module.kind_of_line(line) == "upper" else "front"
    row = storage.get_subject_image(conn, subject_id, label)
    if row is None or not str(row.get("file_path") or "").strip():
        if label != "front":  # 没有半身槽位图就回退 front
            label = "front"
            row = storage.get_subject_image(conn, subject_id, "front")
    return label, row


