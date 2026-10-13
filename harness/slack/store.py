"""Conversation state, event deduplication, and reconciled delivery outbox."""

import hashlib
import json
import secrets
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_principals (
 team TEXT NOT NULL, user TEXT NOT NULL, profile_id TEXT NOT NULL,
 PRIMARY KEY(team,user));
CREATE TABLE IF NOT EXISTS slack_conversations (
 id TEXT PRIMARY KEY, team TEXT NOT NULL, channel TEXT NOT NULL, thread TEXT NOT NULL,
 owner TEXT NOT NULL, profile_id TEXT NOT NULL, stage TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 data TEXT NOT NULL, job_id TEXT UNIQUE, message_ts TEXT, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS slack_conversation_thread ON slack_conversations(team,channel,thread,updated);
CREATE TABLE IF NOT EXISTS slack_inbox (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS slack_outbox (
 id TEXT PRIMARY KEY, conversation TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', generation INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL DEFAULT 0, error TEXT);
"""


class StaleDraft(ValueError):
    pass


class SlackStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.executescript(SCHEMA)

    def recover(self):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE slack_inbox SET status='pending' WHERE status='processing'"
            )
            conn.execute(
                "UPDATE slack_outbox SET status='pending' WHERE status='sending'"
            )

    @staticmethod
    def decode(row):
        if not row:
            return None
        item = dict(row)
        for key in ("data", "payload"):
            if key in item:
                item[key] = json.loads(item[key])
        return item

    def profile(self, team, user, name):
        profile_id = "slack-" + uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO slack_principals VALUES (?,?,?)",
                (team, user, profile_id),
            )
            profile_id = conn.execute(
                "SELECT profile_id FROM slack_principals WHERE team=? AND user=?",
                (team, user),
            ).fetchone()[0]
        profile = self.store.get_profile(profile_id)
        if not profile:
            profile = self.store.upsert_profile(profile_id, name, {"origin": "slack"})
            self.store.claim_profile_token(
                profile_id, hashlib.sha256(secrets.token_bytes(48)).hexdigest()
            )
        return profile

    def get(self, ident):
        with self.store.connect() as conn:
            return self.decode(
                conn.execute(
                    "SELECT * FROM slack_conversations WHERE id=?", (ident,)
                ).fetchone()
            )

    def find(self, team, channel, thread, user):
        with self.store.connect() as conn:
            return self.decode(
                conn.execute(
                    "SELECT * FROM slack_conversations WHERE team=? AND channel=? AND thread=? AND owner=? ORDER BY updated DESC LIMIT 1",
                    (team, channel, thread, user),
                ).fetchone()
            )

    def active(self, team, channel, user):
        with self.store.connect() as conn:
            return [
                self.decode(r)
                for r in conn.execute(
                    "SELECT * FROM slack_conversations WHERE team=? AND channel=? AND owner=? AND stage NOT IN ('completed','failed','cancelled') ORDER BY updated DESC",
                    (team, channel, user),
                )
            ]

    def create(self, ident, team, channel, thread, user, profile_id, data):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO slack_conversations(id,team,channel,thread,owner,profile_id,stage,data,updated) VALUES (?,?,?,?,?,?,'collect',?,?)",
                (
                    ident,
                    team,
                    channel,
                    thread,
                    user,
                    profile_id,
                    json.dumps(data),
                    time.time(),
                ),
            )
        return self.get(ident)

    def save(self, conversation, *, stage=None, data=None, job_id=None):
        with self.store.connect() as conn:
            cursor = conn.execute(
                "UPDATE slack_conversations SET stage=?, data=?, job_id=?, version=version+1, updated=? WHERE id=? AND version=?",
                (
                    stage or conversation["stage"],
                    json.dumps(data if data is not None else conversation["data"]),
                    job_id if job_id is not None else conversation["job_id"],
                    time.time(),
                    conversation["id"],
                    conversation["version"],
                ),
            )
        if not cursor.rowcount:
            raise StaleDraft("This draft changed; use the latest message.")
        return self.get(conversation["id"])

    def live(self):
        with self.store.connect() as conn:
            return [
                self.decode(r)
                for r in conn.execute(
                    "SELECT * FROM slack_conversations WHERE stage IN ('launching','running','coaching')"
                )
            ]

    def receive(self, key, payload):
        with self.store.connect() as conn:
            conn.execute("PRAGMA busy_timeout=1000")
            cursor = conn.execute(
                "INSERT OR IGNORE INTO slack_inbox(id,payload) VALUES (?,?)",
                (key, json.dumps(payload)),
            )
        return bool(cursor.rowcount)

    def claim(self, table):
        if table not in ("slack_inbox", "slack_outbox"):
            raise ValueError("invalid queue")
        processing = "processing" if table == "slack_inbox" else "sending"
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT * FROM {table} WHERE status='pending' AND available<=? ORDER BY rowid LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row:
                conn.execute(
                    f"UPDATE {table} SET status=?,attempts=attempts+1 WHERE id=?",
                    (processing, row["id"]),
                )
            conn.execute("COMMIT")
        item = self.decode(row)
        if item:
            item["attempts"] += 1
        return item

    def done(self, table, item):
        with self.store.connect() as conn:
            if table == "slack_outbox":
                conn.execute(
                    "UPDATE slack_outbox SET status='done',error=NULL WHERE id=? AND generation=?",
                    (item["id"], item["generation"]),
                )
            elif table == "slack_inbox":
                conn.execute(
                    "UPDATE slack_inbox SET status='done',error=NULL WHERE id=?",
                    (item["id"],),
                )
            else:
                raise ValueError("invalid queue")

    def retry(self, table, item, error, *, delay=5):
        if table not in ("slack_inbox", "slack_outbox"):
            raise ValueError("invalid queue")
        with self.store.connect() as conn:
            conn.execute(
                f"UPDATE {table} SET status='pending',available=?,error=? WHERE id=?",
                (time.time() + delay, str(error)[:300], item["id"]),
            )

    def put(self, key, conversation, kind, payload):
        with self.store.connect() as conn:
            if kind == "card":
                conn.execute(
                    "INSERT INTO slack_outbox(id,conversation,kind,payload) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status='pending',generation=slack_outbox.generation+1,available=0,error=NULL WHERE slack_outbox.payload != excluded.payload",
                    (key, conversation["id"], kind, json.dumps(payload)),
                )
            else:
                # Terminal delivery may be scheduled again after a restart. Keep
                # completed messages and persisted upload tickets unchanged.
                conn.execute(
                    "INSERT OR IGNORE INTO slack_outbox(id,conversation,kind,payload) VALUES (?,?,?,?)",
                    (key, conversation["id"], kind, json.dumps(payload)),
                )

    def file_state(self, item, **fields):
        item["payload"].update(fields)
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE slack_outbox SET payload=? WHERE id=? AND generation=?",
                (json.dumps(item["payload"]), item["id"], item["generation"]),
            )

    def message_sent(self, conversation, ts):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE slack_conversations SET message_ts=? WHERE id=?",
                (ts, conversation["id"]),
            )
