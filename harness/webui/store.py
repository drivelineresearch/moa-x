"""Small SQLite persistence layer for Web UI profiles, jobs, and events."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    title TEXT NOT NULL,
    workspace TEXT NOT NULL,
    session_dir TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    summary TEXT,
    pid INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    exit_code INTEGER,
    imported INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job_idx ON events(job_id, seq);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    original_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_profile_idx ON uploads(profile_id, created_at DESC);
CREATE TABLE IF NOT EXISTS github_workspaces (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    profile_id TEXT,
    local_path TEXT NOT NULL,
    remote_url TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_checked_at REAL NOT NULL,
    UNIQUE(owner, repo)
);
CREATE INDEX IF NOT EXISTS github_workspaces_checked_idx
ON github_workspaces(last_checked_at DESC);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._local = threading.local()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def reconcile_interrupted_jobs(self) -> list[str]:
        """Mark jobs orphaned by a prior server exit as failed and recoverable."""
        now = time.time()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status IN ('running', 'cancelling')"
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            if job_ids:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status='failed', phase='interrupted', pid=NULL,
                        summary='Web UI restarted while this job was active; inspect retained artifacts or redispatch the run.',
                        finished_at=?, exit_code=-1
                    WHERE status IN ('running', 'cancelling')
                    """,
                    (now,),
                )
        for job_id in job_ids:
            self.append_event(
                job_id,
                "warning",
                "Run marked interrupted after the Web UI restarted",
            )
        return job_ids

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        if "config_json" in item:
            item["config"] = json.loads(item.pop("config_json") or "{}")
        if "settings_json" in item:
            item["settings"] = json.loads(item.pop("settings_json") or "{}")
        if "data_json" in item:
            item["data"] = json.loads(item.pop("data_json") or "{}")
        item.pop("cancel_requested", None)
        return item

    def upsert_profile(
        self, profile_id: str, display_name: str, settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles(id, display_name, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  display_name=excluded.display_name,
                  settings_json=excluded.settings_json,
                  updated_at=excluded.updated_at
                """,
                (profile_id, display_name, json.dumps(settings or {}), now, now),
            )
        return self.get_profile(profile_id) or {}

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(
                conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
            )

    def insert_upload(self, upload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO uploads(
                  id, profile_id, original_name, stored_path, content_type,
                  size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload["id"],
                    upload.get("profile_id"),
                    upload["original_name"],
                    upload["stored_path"],
                    upload.get("content_type"),
                    upload["size_bytes"],
                    upload["sha256"],
                    upload.get("created_at", time.time()),
                ),
            )
        return self.get_upload(upload["id"]) or {}

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(
                conn.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            )

    def list_uploads(
        self, *, profile_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM uploads"
        params: list[Any] = []
        if profile_id:
            query += " WHERE profile_id=?"
            params.append(profile_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [self._decode(row) or {} for row in conn.execute(query, params)]

    def upsert_github_workspace(
        self,
        *,
        owner: str,
        repo: str,
        profile_id: str | None,
        local_path: str,
        remote_url: str,
    ) -> dict[str, Any]:
        workspace_id = f"{owner}/{repo}"
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO github_workspaces(
                  id, owner, repo, profile_id, local_path, remote_url,
                  created_at, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, repo) DO UPDATE SET
                  profile_id=COALESCE(excluded.profile_id, github_workspaces.profile_id),
                  local_path=excluded.local_path,
                  remote_url=excluded.remote_url,
                  last_checked_at=excluded.last_checked_at
                """,
                (
                    workspace_id,
                    owner,
                    repo,
                    profile_id,
                    local_path,
                    remote_url,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM github_workspaces WHERE owner=? AND repo=?",
                (owner, repo),
            ).fetchone()
        return self._decode(row) or {}

    def list_github_workspaces(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                self._decode(row) or {}
                for row in conn.execute(
                    "SELECT * FROM github_workspaces ORDER BY last_checked_at DESC"
                )
            ]

    def insert_job(self, job: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(
                  id, profile_id, title, workspace, session_dir, goal, status,
                  phase, progress, config_json, summary, created_at, imported
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job.get("profile_id"),
                    job["title"],
                    job["workspace"],
                    job["session_dir"],
                    job["goal"],
                    job.get("status", "queued"),
                    job.get("phase", "queued"),
                    job.get("progress", 0),
                    json.dumps(job.get("config", {})),
                    job.get("summary"),
                    job.get("created_at", time.time()),
                    int(bool(job.get("imported"))),
                ),
            )
        return self.get_job(job["id"]) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(
                conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )

    def list_jobs(
        self, *, limit: int = 50, profile_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if profile_id:
            query += " WHERE profile_id=?"
            params.append(profile_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [self._decode(row) or {} for row in conn.execute(query, params)]

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "phase",
            "progress",
            "summary",
            "pid",
            "cancel_requested",
            "started_at",
            "finished_at",
            "exit_code",
            "config_json",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get_job(job_id)
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                [*values.values(), job_id],
            )
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET cancel_requested=1
                WHERE id=? AND status IN ('queued', 'running', 'cancelling')
                """,
                (job_id,),
            )
        return cursor.rowcount > 0

    def cancel_requested(self, job_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return bool(row and row[0])

    def claim_next_job(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status='queued' AND cancel_requested=0
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = time.time()
            conn.execute(
                """
                UPDATE jobs SET status='running', phase='starting',
                  progress=0.02, started_at=? WHERE id=?
                """,
                (now, row["id"]),
            )
            conn.execute("COMMIT")
        return self.get_job(row["id"])

    def append_event(
        self,
        job_id: str,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events(job_id, kind, message, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, kind, message, json.dumps(data or {}), now),
            )
            row = conn.execute(
                "SELECT * FROM events WHERE seq=?", (cursor.lastrowid,)
            ).fetchone()
        return self._decode(row) or {}

    def events_after(
        self, job_id: str, seq: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows: Iterable[sqlite3.Row] = conn.execute(
                """
                SELECT * FROM events WHERE job_id=? AND seq>?
                ORDER BY seq LIMIT ?
                """,
                (job_id, seq, limit),
            )
            return [self._decode(row) or {} for row in rows]
