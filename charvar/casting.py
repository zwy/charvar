"""配方 ``pick`` 步的随机选角层：人脸/单人动作/服饰 三个库各选一个 + 动作描述补齐。

随机一律 ``charvar.wildcard.rng_for`` 子种子（禁全局 random）；rng 由调用方
（recipes pick 步 handler）派生，key 含 recipe_run_id —— **每次运行换一批**
（掷骰子语义），同一运行内 continue 重试仍是同一批（pick 幂等 + 确定性）。

动作描述补齐顺序 A→B（2026-11-03 定稿）：
- A = 另一库同 ``(pose_id, angle)`` 的动作描述**原文复制**写回当前库（pose_actions
  双库分键，文本随双库原行标 source 两码事）；
- B = LLM 看图起稿（``pose_describe.describe_one``，落库 source=ai_draft）。
两库都没有时只走 B；B 失败（无 key / 视觉模型不可用 / 校验不过）→ CastingError，
配方步 failed —— 先修 LLM 配置或到动作库手写，continue 可重试。

候选查询直接走 DB（索引层，重灌零影响）；候选集固定排序（ORDER BY / 注册表
次序），保证同 rng 下选择跨进程可复现。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from charvar import llm, wildcard


class CastingError(ValueError):
    """选库失败（过滤器不合法 / 池被过滤空 / 描述补不起）—— 步骤层转 PresetError。"""


# pick 步过滤器键白名单（目标库 → 允许的键）
FILTER_KEYS: dict[str, frozenset[str]] = {
    "face": frozenset({"sources", "section", "hair"}),
    "pose": frozenset({"sources", "lines", "categories", "angle", "single", "allow_text"}),
    "costume": frozenset({"sources", "categories", "media"}),
}


def _as_list(value: Any, field_name: str) -> list[str]:
    """过滤器里的列表值：str 也收（单值便利写法），其余类型显式报错。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise CastingError(f"过滤器 {field_name} 要是列表或字符串，收到：{value!r}")
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            raise CastingError(f"过滤器 {field_name} 里有空项")
        if text not in out:
            out.append(text)
    return out


def normalize_filters(target: str, filters: dict | None) -> dict:
    """pick 目标的过滤器规范化 + 校验（值错立即报，不静默放行）。"""
    allowed = FILTER_KEYS.get(target)
    if allowed is None:
        raise CastingError(f"未知选库目标：{target}（可选 {'/'.join(FILTER_KEYS)}）")
    value = dict(filters or {})
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise CastingError(
            f"{target} 过滤器有未知键：{'/'.join(unknown)}"
            f"（可选 {'/'.join(sorted(allowed))}）"
        )
    if target == "face":
        from charvar import faces as faces_service

        for source in _as_list(value.get("sources"), "face.sources"):
            if source not in faces_service.FACE_SOURCES:
                raise CastingError(
                    f"没有登记这个人脸库：{source}（known={'/'.join(faces_service.FACE_SOURCES)}）"
                )
        section = str(value.get("section") or "").strip()
        if section and section not in faces_service.SECTION_TYPES:
            raise CastingError(
                f"face.section 只支持 {'/'.join(faces_service.SECTION_TYPES)}，当前 {section!r}"
            )
        value["sources"] = _as_list(value.get("sources"), "face.sources")
        value["hair"] = _as_list(value.get("hair"), "face.hair")
        value["section"] = section
        return value
    if target == "pose":
        from charvar import poses as poses_service

        sources = _as_list(value.get("sources"), "pose.sources")
        for source in sources:
            if source not in poses_service.POSE_SOURCES:
                raise CastingError(
                    f"没有登记这个动作库：{source}（known={'/'.join(poses_service.POSE_SOURCES)}）"
                )
        angle = str(value.get("angle") or "").strip() or poses_service.ANGLE_ORDER[0]
        if angle not in poses_service.ANGLE_ORDER:
            raise CastingError(
                f"pose.angle 不合法：{angle!r}（可选 {'/'.join(poses_service.ANGLE_ORDER)}）"
            )
        single = value.get("single", True)
        if not isinstance(single, bool):
            raise CastingError("pose.single 要是布尔值（缺省 true = 排除 2人絡み 双人）")
        allow_text = value.get("allow_text", False)
        if not isinstance(allow_text, bool):
            raise CastingError("pose.allow_text 要是布尔值（缺省 false = 只选有姿势参考图的动作）")
        value.update(
            sources=sources,
            lines=_as_list(value.get("lines"), "pose.lines"),
            categories=_as_list(value.get("categories"), "pose.categories"),
            angle=angle,
            single=single,
            allow_text=allow_text,
        )
        return value
    from charvar import costumes as costumes_service

    sources = _as_list(value.get("sources"), "costume.sources")
    for source in sources:
        if source not in costumes_service.COSTUME_SOURCES:
            raise CastingError(
                f"没有登记这个服饰库：{source}（known={'/'.join(costumes_service.COSTUME_SOURCES)}）"
            )
    media = str(value.get("media") or "").strip()
    if media and media not in ("real", "anime"):
        raise CastingError("costume.media 只支持 real/anime（空 = 不筛）")
    value.update(
        sources=sources,
        categories=_as_list(value.get("categories"), "costume.categories"),
        media=media,
    )
    return value


def _in_clause(column: str, values: list[str], where: list[str], params: list[str]) -> None:
    where.append(f"{column} IN ({','.join('?' for _ in values)})")
    params.extend(values)


def pick_face(conn: sqlite3.Connection, filters: dict, rng: Any) -> dict:
    """人脸库随机选一个（face_ids 固定排序 → rng 可复现）。"""
    where: list[str] = []
    params: list[str] = []
    if filters["sources"]:
        _in_clause("source", filters["sources"], where, params)
    if filters["section"]:
        where.append("section = ?")
        params.append(filters["section"])
    if filters["hair"]:
        _in_clause("hair", filters["hair"], where, params)
    rows = conn.execute(
        "SELECT source, face_id, section, hair FROM face_items"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY source, face_id",
        params,
    ).fetchall()
    if not rows:
        raise CastingError("人脸池被过滤空了：先确认库已导入（charvar face list），或放宽过滤器")
    row = rng.choice(rows)
    return {
        "source": row["source"],
        "face_id": row["face_id"],
        "section": row["section"],
        "section_zh": _face_section_zh(row["section"]),
        "hair": row["hair"],
        "hair_zh": _face_hair_zh(row["hair"]),
        "thumb_url": f"/faces/image/{row['source']}/{row['face_id']}/thumb",
    }


def pick_pose(conn: sqlite3.Connection, filters: dict, rng: Any) -> dict:
    """动作库随机选一个动作（固定 angle；single=true 默认排除 2人絡み）。

    只选动作（pose_id），视角随 angle 固定 —— 描述级补齐在该视角上做。
    ``allow_text: true`` = 无图文本动作（自建库 studio）也入池 —— 文本条目必须
    已写好该视角的动作描述（组合首帧与脚本全锚在描述上，没描述的条目不可用）；
    缺省 false 维持旧行为（只有姿势参考图的动作）。
    """
    where: list[str] = ["angle = ?"]
    params: list[str] = [filters["angle"]]
    if filters["single"]:
        where.append("line <> ?")
        params.append("2人絡み")
    # 缺省（allow_text=false）：无图纯文本动作（自建文本库）天然排除 —— 组合首帧要
    # 姿势参考图，LLM 起稿也要看图。放开时也只收「描述已写好」的文本条目。
    if filters["allow_text"]:
        where.append(
            "((TRIM(COALESCE(file_path, '')) <> '') OR (display_type = 'text' AND EXISTS ("
            "SELECT 1 FROM pose_actions pa WHERE pa.source_id = pose_images.source"
            " AND pa.pose_id = pose_images.pose_id AND pa.angle = pose_images.angle"
            " AND TRIM(COALESCE(pa.action_text, '')) <> '')))"
        )
    else:
        where.append("(TRIM(COALESCE(file_path, '')) <> '')")
    if filters["sources"]:
        _in_clause("source", filters["sources"], where, params)
    if filters["lines"]:
        _in_clause("line", filters["lines"], where, params)
    if filters["categories"]:
        _in_clause("category", filters["categories"], where, params)
    rows = conn.execute(
        "SELECT DISTINCT source, pose_id, line, category, title FROM pose_images"
        " WHERE " + " AND ".join(where)
        + " ORDER BY source, pose_id",
        params,
    ).fetchall()
    if not rows:
        raise CastingError("动作池被过滤空了：先确认库已导入，或放宽过滤器（单人系統缺省排除 2人絡み；文本动作要 allow_text 且描述已写）")
    row = rng.choice(rows)
    from charvar import poses as poses_service
    from charvar.pose_translations import POSE_TITLE_ZH

    title = str(row["title"] or "")
    is_text = str((poses_service.POSE_SOURCES.get(row["source"]) or {}).get("display_type") or "line") == "text"
    return {
        "source": row["source"],
        "pose_id": row["pose_id"],
        "angle": filters["angle"],
        "title": title,
        "title_zh": POSE_TITLE_ZH.get(row["pose_id"], "").strip() or title,
        "line": row["line"],
        "category": row["category"],
        "category_zh": poses_service.category_zh(row["category"]),
        "display_type": str((poses_service.POSE_SOURCES.get(row["source"]) or {}).get("display_type") or "line"),
        # 缩略 URL 与 list_poses 同式 —— 角度原文直接拼，浏览器自动编码（服务端从 DB 行定文件）；
        # 文本动作无图 → 空串（前端渲染「无图 · 纯文本」占位，同 list API 语义）
        "thumb_url": "" if is_text else f"/poses/image/{row['source']}/{row['pose_id']}/{filters['angle']}/thumb",
        "action_text": "",
        "action_origin": "",
    }


def pick_costume(conn: sqlite3.Connection, filters: dict, rng: Any) -> dict:
    """服饰库随机选一条；纯文本（无图）条目天然排除 —— 图锚定换装用不了。"""
    where: list[str] = ["(TRIM(COALESCE(file_path, '')) <> '' OR TRIM(COALESCE(image_path, '')) <> '')"]
    params: list[str] = []
    if filters["sources"]:
        _in_clause("source_id", filters["sources"], where, params)
    if filters["categories"]:
        _in_clause("category", filters["categories"], where, params)
    if filters["media"]:
        where.append("media_type = ?")
        params.append(filters["media"])
    from charvar import costumes as costumes_service

    rows = conn.execute(
        "SELECT source_id, item_id, label, category, display_type, media_type, keywords"
        " FROM costume_items WHERE " + " AND ".join(where),
        params,
    ).fetchall()
    if not rows:
        raise CastingError(
            "服饰池被过滤空了：先确认库已导入（charvar costume import），或放宽过滤器"
        )

    def _source_order(row) -> int:
        return (
            list(costumes_service.COSTUME_SOURCES).index(row["source_id"])
            if row["source_id"] in costumes_service.COSTUME_SOURCES
            else len(costumes_service.COSTUME_SOURCES)
        )

    rows = sorted(rows, key=lambda r: (_source_order(r), r["item_id"]))
    row = rng.choice(rows)
    return {
        "source": row["source_id"],
        "item_id": row["item_id"],
        "label": row["label"] or "",
        "category": row["category"],
        "category_zh": costumes_service.category_zh(row["category"]),
        "display": row["display_type"],
        "media": row["media_type"] or "",
        "keywords": row["keywords"] or "",
        "ref": f"{row['source_id']}/{row['item_id']}",
        "thumb_url": f"/costumes/image/{row['source_id']}/{row['item_id']}/thumb",
    }


def ensure_pose_description(
    conn: sqlite3.Connection,
    *,
    source: str,
    pose_id: str,
    angle: str,
    model: str | None = None,
    config: dict | None = None,
) -> dict:
    """保证该库该动作该视角有动作描述；返回 {"action_text", "origin"}。

    顺序 A→B：A = 另一注册库同组合的描述原文复制写回当前库（origin=twin_copy）；
    B = LLM 看图起稿（pose_describe，source=ai_draft）。两路都拿不到 → CastingError。
    """
    from charvar import poses as poses_service

    current = poses_service.pose_action(conn, pose_id, angle, source=source)
    if str(current["action_text"] or "").strip():
        return {"action_text": current["action_text"], "origin": "existing"}
    for twin in poses_service.POSE_SOURCES:
        if twin == source:
            continue
        try:
            twin_row = poses_service.pose_action(conn, pose_id, angle, source=twin)
        except poses_service.PoseError:
            # 登记了但本机没灌库的库（如自建文本库与站点库 action 集不重叠）—— 跳过不炸
            continue
        twin_text = str(twin_row["action_text"] or "").strip()
        if not twin_text:
            continue
        data = poses_service.set_pose_action(
            conn,
            pose_id,
            angle,
            twin_text,
            source=twin_row["source"] or "manual",
            source_id=source,
        )
        return {
            "action_text": data["action_text"],
            "origin": f"twin_copy({twin}→{source})",
        }
    from charvar import pose_describe

    try:
        result = pose_describe.describe_one(
            conn, pose_id, angle, source=source, model=model, config=config
        )
    except llm.LLMError as exc:
        raise CastingError(
            f"动作描述补不上（两库都没有，LLM 起稿失败：{exc}）"
            "——先在设置页配好 LLM（视觉模型），或 charvar pose action … --set 手写"
        ) from exc
    if result["status"] == "done":
        return {"action_text": result["text"], "origin": "ai_draft"}
    if result["status"] == "skipped":  # 并发/竞态下已被写：当 existing
        return {"action_text": result["text"], "origin": "existing"}
    raise CastingError(
        f"动作描述补不上（两库都没有，LLM 起稿失败）：{result.get('error')}"
        "——先在设置页配好 LLM（视觉模型），或 charvar pose action … --set 手写"
    )


def _face_section_zh(section: str) -> str:
    from charvar import faces as faces_service

    return faces_service.section_zh(section)


def _face_hair_zh(hair: str) -> str:
    from charvar import faces as faces_service

    return faces_service.hair_zh(hair)