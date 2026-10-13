import json
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from harness.slack.bot import Bot, AccessDenied
from harness.slack.delivery import Delivery
from harness.slack.files import Files, FileError, download_private
from harness.webui.app import create_app
from harness.webui.store import Store
from harness.webui.rosters import resolve_roster
from harness.webui.worker import JobWorker
from harness.scripts.config import BUILTIN_PROVIDERS
from harness.scripts.attachments import prepare_attachment_context

TEAM = "T_TEST"
USER = "U_MEMBER"
BOT = "U_BOT"


def catalog():
    out = []
    for name, row in BUILTIN_PROVIDERS.items():
        roles = [] if name == "kimi" else ["proposer", "refiner", "aggregator"]
        levels = {
            "agy": ["low", "medium", "high"],
            "claude": ["low", "medium", "high", "xhigh", "max"],
            "codex": ["low", "medium", "high", "xhigh"],
        }.get(row.harness, [])
        out.append(
            {
                "id": name,
                "model": row.model,
                "harness": row.harness,
                "roles": roles,
                "available": True,
                "effort": row.effort,
                "supports_effort": bool(levels),
                "effort_options": levels,
            }
        )
    return out


class Client:
    def __init__(self):
        self.messages = []
        self.posts = 0

    def users_info(self, user):
        return {"user": {"id": user, "team_id": TEAM, "real_name": "Member"}}

    def chat_postMessage(self, **kwargs):
        self.posts += 1
        msg = {"ts": str(self.posts), **kwargs}
        self.messages.append(msg)
        return msg

    def chat_update(self, **kwargs):
        return kwargs

    def conversations_replies(self, **kwargs):
        return {"messages": self.messages}


class FilesStub:
    def ingest(self, *args, **kwargs):
        return ["upload"]

    def context(self, c):
        return "Supported reference summary."


class SlackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite3")
        self.client = Client()
        self.analyze = Mock(
            return_value={
                "suitability": "needs_clarification",
                "score": 70,
                "summary": "Clarify scope",
                "recommended_depth": "quick",
                "depth_rationale": "A small decision",
                "questions": [
                    {
                        "id": "scope",
                        "prompt": "Which scope?",
                        "why": "Determines effort",
                        "options": [
                            {
                                "label": "Narrow",
                                "description": "Narrow scope",
                                "recommended": True,
                            },
                            {
                                "label": "Broad",
                                "description": "Broad scope",
                                "recommended": False,
                            },
                        ],
                        "allow_custom": True,
                    }
                ],
                "model": {"model": "gpt-6-luna"},
            }
        )
        self.finalize = Mock(
            return_value={
                "optimized_prompt": "Plan the stated task with narrow scope.",
                "changes": [],
                "assumptions": [],
                "remaining_risks": [],
            }
        )
        self.bot = Bot(
            self.store,
            self.root,
            self.client,
            team=TEAM,
            bot_user=BOT,
            token="test",
            base_url="https://example.test/moa-x",
            files=FilesStub(),
            coach_analyze=self.analyze,
            coach_finalize=self.finalize,
            roster_resolver=lambda depth, **kw: resolve_roster(
                depth, catalog=catalog(), **kw
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def message(
        self, ts="1", text="Plan a deployment", user=USER, channel="D_TEST", thread=None
    ):
        event = {
            "type": "message",
            "channel": channel,
            "user": user,
            "ts": ts,
            "text": text,
            "channel_type": "im" if channel.startswith("D") else "channel",
        }
        if thread:
            event["thread_ts"] = thread
        self.bot.enqueue("events_api", {"team_id": TEAM, "event": event})
        item = self.bot.db.claim("slack_inbox")
        if item:
            self.bot.process(item)
        return self.bot.db.find(TEAM, channel, thread or ts, user)

    def preview(self):
        c = self.message()
        self.bot.action(
            USER,
            "moa_answer",
            {"id": c["id"], "v": c["version"], "answer": "Narrow", "question": "scope"},
            "answer:1",
        )
        return self.bot.db.get(c["id"])

    def test_interview_owner_depth_and_real_queue(self):
        c = self.preview()
        self.assertEqual(c["stage"], "preview")
        self.assertEqual(c["data"]["depth"], "quick")
        self.bot.action(USER, "moa_run", {"id": c["id"], "v": c["version"]}, "run:1")
        c = self.bot.db.get(c["id"])
        job = self.store.get_job(c["job_id"])
        self.assertEqual(c["stage"], "running")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["config"]["transport"], "slack")
        self.assertEqual(job["config"]["refiners"], ["deepseek-flash"])
        self.assertEqual(
            job["config"]["options"]["model_overrides"]["agy-gemini-pro"],
            "gemini-4-argon-low",
        )
        self.assertIn("/moa-x/shared/runs/", c["data"]["dashboard"])
        self.assertTrue(self.store.profile_token_claimed(c["profile_id"]))

    def test_duplicate_event_and_mention_make_one_interview(self):
        self.message(channel="C_TEST", text="<@U_BOT> Plan a deployment")
        event = {
            "type": "app_mention",
            "channel": "C_TEST",
            "user": USER,
            "ts": "1",
            "text": "<@U_BOT> Plan a deployment",
        }
        self.assertFalse(
            self.bot.enqueue("events_api", {"team_id": TEAM, "event": event})
        )
        self.assertEqual(self.analyze.call_count, 1)

    def test_other_users_cannot_run_or_cancel(self):
        c = self.preview()
        for action in ["moa_run", "moa_cancel"]:
            with self.assertRaises(AccessDenied):
                self.bot.action(
                    "U_OTHER", action, {"id": c["id"], "v": c["version"]}, "other"
                )
        self.assertEqual(self.store.list_jobs(), [])

    def test_guest_and_foreign_workspace_rejected(self):
        self.client.users_info = lambda user: {
            "user": {"team_id": TEAM, "is_restricted": True}
        }
        with self.assertRaises(AccessDenied):
            self.bot.member("U_GUEST")
        self.assertFalse(
            self.bot.enqueue(
                "events_api", {"team_id": "T_OTHER", "event": {"type": "message"}}
            )
        )

    def test_stale_double_run_creates_one_job(self):
        c = self.preview()
        value = {"id": c["id"], "v": c["version"]}
        self.bot.action(USER, "moa_run", value, "run:1")
        from harness.slack.store import StaleDraft

        with self.assertRaises(StaleDraft):
            self.bot.action(USER, "moa_run", value, "run:2")
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_launch_recovers_existing_job_without_duplicate(self):
        c = self.preview()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        c = self.bot.db.save(c, stage="launching")
        self.bot.launch(c)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_cancel_fast_path_does_not_wait_for_interview_pool(self):
        c = self.preview()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        self.bot.enqueue(
            "interactive",
            {
                "type": "block_actions",
                "team": {"id": TEAM},
                "user": {"id": USER},
                "actions": [
                    {
                        "action_id": "moa_cancel",
                        "action_ts": "7",
                        "value": json.dumps({"id": c["id"], "v": 0}),
                    }
                ],
            },
        )
        self.assertEqual(self.store.get_job(c["job_id"])["status"], "cancelled")
        self.assertIsNone(self.store.claim_next_job())

    def test_two_requests_in_one_dm_need_thread_selection(self):
        first = self.message()
        self.message(ts="2", text="new: Plan something else")
        self.message(ts="3", text="Narrow")
        self.assertEqual(len(self.bot.db.get(first["id"])["data"]["answers"]), 0)

    def test_unrelated_channel_messages_and_bots_are_ignored(self):
        self.message(channel="C_TEST", text="Normal conversation")
        self.assertEqual(self.analyze.call_count, 0)
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM slack_inbox").fetchone()[0], 0
            )
        self.message(ts="2", user=BOT)
        self.assertEqual(self.analyze.call_count, 0)

    def test_depth_effort_changes_freeze_current_models(self):
        c = self.preview()
        self.bot.action(
            USER,
            "moa_depth",
            {"id": c["id"], "v": c["version"], "depth": "thorough"},
            "depth",
        )
        c = self.bot.db.get(c["id"])
        self.assertIn("glm", c["data"]["roster"]["proposers"])
        self.bot.action(
            USER,
            "moa_effort",
            {"id": c["id"], "v": c["version"], "route": "codex-sol", "effort": "high"},
            "effort",
        )
        c = self.bot.db.get(c["id"])
        self.assertEqual(
            c["data"]["roster"]["options"]["effort_overrides"]["codex-sol"], "high"
        )

    def test_unavailable_lane_blocks_run_without_degrading(self):
        rows = catalog()
        next(x for x in rows if x["id"] == "qwen")["available"] = False
        with self.assertRaises(ValueError):
            resolve_roster("balanced", catalog=rows)

    def test_completed_cancel_is_harmless(self):
        c = self.preview()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        self.store.update_job(c["job_id"], status="completed")
        self.bot.cancel(c, "late")
        self.assertEqual(self.store.get_job(c["job_id"])["status"], "completed")

    def test_delivery_reconciles_post_after_lost_response(self):
        c = self.message()
        item = self.bot.db.claim("slack_outbox")
        self.client.messages = [
            {
                "ts": "existing",
                "metadata": {"event_payload": {"delivery_id": item["id"]}},
            }
        ]
        item["attempts"] = 2
        Delivery(self.bot.db, self.client).deliver(item)
        self.assertEqual(self.client.posts, 0)
        self.assertEqual(self.bot.db.get(c["id"])["message_ts"], "existing")

    def test_concurrent_restart_recovery_runs_one_interview(self):
        c = self.message()
        c = self.bot.db.save(c, stage="coaching")
        entered = threading.Event()
        release = threading.Event()
        response = self.finalize.return_value
        self.finalize.side_effect = lambda *a, **k: (
            entered.set(),
            release.wait(2),
            response,
        )[-1]
        self.analyze.return_value["questions"] = []
        d = dict(c["data"])
        d.pop("analysis", None)
        c = self.bot.db.save(c, data=d)
        first = threading.Thread(target=self.bot.coach, args=(c,))
        second = threading.Thread(target=self.bot.coach, args=(c,))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        self.assertEqual(self.finalize.call_count, 1)
        self.assertEqual(self.bot.db.get(c["id"])["stage"], "preview")

    def test_progress_reports_finished_lanes_before_checkpoint(self):
        c = self.preview()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        self.store.update_job(
            c["job_id"], status="running", phase="layer1", progress=0.08
        )
        self.store.append_event(
            c["job_id"], "log", "[orchestrator]   codex-luna proposer: OK (12s)"
        )
        self.bot.monitor()
        c = self.bot.db.get(c["id"])
        self.assertIn("codex-luna proposer: OK", c["data"]["progress"])
        self.bot.monitor()
        self.assertIn(
            "codex-luna proposer: OK", self.bot.db.get(c["id"])["data"]["progress"]
        )

    def test_slack_controls_have_unique_actions_and_route_values(self):
        from harness.slack.cards import card

        c = self.message()
        payload = card(c)
        for block in payload["blocks"]:
            ids = [element["action_id"] for element in block.get("elements", [])]
            self.assertEqual(len(ids), len(set(ids)))
        choice = next(
            b["elements"][0]
            for b in payload["blocks"]
            if b.get("elements")
            and b["elements"][0]["action_id"].startswith("moa_answer")
        )
        self.bot.enqueue(
            "interactive",
            {
                "type": "block_actions",
                "team": {"id": TEAM},
                "user": {"id": USER},
                "actions": [dict(choice, action_ts="44")],
            },
        )
        self.bot.process(self.bot.db.claim("slack_inbox"))
        self.assertEqual(self.bot.db.get(c["id"])["stage"], "preview")

    def test_codex_source_packet_uses_project_receipts_only(self):
        from harness.scripts.adapters.codex import _source_packet
        import subprocess

        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "README.md").write_text("Visible project receipt")
        (repo / ".env").write_text("Hidden credential")
        outside = self.root / "outside.txt"
        outside.write_text("Hidden outside data")
        (repo / "linked.txt").symlink_to(outside)
        (repo / ".moa").mkdir()
        (repo / ".moa/private.txt").write_text("Hidden prior user run")
        packet = _source_packet(repo, "README.md")
        self.assertIn("1: Visible project receipt", packet)
        for hidden in (
            "Hidden credential",
            "Hidden outside data",
            "Hidden prior user run",
        ):
            self.assertNotIn(hidden, packet)

    def test_final_delivery_survives_repeated_scheduling(self):
        c = self.preview()
        db = self.bot.db
        payload = {
            "path": str(self.root / "plan.md"),
            "filename": "plan.md",
            "title": "Plan",
        }
        db.put("file:finish", c, "file", payload)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM slack_outbox WHERE id='file:finish'"
            ).fetchone()
        item = db.decode(row)
        db.file_state(
            item,
            file_id="F_COMPLETE",
            upload_url="https://files.slack.com/upload",
            uploaded=True,
        )
        db.done("slack_outbox", item)
        db.put("file:finish", c, "file", payload)
        with self.store.connect() as conn:
            row = db.decode(
                conn.execute(
                    "SELECT * FROM slack_outbox WHERE id='file:finish'"
                ).fetchone()
            )
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["payload"]["file_id"], "F_COMPLETE")

    def test_file_completion_retry_retains_ticket_and_bytes(self):
        c = self.preview()
        path = self.root / "plan.md"
        path.write_text("Complete plan")
        self.bot.db.put(
            "file:upload",
            c,
            "file",
            {"path": str(path), "filename": "plan.md", "title": "Plan"},
        )
        with self.store.connect() as conn:
            item = self.bot.db.decode(
                conn.execute(
                    "SELECT * FROM slack_outbox WHERE id='file:upload'"
                ).fetchone()
            )
        client = Mock()
        client.files_getUploadURLExternal.return_value = {
            "file_id": "F1",
            "upload_url": "https://files.slack.com/upload",
        }
        client.files_completeUploadExternal.side_effect = [TimeoutError(), {}]
        delivery = Delivery(self.bot.db, client)
        with patch("harness.slack.delivery.urllib.request.urlopen") as upload:
            with self.assertRaises(TimeoutError):
                delivery.file(item, c)
            delivery.file(item, c)
        self.assertEqual(client.files_getUploadURLExternal.call_count, 1)
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(client.files_completeUploadExternal.call_count, 2)

    def test_restart_recovers_inbox_and_outbox(self):
        self.bot.enqueue(
            "events_api",
            {
                "team_id": TEAM,
                "event": {
                    "type": "message",
                    "channel": "D_TEST",
                    "user": USER,
                    "ts": "1",
                    "text": "Plan",
                },
            },
        )
        item = self.bot.db.claim("slack_inbox")
        self.bot.db.recover()
        self.assertEqual(self.bot.db.claim("slack_inbox")["id"], item["id"])

    def test_read_only_share_is_scoped_expiring_and_prefix_safe(self):
        c = self.preview()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        app = create_app(
            {
                "TESTING": True,
                "START_WORKER": False,
                "DATABASE": str(self.store.path),
                "UPLOAD_DIR": str(self.root / "uploads"),
                "BRIEF_WORKSPACE_DIR": str(self.root / "workspaces/brief"),
                "GITHUB_WORKSPACE_DIR": str(self.root / "workspaces/github"),
                "WORKSPACE_ROOTS": [self.root],
            }
        )
        client = app.test_client()
        path = "/shared/runs/" + c["data"]["share_token"]
        result = client.get(path, headers={"X-Forwarded-Prefix": "/moa-x"})
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"/moa-x/shared/runs/", result.data)
        state = client.get(path + "/status").get_json()
        self.assertEqual(state["id"], c["job_id"])
        self.assertNotIn("profile_id", state)
        self.assertNotIn("workspace", state)
        self.assertEqual(
            client.post("/api/jobs/" + c["job_id"] + "/cancel").status_code, 401
        )
        self.assertEqual(
            client.get("/shared/runs/" + secrets.token_urlsafe(32)).status_code, 404
        )
        with self.store.connect() as conn:
            conn.execute("UPDATE run_shares SET expires_at=0")
        self.assertEqual(client.get(path).status_code, 404)

    def test_private_repository_requires_explicit_allowlist(self):
        with patch(
            "harness.slack.bot.subprocess.run",
            return_value=Mock(returncode=0, stdout='{"isPrivate":true}'),
        ):
            with self.assertRaises(ValueError):
                self.bot.check_repository("drivelineresearch/private")

    def test_supported_upload_snapshot_hash_and_cache(self):
        profile = self.store.upsert_profile("upload-user", "Test")
        client = Mock()
        client.files_info.return_value = {
            "file": {
                "name": "reference.txt",
                "size": 5,
                "url_private": "https://files.slack.com/private",
            }
        }
        files = Files(
            self.store, self.root, client, "test", downloader=lambda *a, **k: b"facts"
        )
        ids = files.ingest(profile["id"], ["F1"])
        c = {"id": "draft", "profile_id": profile["id"], "data": {"upload_ids": ids}}
        summary = files.context(c)
        self.assertIn("facts", summary)
        with patch(
            "harness.slack.files.prepare_attachment_context",
            side_effect=AssertionError("should reuse cache"),
        ):
            self.assertEqual(files.context(c), summary)
        self.assertEqual(files.ingest(profile["id"], ["F1"], existing=ids), [])

    def test_unsupported_oversize_and_host_file_rejection(self):
        client = Mock()
        client.files_info.return_value = {"file": {"name": "report.docx"}}
        files = Files(self.store, self.root, client, "test")
        with self.assertRaises(FileError):
            files.ingest("user", ["F"])
        with self.assertRaises(FileError):
            download_private("https://attacker.test/file", "test")

    def test_attachment_cache_invalidates_when_snapshot_changes(self):
        folder = self.root / "cache"
        (folder / "inputs").mkdir(parents=True)
        path = folder / "inputs/source.txt"
        path.write_text("first")
        scout = {
            "uploaded_files": [{"name": "source.txt", "path": "inputs/source.txt"}]
        }
        prepare_attachment_context(scout, folder)
        first = scout["attachment_context"]["signature"]
        path.write_text("second")
        prepare_attachment_context(scout, folder)
        self.assertNotEqual(first, scout["attachment_context"]["signature"])
        self.assertIn("second", scout["attachment_context"]["markdown"])

    def test_cross_process_cancel_terminates_silent_worker(self):
        script = self.root / "silent.py"
        script.write_text('import time\nprint("starting",flush=True)\ntime.sleep(30)\n')
        session = self.root / "session"
        session.mkdir()
        (session / "scout-brief.json").write_text("{}")
        job = self.store.insert_job(
            {
                "id": "silent-job",
                "profile_id": "user",
                "title": "Test",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "test",
                "config": {},
                "status": "running",
            }
        )
        worker = JobWorker(self.store, script)
        done = []
        thread = threading.Thread(
            target=lambda: done.append(worker._run_phase(job, "layer1"))
        )
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (
            self.store.get_job(job["id"]) or {}
        ).get("pid"):
            time.sleep(0.02)
        self.store.request_cancel(job["id"])
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertTrue(done)
        self.assertNotEqual(done[0], 0)


if __name__ == "__main__":
    unittest.main()
