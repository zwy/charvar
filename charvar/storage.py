"""SQLite 存储：主体档案 + 生成记录 + ComfyUI 提交记录。

- 主体档案：全量 JSON 存储，schema 由 pydantic 校验，升级时兼容；
- runs.run_json：完整 GenerateResult，直接可回放/导出；
- comfy_prompts：ComfyUI /prompt 返回的 prompt_id，供后续关联图片与 caption。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from charvar import paths
from charvar.models import GenerateResult, SubjectProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    preset TEXT NOT NULL,
    seed INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_subject ON runs(subject_id, created_at);
CREATE TABLE IF NOT EXISTS comfy_prompts (
    run_id TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    variant_id TEXT NOT NULL,
    prompt_id TEXT NOT NULL,
    filename_prefix TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    output_files TEXT,
    PRIMARY KEY (run_id, item_index)
);
CREATE TABLE IF NOT EXISTS run_queue_settings (
    run_id TEXT PRIMARY KEY,
    params_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS openrouter_images (
    run_id TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    variant_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    cost REAL,
    generation_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, item_index)
);
CREATE TABLE IF NOT EXISTS subject_images (
    subject_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'empty',
    source TEXT,
    provider TEXT,
    prompt TEXT NOT NULL DEFAULT '',
    file_path TEXT,
    prompt_id TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, kind)
);
CREATE TABLE IF NOT EXISTS subject_templates (
    subject_id TEXT PRIMARY KEY,
    template_md TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pose_images (
    -- 站点 id 去视角后缀（如 full_sexy_P001）；(pose_id, angle) 唯一
    pose_id TEXT NOT NULL,
    angle TEXT NOT NULL,
    line TEXT NOT NULL DEFAULT '',
    number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    -- data/ 相对路径（与 subject_images.file_path 同约定）
    file_path TEXT NOT NULL DEFAULT '',
    thumb_path TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (pose_id, angle)
);
CREATE INDEX IF NOT EXISTS idx_pose_images_line ON pose_images(line, category);
CREATE TABLE IF NOT EXISTS pose_actions (
    -- 用户内容：每动作一条的「动作描述提示词」（动作库页面展示/复制，后续
    -- 编辑与 AI 起草用；source 区分 manual / ai_draft）。独立于 pose_images：
    -- 那是可整批重灌的站点索引（pose import upsert 覆盖），用户文案不进它。
    pose_id TEXT PRIMARY KEY,
    action_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# 已存在的旧库补列（SQLite 无静默 ALTER，逐一尝试即可）
_MIGRATIONS = """
ALTER TABLE comfy_prompts ADD COLUMN output_files TEXT;
ALTER TABLE subject_images ADD COLUMN provider TEXT;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    cur_columns = {row[1] for row in conn.execute("PRAGMA table_info(comfy_prompts)")}
    for statement in filter(None, (s.strip() for s in _MIGRATIONS.split(";"))):
        if "output_files" in statement and "output_files" in cur_columns:
            continue
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass  # 列已存在等幂等迁移
    return conn


# ---------- 主体档案 ----------

def save_subject(conn: sqlite3.Connection, profile: SubjectProfile, *, replace: bool = True) -> None:
    """replace=True 时 upsert（PUT）；False 时仅新建，同 id 会抛 sqlite3.IntegrityError（POST）。"""
    now = utcnow()
    values = (profile.id, profile.name, profile.model_dump_json(), now, now)
    if replace:
        conn.execute(
            """INSERT INTO subjects (id, name, profile_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, profile_json=excluded.profile_json, updated_at=excluded.updated_at""",
            values,
        )
    else:
        conn.execute(
            "INSERT INTO subjects (id, name, profile_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            values,
        )
    conn.commit()


def get_subject(conn: sqlite3.Connection, subject_id: str) -> SubjectProfile | None:
    row = conn.execute("SELECT profile_json FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if row is None:
        return None
    return SubjectProfile.model_validate_json(row["profile_json"])


def list_subjects(conn: sqlite3.Connection) -> list[SubjectProfile]:
    rows = conn.execute("SELECT profile_json FROM subjects ORDER BY id").fetchall()
    return [SubjectProfile.model_validate_json(row["profile_json"]) for row in rows]


def delete_subject(conn: sqlite3.Connection, subject_id: str) -> bool:
    cur = conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------- 生成记录 ----------

def save_run(conn: sqlite3.Connection, result: GenerateResult) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO runs
           (run_id, subject_id, preset, seed, item_count, created_at, result_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            result.run_id,
            result.subject_id,
            result.preset,
            result.seed,
            len(result.items),
            utcnow(),
            result.model_dump_json(),
        ),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> GenerateResult | None:
    row = conn.execute("SELECT result_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return GenerateResult.model_validate_json(row["result_json"])


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT run_id, subject_id, preset, seed, item_count, created_at,
                 (SELECT COUNT(*) FROM comfy_prompts cp WHERE cp.run_id = runs.run_id) AS comfy_count
          FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_run(conn: sqlite3.Connection, run_id: str) -> bool:
    cur = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM comfy_prompts WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM openrouter_images WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM run_queue_settings WHERE run_id = ?", (run_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------- ComfyUI 提交记录 ----------

def save_comfy_submission(
    conn: sqlite3.Connection,
    run_id: str,
    item_index: int,
    variant_id: str,
    prompt_id: str,
    filename_prefix: str,
    output_files: list[dict[str, str]] | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO comfy_prompts
           (run_id, item_index, variant_id, prompt_id, filename_prefix, submitted_at, output_files)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, item_index, variant_id, prompt_id, filename_prefix, utcnow(), json.dumps(output_files or [], ensure_ascii=False)),
    )


def save_comfy_submissions(conn: sqlite3.Connection, run_id: str, submissions: list[dict[str, Any]]) -> None:
    for entry in submissions:
        prompt_id = entry.get("prompt_id")
        if not entry.get("ok") or not prompt_id:
            continue
        save_comfy_submission(
            conn,
            run_id,
            int(entry["index"]),
            str(entry["variant_id"]),
            str(prompt_id),
            str(entry["filename_prefix"]),
            entry.get("files") or [],
        )
    conn.commit()


def list_comfy_submissions(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT item_index, variant_id, prompt_id, filename_prefix, submitted_at, output_files FROM comfy_prompts WHERE run_id = ? ORDER BY item_index",
        (run_id,),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["output_files"] = json.loads(data["output_files"]) if data["output_files"] else []
        result.append(data)
    return result


# ---------- OpenRouter 生图记录 ----------

def save_openrouter_image(
    conn: sqlite3.Connection,
    run_id: str,
    item_index: int,
    variant_id: str,
    *,
    model: str = "",
    status: str = "done",
    cost: float | None = None,
    generation_id: str = "",
    error: str = "",
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO openrouter_images
           (run_id, item_index, variant_id, model, status, cost, generation_id, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, item_index, variant_id, model, status, cost, generation_id, error, utcnow()),
    )


def save_openrouter_images(conn: sqlite3.Connection, run_id: str, entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        # status 优先取 entry 自带的；没带就从 ok 推导 —— 失败条目绝不能落成 "done"
        status = str(entry.get("status") or ("done" if entry.get("ok") else "error"))
        save_openrouter_image(
            conn,
            run_id,
            int(entry["index"]),
            str(entry["variant_id"]),
            model=str(entry.get("model") or ""),
            status=status,
            cost=entry.get("cost"),
            generation_id=str(entry.get("generation_id") or ""),
            error=str(entry.get("error") or ""),
        )
    conn.commit()


def list_openrouter_images(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT item_index, variant_id, model, status, cost, generation_id, error, created_at "
        "FROM openrouter_images WHERE run_id = ? ORDER BY item_index",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------- 入队参数快照（重新生成时原样重放） ----------
def save_queue_settings(conn: sqlite3.Connection, run_id: str, params: dict[str, Any]) -> None:
    """批量入队时快照最终生效参数（请求级值，重放时再走同一预检路径）；单条重新提交不必更新。"""
    conn.execute(
        """INSERT OR REPLACE INTO run_queue_settings (run_id, params_json, updated_at)
           VALUES (?, ?, ?)""",
        (run_id, json.dumps(params, ensure_ascii=False), utcnow()),
    )
    conn.commit()


def get_queue_settings(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT params_json FROM run_queue_settings WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["params_json"])


# ---------- 主体档案图片 ----------

def get_subject_image(conn: sqlite3.Connection, subject_id: str, kind: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM subject_images WHERE subject_id = ? AND kind = ?",
        (subject_id, kind),
    ).fetchone()
    return dict(row) if row is not None else None


def upsert_subject_image(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    """全量 upsert 一行主体图片记录（entry 需含 subject_id/kind 及各字段）。"""
    now = utcnow()
    conn.execute(
        """INSERT INTO subject_images
           (subject_id, kind, status, source, provider, prompt, file_path, prompt_id, error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(subject_id, kind) DO UPDATE SET
             status=excluded.status, source=excluded.source, provider=excluded.provider,
             prompt=excluded.prompt,
             file_path=excluded.file_path, prompt_id=excluded.prompt_id, error=excluded.error,
             updated_at=excluded.updated_at""",
        (
            entry["subject_id"],
            entry["kind"],
            entry.get("status", "empty"),
            entry.get("source"),
            entry.get("provider"),
            entry.get("prompt", ""),
            entry.get("file_path"),
            entry.get("prompt_id"),
            entry.get("error", ""),
            entry.get("created_at") or now,
            now,
        ),
    )
    conn.commit()


def list_subject_images(conn: sqlite3.Connection, subject_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM subject_images WHERE subject_id = ? ORDER BY kind",
        (subject_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_subject_image(conn: sqlite3.Connection, subject_id: str, kind: str) -> bool:
    cur = conn.execute(
        "DELETE FROM subject_images WHERE subject_id = ? AND kind = ?",
        (subject_id, kind),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_subject_images(conn: sqlite3.Connection, subject_id: str) -> None:
    conn.execute("DELETE FROM subject_images WHERE subject_id = ?", (subject_id,))
    conn.commit()


# ---------- 主体通用模板 ----------

def get_subject_template(conn: sqlite3.Connection, subject_id: str) -> str | None:
    row = conn.execute(
        "SELECT template_md FROM subject_templates WHERE subject_id = ?",
        (subject_id,),
    ).fetchone()
    return row["template_md"] if row is not None else None


def save_subject_template(conn: sqlite3.Connection, subject_id: str, template_md: str) -> None:
    conn.execute(
        """INSERT INTO subject_templates (subject_id, template_md, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(subject_id) DO UPDATE SET
             template_md=excluded.template_md, updated_at=excluded.updated_at""",
        (subject_id, template_md, utcnow()),
    )
    conn.commit()


def delete_subject_template(conn: sqlite3.Connection, subject_id: str) -> None:
    conn.execute("DELETE FROM subject_templates WHERE subject_id = ?", (subject_id,))
    conn.commit()


# ---------- 设置 ----------

def get_setting(conn: sqlite3.Connection, key: str) -> Any | None:
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
             value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), utcnow()),
    )
    conn.commit()


def list_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
    return out
