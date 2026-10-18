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
"""

# 已存在的旧库补列（SQLite 无静默 ALTER，逐一尝试即可）
_MIGRATIONS = """
ALTER TABLE comfy_prompts ADD COLUMN output_files TEXT;
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
