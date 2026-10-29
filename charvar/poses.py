"""姿势库（第三方数据 pose-db-01）：导入 / 浏览 / 解析参考图来源。

数据真源在磁盘 ``data/poses/pose-db-01/``（catalog.json + img/ + thumbs/，
由 ``scripts/download_pose_db.py`` 抓取），DB 表 ``pose_images`` 只是索引 ——
丢失/损坏用 ``charvar pose import`` 一次重灌，重建成本接近零。

图片是灰色无脸人偶线稿：**只锚姿势与构图，不锚任何身份外观**（脸/发型/服装/
配色都来自主体档案或其它参考图）—— 参考图解析走新角色 ``pose``
（LLM 锚定句见 ``llm_prompts.REFERENCE_ROLE_CLAUSES`` 的 pose 分句）。

站点许可：作品制作可免费商用；**禁止再分发图片数据本身** —— 故目录必须留在
data/（.gitignore 覆盖），绝不进 git。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from charvar import paths

# 库 id（也是磁盘目录名与 DB source 列的取值；同型可扩 v2 库）
SOURCE_ID = "pose-db-01"

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


def source_dir() -> Path:
    return paths.data_dir() / "poses" / SOURCE_ID


def catalog_path() -> Path:
    return source_dir() / "catalog.json"


def pose_id_from_item_id(item_id: str) -> str:
    """站点条目 id（``full_sexy_P001_front``）→ 动作 id（``full_sexy_P001``）。

    站点条目 id = `{pose_id}` + `_` + 视角词，视角词不含下划线 —— 尾段即视角。
    """
    return str(item_id).rsplit("_", 1)[0]


class PoseError(Exception):
    """姿势库错误（导入校验失败 / 查不到该动作视角）。"""


# ---------- 导入（磁盘 catalog.json → DB 索引） ----------

def import_catalog(conn: sqlite3.Connection, source: Path | None = None) -> dict:
    """解析磁盘 catalog.json，逐条写入 pose_images（幂等 upsert）。

    校验从紧：id 字符集、视角枚举、img/thumbs 文件齐全与魔数 —— 任何一条
    异常整批拒绝（DB 可以重灌，灌一半的索引比空库更难排查）。
    返回 {"source", "poses", "images", "skipped", "errors"}。
    """
    src = Path(source) if source else source_dir()
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
                pose_id,
                angle,
                str(item.get("系統") or ""),
                str(item.get("ポーズ番号")or ""),
                str(item.get("ポーズ名") or ""),
                str(item.get("分類") or ""),
                rel_full,
                rel_thumb,
                str(item.get("source") or SOURCE_ID),
            )
        )

    if bad_ids:
        raise PoseError(f"发现 {len(bad_ids)} 条 id 不合法（如 {bad_ids[0]}…），整批拒绝")
    if unknown_angles:
        raise PoseError(f"发现未知视角：{' / '.join(sorted(unknown_angles))}，整批拒绝")

    conn.executemany(
        """INSERT INTO pose_images
           (pose_id, angle, line, number, title, category, file_path, thumb_path, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(pose_id, angle) DO UPDATE SET
             line=excluded.line, number=excluded.number, title=excluded.title,
             category=excluded.category, file_path=excluded.file_path,
             thumb_path=excluded.thumb_path, source=excluded.source""",
        rows,
    )
    conn.commit()
    poses = {r[0] for r in rows}
    return {
        "source": SOURCE_ID,
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
    """动作清单（按动作分组）+ 系統/分類元数据，姿势库浏览页一次拉全。

    line/category 按存储原文过滤；q 对 title/number/pose_id 大小写不敏感。
    视角在返回里全量给出（页签/过滤在客户端做，动作列表不大 —— 288 条）。
    """
    where: list[str] = []
    params: list[str] = []
    if line:
        where.append("line = ?")
        params.append(line)
    if category:
        where.append("category = ?")
        params.append(category)
    if q:
        where.append("(title LIKE ? OR number LIKE ? OR pose_id LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    # 视角排序在 Python 端按 ANGLE_ORDER 固定次序做（SQL 排视角用索引更绕）。
    rows = conn.execute(
        "SELECT pose_id, angle, line, number, title, category, thumb_path, file_path"
        " FROM pose_images"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY line, number, pose_id",
        params,
    ).fetchall()

    grouped: dict[str, dict] = {}
    for row in rows:
        pose = grouped.setdefault(
            row["pose_id"],
            {
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
            },
        )
        pose["images"].append(
            {
                "angle": row["angle"],
                "angle_zh": angle_zh(row["angle"]),
                "thumb_url": f"/poses/image/{SOURCE_ID}/{row['pose_id']}/{row['angle']}/thumb",
                "full_url": f"/poses/image/{SOURCE_ID}/{row['pose_id']}/{row['angle']}/full",
            }
        )
        pose["image_count"] += 1
    for pose in grouped.values():
        pose["images"].sort(key=lambda im: ANGLE_ORDER.index(im["angle"]) if im["angle"] in ANGLE_ORDER else 99)
    # 动作按 站点系統次序 → 编号 排（与浏览页页签一致；未收录系統按字符串退居其后）
    poses_list = sorted(
        grouped.values(),
        key=lambda p: (
            LINE_ORDER.index(p["line"]) if p["line"] in LINE_ORDER else len(LINE_ORDER),
            p["number"],
            p["pose_id"],
        ),
    )

    lines = [
        {"id": r["line"], "zh": line_zh(r["line"]), "poses": r["n"]}
        for r in conn.execute(
            "SELECT line, COUNT(DISTINCT pose_id) AS n FROM pose_images GROUP BY line"
        )
    ]
    lines.sort(
        key=lambda l: (LINE_ORDER.index(l["id"]) if l["id"] in LINE_ORDER else len(LINE_ORDER), l["id"])
    )
    categories = [
        {"id": r["category"], "zh": category_zh(r["category"]), "poses": r["n"]}
        for r in conn.execute(
            "SELECT category, COUNT(DISTINCT pose_id) AS n FROM pose_images GROUP BY category ORDER BY COUNT(DISTINCT pose_id) DESC, category"
        )
    ]
    return {"source": SOURCE_ID, "lines": lines, "categories": categories, "poses": poses_list}


# ---------- 参考图来源解析 ----------

def _locate(pose_id: str, angle: str | None, db_path=None) -> tuple[Path, str]:
    """(pose_id, angle) → (原寸图路径, 缓存键)。

    只认 DB 里有记录的组合 —— 文件名永远由服务端从 DB 行定（与主体槽位/
    Playground run 同一原则：浏览器不给路径）。angle 缺省正面。
    缓存键 = 站点条目 id（取自落盘文件名，纯 ASCII 天然唯一 —— 视角原文是
    CJK，映射成下划线会互相相撞）。
    db_path：build_app 隔离库时由调用方传入（缺省 = 默认库）。
    """
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    angle_key = str(angle or "").strip() or ANGLE_ORDER[0]
    from charvar import storage  # 延迟导入，避免 storage 侧的初始化顺序问题

    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path, angle FROM pose_images WHERE pose_id = ? AND angle = ?",
            (pose_id, angle_key),
        ).fetchone()
    if row is None:
        raise PoseError(f"姿势库没有这个动作视角：{pose_id} / {angle_key}")
    full = source_dir() / row["file_path"]
    if not _is_png(full):
        raise PoseError(f"参考图文件缺失：{row['file_path']}（重跑 scripts/download_pose_db.py）")
    return full, f"pose_{Path(str(row['file_path'])).stem}"


def full_image_file(pose_id: str, angle: str | None, db_path=None) -> Path:
    """原寸 PNG 路径（网页取图端点用）。"""
    return _locate(pose_id, angle, db_path)[0]


def thumb_image_file(pose_id: str, angle: str | None, db_path=None) -> Path:
    """缩略图 JPEG 路径（网页取图端点用）。"""
    pose_id = str(pose_id or "").strip()
    if not POSE_ID_RE.fullmatch(pose_id):
        raise PoseError(f"非法动作 id：{pose_id[:60]}")
    angle_key = str(angle or "").strip() or ANGLE_ORDER[0]
    from charvar import storage  # 延迟导入，避免 storage 侧的初始化顺序问题

    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT thumb_path, angle FROM pose_images WHERE pose_id = ? AND angle = ?",
            (pose_id, angle_key),
        ).fetchone()
    if row is None:
        raise PoseError(f"姿势库没有这个动作视角：{pose_id} / {angle_key}")
    thumb = source_dir() / row["thumb_path"]
    if not _is_jpeg(thumb):
        raise PoseError(f"缩略图文件缺失：{row['thumb_path']}（重跑 scripts/download_pose_db.py）")
    return thumb


# ---------- 参考图预处理（复用 reference_images 的统一规格） ----------

def prepared_reference_file(pose_id: str, angle: str | None, db_path=None) -> Path:
    """参考图发送用：压缩版（长边 1536 / JPEG q88），缓存于姿势库目录。

    原寸 900×1200 不过 1536 线，通常直通转 JPEG；缓存键用站点条目 id
    （pose_id_view），与主体图的 kind 缓存同一套 mtime+size 失效判定。
    """
    from charvar import reference_images  # 延迟导入避免循环

    full, cache_kind = _locate(pose_id, angle, db_path)
    return reference_images.prepared_reference(full, source_dir(), cache_kind)


