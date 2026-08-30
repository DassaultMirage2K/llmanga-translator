"""SQLite setup and connection helpers (stdlib sqlite3)."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import Config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    context TEXT DEFAULT '',
    file_path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    mime_type TEXT,
    file_size INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS translation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    current_image_id INTEGER,
    error_message TEXT,
    settings JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (current_image_id) REFERENCES images(id)
);

CREATE TABLE IF NOT EXISTS translation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL UNIQUE,
    job_id INTEGER NOT NULL,
    result_data JSON,
    context_after TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

-- Global app settings (key -> value strings; extensible for future items)
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_images_work ON images(work_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_jobs_work ON translation_jobs(work_id);
"""


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    keys = row.keys()
    return {k: row[i] for i, k in enumerate(keys)}


class Database:
    """Thin wrapper around a single SQLite connection.

    A single connection is shared across the process; FastAPI runs on one
    event loop thread and blocking DB calls are short, so this is safe enough
    for this deployment. All queries use parameter binding (no string
    interpolation of user input).
    """

    def __init__(self, db_path: str | Path = Config.DATABASE_PATH):
        self.db_path = str(db_path)
        # Ensure parent directory exists before connecting.
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # ---- low-level helpers -------------------------------------------------
    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self.cursor() as cur:
            row = cur.execute(sql, params).fetchone()
            return _row_to_dict(row) if row else None

    def query_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self.cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run a write statement; returns lastrowid."""
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid or 0

    # ---- works -------------------------------------------------------------
    def create_work(self, name: str, description: Optional[str] = None) -> int:
        return self.execute(
            "INSERT INTO works (name, description) VALUES (?, ?)",
            (name, description),
        )

    def get_work(self, work_id: int) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM works WHERE id = ?", (work_id,))

    def list_works(self) -> List[Dict[str, Any]]:
        # cover_image_id = the work's first image (lowest sort_order, then id);
        # NULL when the work has no images yet. Lets the list show a thumbnail
        # without an extra request per work.
        rows = self.query_all(
            """SELECT w.*, COUNT(i.id) AS image_count,
                      (SELECT i2.id FROM images i2 WHERE i2.work_id = w.id
                       ORDER BY i2.sort_order ASC, i2.id ASC LIMIT 1) AS cover_image_id
               FROM works w LEFT JOIN images i ON i.work_id = w.id
               GROUP BY w.id ORDER BY w.updated_at DESC"""
        )
        return rows

    def update_work(self, work_id: int, name: Optional[str], description: Optional[str]) -> None:
        self.execute(
            "UPDATE works SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, description, work_id),
        )

    def delete_work(self, work_id: int) -> None:
        # Cascade handles images/results; jobs are cleaned up by caller.
        self.execute("DELETE FROM works WHERE id = ?", (work_id,))

    # ---- images ------------------------------------------------------------
    def add_image(
        self,
        work_id: int,
        filename: str,
        original_name: str,
        sort_order: int,
        file_path: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> int:
        return self.execute(
            """INSERT INTO images
               (work_id, filename, original_name, sort_order, context, file_path,
                width, height, mime_type, file_size)
               VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?)""",
            (work_id, filename, original_name, sort_order, file_path,
             width, height, mime_type, file_size),
        )

    def get_image(self, image_id: int) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM images WHERE id = ?", (image_id,))

    def list_images(self, work_id: int) -> List[Dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM images WHERE work_id = ? ORDER BY sort_order ASC",
            (work_id,),
        )

    def update_image_context(self, image_id: int, context: str) -> None:
        self.execute("UPDATE images SET context = ? WHERE id = ?", (context, image_id))

    def delete_image(self, image_id: int) -> None:
        self.execute("DELETE FROM images WHERE id = ?", (image_id,))

    def reorder_images(self, work_id: int, ordered_ids: List[int]) -> None:
        for idx, img_id in enumerate(ordered_ids):
            self.execute(
                "UPDATE images SET sort_order = ? WHERE id = ? AND work_id = ?",
                (idx + 1, img_id, work_id),
            )

    def next_sort_order(self, work_id: int) -> int:
        row = self.query_one(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM images WHERE work_id = ?",
            (work_id,),
        )
        return (row["m"] if row else 0) + 1

    # ---- jobs --------------------------------------------------------------
    def create_job(self, work_id: int, settings: Optional[Dict] = None) -> int:
        return self.execute(
            "INSERT INTO translation_jobs (work_id, status, progress, settings) VALUES (?, 'pending', 0, ?)",
            (work_id, json.dumps(settings or {})),
        )

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        row = self.query_one("SELECT * FROM translation_jobs WHERE id = ?", (job_id,))
        if row and row.get("settings"):
            try:
                row["settings"] = json.loads(row["settings"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row

    def update_job_status(
        self, job_id: int, status: str, progress: Optional[int] = None,
        current_image_id: Optional[int] = None, error_message: Optional[str] = None,
    ) -> None:
        sets = ["status = ?"]
        params: list = [status]
        if progress is not None:
            sets.append("progress = ?")
            params.append(progress)
        if current_image_id is not None:
            sets.append("current_image_id = ?")
            params.append(current_image_id)
        if error_message is not None:
            sets.append("error_message = ?")
            params.append(error_message)
        # Timestamp transitions.
        if status == "processing":
            sets.append("started_at = CURRENT_TIMESTAMP")
        elif status in ("completed", "failed"):
            sets.append("completed_at = CURRENT_TIMESTAMP")
        params.append(job_id)
        self.execute(f"UPDATE translation_jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))

    # ---- results -----------------------------------------------------------
    def save_result(self, image_id: int, job_id: int, result_data: Any, context_after: str) -> None:
        # Upsert keyed on image_id (UNIQUE).
        existing = self.query_one("SELECT id FROM translation_results WHERE image_id = ?", (image_id,))
        if existing:
            self.execute(
                """UPDATE translation_results
                   SET job_id = ?, result_data = ?, context_after = ?
                   WHERE image_id = ?""",
                (job_id, json.dumps(result_data), context_after, image_id),
            )
        else:
            self.execute(
                """INSERT INTO translation_results (image_id, job_id, result_data, context_after)
                   VALUES (?, ?, ?, ?)""",
                (image_id, job_id, json.dumps(result_data), context_after),
            )

    def get_result_for_image(self, image_id: int) -> Optional[Dict[str, Any]]:
        row = self.query_one("SELECT * FROM translation_results WHERE image_id = ?", (image_id,))
        if row and row.get("result_data"):
            try:
                row["result_data"] = json.loads(row["result_data"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row

    def get_job_results(self, job_id: int) -> List[Dict[str, Any]]:
        rows = self.query_all("SELECT * FROM translation_results WHERE job_id = ?", (job_id,))
        for r in rows:
            if r.get("result_data"):
                try:
                    r["result_data"] = json.loads(r["result_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    # ---- app settings ------------------------------------------------------
    def get_all_settings(self) -> Dict[str, str]:
        rows = self.query_all("SELECT key, value FROM app_settings")
        return {r["key"]: r["value"] for r in rows}

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
