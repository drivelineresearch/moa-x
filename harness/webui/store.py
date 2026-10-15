"""Small SQLite persistence layer for Web UI profiles, jobs, and events."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
HOME_PATH_RE = re.compile(r"(?<![\w.-])/(?:home|Users)/[^/\s]+")


def redact_event_text(value: Any) -> str:
    """Remove operator identity and home-directory details from UI logs."""
    text = str(value or "")
    text = EMAIL_RE.sub("[redacted email]", text)
    return HOME_PATH_RE.sub("~", text)


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    access_token_hash TEXT,
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
CREATE TABLE IF NOT EXISTS run_shares (
    token_hash TEXT PRIMARY KEY, job_id TEXT NOT NULL, profile_id TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS report_shares (
    token_hash TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS report_shares_job_idx
ON report_shares(job_id, revoked_at);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self.initialize()

    def connect(self, *, timeout: float = 30) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=timeout, isolation_level=None, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(profiles)").fetchall()
            }
            if "access_token_hash" not in columns:
                conn.execute(
                    "ALTER TABLE profiles ADD COLUMN access_token_hash TEXT"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS profiles_token_idx "
                "ON profiles(access_token_hash)"
            )

    def reconcile_interrupted_jobs(self) -> list[str]:
        """Resume v2 checkpoints after an owned-worker restart; preserve legacy behavior."""
        now = time.time()
        resumed, interrupted = [], []
        with self.connect() as conn:
            rows = conn.execute("SELECT id, config_json, cancel_requested FROM jobs WHERE status IN ('running', 'cancelling')").fetchall()
            for row in rows:
                config = json.loads(row["config_json"] or "{}")
                options = config.setdefault("options", {})
                count = options.get("restart_count", 0)
                if type(count) is not int or count < 0:
                    count = 2
                session = conn.execute("SELECT session_dir FROM jobs WHERE id=?", (row["id"],)).fetchone()
                try:
                    scout = json.loads((Path(session["session_dir"]) / "scout-brief.json").read_text())
                except (OSError, ValueError):
                    scout = {}
                resumable = scout.get("pipeline_version") == 2 and count < 2 and not row["cancel_requested"]
                if resumable:
                    options.update(resume_checkpoints=True, restart_count=count + 1)
                    conn.execute("UPDATE jobs SET status='queued', phase='queued', pid=NULL, progress=0, finished_at=NULL, exit_code=NULL, config_json=?, summary=? WHERE id=?",
                                 (json.dumps(config), 'Resuming retained lane checkpoints after worker restart.', row["id"]))
                    resumed.append(row["id"])
                else:
                    conn.execute("UPDATE jobs SET status=?, phase=?, pid=NULL, finished_at=?, exit_code=-1, summary=? WHERE id=?",
                                 ('cancelled' if row["cancel_requested"] else 'failed', 'cancelled' if row["cancel_requested"] else 'interrupted', now,
                                  'Web UI restarted; inspect retained artifacts or redispatch the run.', row["id"]))
                    interrupted.append(row["id"])
        for job_id in resumed + interrupted:
            self.append_event(job_id, "warning", "Resuming retained lane checkpoints after restart" if job_id in resumed else "Run marked interrupted after the Web UI restarted")
        return resumed + interrupted

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
        item.pop("access_token_hash", None)
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

    def profile_token_claimed(self, profile_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT access_token_hash FROM profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
        return bool(row and row["access_token_hash"])

    def claim_profile_token(self, profile_id: str, token_hash: str) -> bool:
        """Bind an unclaimed profile to one browser capability token."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE profiles SET access_token_hash=?, updated_at=?
                WHERE id=? AND access_token_hash IS NULL
                """,
                (token_hash, time.time(), profile_id),
            )
        return cursor.rowcount == 1

    def get_profile_by_token_hash(
        self, token_hash: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(
                conn.execute(
                    "SELECT * FROM profiles WHERE access_token_hash=?",
                    (token_hash,),
                ).fetchone()
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

    def list_github_workspaces(
        self, *, profile_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM github_workspaces"
        params: list[Any] = []
        if profile_id:
            query += " WHERE profile_id=?"
            params.append(profile_id)
        query += " ORDER BY last_checked_at DESC"
        with self.connect() as conn:
            return [
                self._decode(row) or {}
                for row in conn.execute(query, params)
            ]

    def create_report_share(
        self, *, job_id: str, profile_id: str, token_hash: str
    ) -> dict[str, Any]:
        """Replace an existing share so a new link also revokes an old one."""
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE report_shares SET revoked_at=?
                WHERE job_id=? AND profile_id=? AND revoked_at IS NULL
                """,
                (now, job_id, profile_id),
            )
            conn.execute(
                """
                INSERT INTO report_shares(token_hash, job_id, profile_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, job_id, profile_id, now),
            )
            row = conn.execute(
                "SELECT * FROM report_shares WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return self._decode(row) or {}

    def revoke_report_shares(self, *, job_id: str, profile_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE report_shares SET revoked_at=?
                WHERE job_id=? AND profile_id=? AND revoked_at IS NULL
                """,
                (time.time(), job_id, profile_id),
            )
        return cursor.rowcount

    def get_active_report_share(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._decode(
                conn.execute(
                    """
                    SELECT * FROM report_shares
                    WHERE token_hash=? AND revoked_at IS NULL
                    """,
                    (token_hash,),
                ).fetchone()
            )

    def create_run_share(self, job_id, profile_id, token_hash, *, lifetime=30*86400):
        job = self.get_job(job_id)
        if not job or job['profile_id'] != profile_id:
            raise ValueError('job does not belong to this principal')
        with self.connect() as conn:
            conn.execute('INSERT INTO run_shares VALUES (?,?,?,?,?)',
                         (token_hash,job_id,profile_id,time.time(),time.time()+lifetime))

    def get_run_share(self, token_hash):
        with self.connect() as conn:
            return self._decode(conn.execute('SELECT * FROM run_shares WHERE token_hash=? AND expires_at>?',
                                             (token_hash,time.time())).fetchone())

    def revoke_run_shares(self, job_id, profile_id):
        with self.connect() as conn:
            conn.execute("DELETE FROM run_shares WHERE job_id=? AND profile_id=?", (job_id, profile_id))

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

    def claim_job_profile(self, job_id: str, profile_id: str) -> bool:
        """Assign an unowned imported job without overriding another owner."""
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET profile_id=? WHERE id=? AND profile_id IS NULL",
                (profile_id, job_id),
            )
        return cursor.rowcount == 1

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
        guard = " AND cancel_requested=0" if values.get("status") in {"completed", "failed"} else ""
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?{guard}",
                [*values.values(), job_id],
            )
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET cancel_requested=1,
                  status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END,
                  phase=CASE WHEN status='queued' THEN 'cancelled' ELSE phase END,
                  finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END
                WHERE id=? AND status IN ('queued', 'running', 'cancelling')
                """,
                (time.time(), job_id),
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

    def queue_summary(self, job: dict[str, Any]) -> dict[str, Any]:
        """Bounded historical range, explicitly not a promise or model estimate."""
        depth = job.get("config", {}).get("options", {}).get("depth")
        with self.connect() as conn:
            ahead = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling') AND created_at<?", (job["created_at"],)).fetchone()[0] if job.get("status") == "queued" else 0
            rows = conn.execute("SELECT started_at,finished_at,config_json FROM jobs WHERE status='completed' AND started_at IS NOT NULL AND finished_at>started_at ORDER BY finished_at DESC LIMIT 40").fetchall()
        durations = []
        for row in rows:
            config = json.loads(row["config_json"] or "{}")
            if depth and config.get("options", {}).get("depth") != depth:
                continue
            current = job.get("config", {})
            if any(config.get(key) != current.get(key) for key in ("proposers", "refiners", "aggregator")):
                continue
            if any(config.get("options", {}).get(key) != current.get("options", {}).get(key)
                   for key in ("model_overrides", "effort_overrides")):
                continue
            durations.append((row["finished_at"] - row["started_at"]) / 60)
        durations.sort()
        interval = [round(durations[len(durations)//4]), round(durations[(len(durations)*3)//4])] if len(durations) >= 3 else None
        return {"runs_ahead": ahead, "historical_minutes": interval, "samples": len(durations)}

    def append_event(
        self,
        job_id: str,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        message = redact_event_text(message)
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
