"""Threaded interviews and runs, backed by the shared MoA-X queue."""

import copy
import hashlib
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from harness.webui.launch import LaunchService, LaunchError
from harness.webui.prompt_coach import analyze, finalize
from harness.webui.rosters import resolve_roster
from .cards import card, text
from .files import Files, FileError
from .store import SlackStore, StaleDraft

TERMINAL = {"completed", "failed", "cancelled"}


class AccessDenied(ValueError):
    pass


class Bot:
    def __init__(
        self,
        store,
        root,
        client,
        *,
        team,
        bot_user,
        token,
        base_url,
        github_owner="drivelineresearch",
        private_repos=(),
        coach_analyze=analyze,
        coach_finalize=finalize,
        roster_resolver=resolve_roster,
        files=None,
        launcher=None,
    ):
        self.store, self.root, self.client = store, Path(root), client
        self.db = SlackStore(store)
        self.team, self.bot_user, self.base_url = team, bot_user, base_url.rstrip("/")
        self.github_owner, self.private_repos = github_owner, set(private_repos)
        self.analyze, self.finalize, self.resolve = (
            coach_analyze,
            coach_finalize,
            roster_resolver,
        )
        self.files = files or Files(store, root, client, token)
        self.launcher = launcher or LaunchService(
            store,
            upload_dir=self.root / "uploads",
            github_workspace_dir=self.root / "workspaces/github",
            brief_workspace_dir=self.root / "workspaces/brief",
            github_owner=github_owner,
            workspace_roots=[self.root / "workspaces"],
        )
        self.members = {}
        self.locks = {}
        self.lock = threading.Lock()

    def enqueue(self, kind, payload):
        if kind == "events_api":
            event = payload.get("event") or {}
            if payload.get("team_id") != self.team or event.get("type") not in (
                "message",
                "app_mention",
            ):
                return False
            if (
                event.get("bot_id")
                or event.get("user") == self.bot_user
                or event.get("subtype") not in (None, "file_share")
            ):
                return False
            key = (
                "msg:"
                + self.team
                + ":"
                + str(event.get("channel"))
                + ":"
                + str(event.get("ts"))
            )
            inserted = self.db.receive(key, {"kind": "message", "event": event})
            if str(event.get("text", "")).strip().lower() == "cancel":
                c = self.db.find(
                    self.team,
                    event.get("channel"),
                    event.get("thread_ts") or event["ts"],
                    event.get("user"),
                )
                if not c and str(event.get("channel", "")).startswith("D"):
                    active = self.db.active(
                        self.team, event["channel"], event.get("user")
                    )
                    if len(active) == 1:
                        c = active[0]
                if c:
                    self.cancel(c, key)
                    self.db.done("slack_inbox", {"id": key})
            return inserted
        if kind == "interactive" and payload.get("type") == "block_actions":
            if (payload.get("team") or {}).get("id") != self.team:
                return False
            for action in payload.get("actions", []):
                if not str(action.get("action_id", "")).startswith("moa_"):
                    continue
                raw = (action.get("selected_option") or {}).get("value") or action.get(
                    "value", ""
                )
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                user = (payload.get("user") or {}).get("id")
                key = (
                    "act:"
                    + str(user)
                    + ":"
                    + str(action.get("action_ts"))
                    + ":"
                    + action["action_id"]
                )
                action_name = action["action_id"].partition(":")[0]
                self.db.receive(
                    key,
                    {
                        "kind": "action",
                        "user": user,
                        "action": action_name,
                        "value": value,
                    },
                )
                if action_name == "moa_cancel":
                    c = self.db.get(value.get("id"))
                    if c and c["owner"] == user and c["team"] == self.team:
                        self.cancel(c, key)
                        self.db.done("slack_inbox", {"id": key})
            return True
        return False

    def member(self, user):
        if not user or user == self.bot_user:
            raise AccessDenied("Human workspace member required.")
        cached = self.members.get(user)
        if cached and cached[0] > time.time():
            return cached[1]
        info = self.client.users_info(user=user)["user"]
        teams = (info.get("enterprise_user") or {}).get("teams", [])
        if (info.get("team_id") != self.team and self.team not in teams) or any(
            info.get(k)
            for k in (
                "deleted",
                "is_bot",
                "is_restricted",
                "is_ultra_restricted",
                "is_stranger",
            )
        ):
            raise AccessDenied(
                "MoA-X is available to full Driveline workspace members."
            )
        self.members[user] = (time.time() + 300, info)
        return info

    def card(self, c):
        self.db.put("card:" + c["id"], c, "card", card(c))

    def notice(self, c, message, key):
        self.db.put("notice:" + key, c, "post", {"text": message})

    def process(self, item):
        p = item["payload"]
        try:
            if p["kind"] == "message":
                self.message(p["event"], item["id"])
            else:
                self.action(p["user"], p["action"], p["value"], item["id"])
            self.db.done("slack_inbox", item)
        except StaleDraft:
            self.db.done("slack_inbox", item)
        except AccessDenied:
            self.db.done("slack_inbox", item)
        except Exception as error:
            # API exceptions can contain private URLs; persist only a bounded code.
            code = getattr(error, "response", {}).get("error", type(error).__name__)
            self.db.retry(
                "slack_inbox", item, code, delay=min(60, 5 * item["attempts"])
            )
            if item["attempts"] >= 4:
                self.db.done("slack_inbox", item)
                ident = p.get("value", {}).get("id")
                c = self.db.get(ident) if ident else None
                if not c and p.get("kind") == "message":
                    e = p["event"]
                    c = self.db.find(
                        self.team,
                        e.get("channel"),
                        e.get("thread_ts") or e.get("ts"),
                        e.get("user"),
                    )
                    if not c:
                        active = self.db.active(
                            self.team, e.get("channel"), e.get("user")
                        )
                        if len(active) == 1:
                            c = active[0]
                if c:
                    self.notice(
                        c,
                        "That action could not finish. Please retry from the latest card.",
                        item["id"],
                    )

    def conversation_lock(self, ident):
        with self.lock:
            return self.locks.setdefault(ident, threading.RLock())

    def message(self, event, key):
        channel, user = event.get("channel"), event.get("user")
        content = re.sub(
            r"<@" + re.escape(self.bot_user) + r">", "", str(event.get("text") or "")
        ).strip()
        dm = event.get("channel_type") == "im" or str(channel).startswith("D")
        mention = event.get(
            "type"
        ) == "app_mention" or "<@" + self.bot_user + ">" in str(event.get("text") or "")
        thread = event.get("thread_ts") or event["ts"]
        c = self.db.find(self.team, channel, thread, user)
        if (
            not c
            and dm
            and not event.get("thread_ts")
            and not content.lower().startswith("new:")
        ):
            active = self.db.active(self.team, channel, user)
            if len(active) == 1:
                c = active[0]
            elif len(active) > 1:
                c = active[0]
                self.notice(
                    c,
                    "You have several open requests. Reply in the request’s thread, or start a new one with “new: your task”.",
                    key,
                )
                return
        if not dm and not mention and not c:
            return
        info = self.member(user)
        command = content.lower()
        if c and command == "cancel":
            return self.cancel(c, key)
        if c and command == "status":
            self.card(c)
            return
        if c and c["stage"] == "running":
            self.notice(
                c,
                "This run’s inputs are frozen. Start another request with “new: your task” or a new mention.",
                key,
            )
            return
        if not c or c["stage"] in TERMINAL or command.startswith("new:"):
            profile = self.db.profile(
                self.team,
                user,
                (info.get("profile") or {}).get("display_name")
                or info.get("real_name")
                or user,
            )
            ident = uuid.uuid5(uuid.NAMESPACE_URL, key).hex
            if command.startswith("new:"):
                content = content[4:].strip()
            data = {
                "brief": content[:20000],
                "upload_ids": [],
                "answers": [],
                "depth": "balanced",
                "depth_manual": False,
                "effort_overrides": {},
            }
            if c and c["job_id"] and c["owner"] == user:
                prior = self.store.get_job(c["job_id"])
                plan = Path(prior["session_dir"]) / "final-plan.md" if prior else None
                if prior and prior["status"] == "completed" and plan.is_file():
                    data["prior_summary"] = plan.read_text()[:6000]
                    data["parent_job_id"] = prior["id"]
            c = self.db.create(
                ident, self.team, channel, thread, user, profile["id"], data
            )
        with self.conversation_lock(c["id"]):
            c = self.db.get(c["id"])
            d = copy.deepcopy(c["data"])
            if d.get("last_message") == key:
                if c["stage"] == "coaching":
                    self.coach(c)
                else:
                    self.card(c)
                return
            file_ids = [f["id"] for f in event.get("files", []) if f.get("id")]
            try:
                if file_ids:
                    d["upload_ids"] += self.files.ingest(
                        c["profile_id"], file_ids, existing=d.get("upload_ids", [])
                    )
            except FileError as error:
                self.notice(c, str(error), key)
                return
            d["last_message"] = key
            if c["stage"] == "interview" and content and not file_ids:
                c = self.db.save(c, data=d)
                return self.answer(c, content, key)
            if c["stage"] == "preview" and not file_ids:
                if command == "run":
                    return self.run(c)
                if command in ("quick", "balanced", "thorough"):
                    d.update(depth=command, depth_manual=True, effort_overrides={})
                    return self.preview(self.db.save(c, data=d))
                # A typed edit replaces the draft rather than launching a hidden run.
                d["brief"] = content[:20000]
                d.pop("analysis", None)
                d["answers"] = []
            elif c["stage"] == "collect" and content:
                d["brief"] = content[:20000]
            elif file_ids:
                d.pop("analysis", None)
                d["answers"] = []
                if content and c["stage"] not in ("interview", "preview"):
                    d["brief"] = content[:20000]
            c = self.db.save(c, data=d)
            if not d.get("brief"):
                c = self.db.save(c, stage="collect")
                self.card(c)
                return
            self.coach(c)

    def coach(self, c):
        d = copy.deepcopy(c["data"])
        c = self.db.save(c, stage="coaching", data=d)
        self.card(c)
        try:
            references = self.files.context(c)
            if d.get("prior_summary"):
                references += "\nPrior run:\n" + d["prior_summary"]
            if "analysis" not in d:
                d["analysis"] = self.analyze(
                    d["brief"],
                    context_mode="github" if self.repository(d["brief"]) else "brief",
                    attachment_count=len(d["upload_ids"]),
                    planning_depth=d["depth"],
                    reference_summary=references[:8000],
                )
                if not d["depth_manual"]:
                    d["depth"] = d["analysis"]["recommended_depth"]
                d["answers"] = []
                if d["analysis"]["questions"]:
                    c = self.db.save(c, stage="interview", data=d)
                    self.card(c)
                    return
                if d["analysis"]["suitability"] == "poor_fit":
                    c = self.db.save(c, stage="collect", data=d)
                    self.notice(c, d["analysis"]["summary"], c["id"] + ":poor-fit")
                    self.card(c)
                    return
            d["finalized"] = self.finalize(
                d["brief"],
                questions=d["analysis"]["questions"],
                answers=d["answers"],
                context_mode="github" if self.repository(d["brief"]) else "brief",
                attachment_count=len(d["upload_ids"]),
                planning_depth=d["depth"],
                reference_summary=references[:8000],
            )
            c = self.db.save(c, stage="preview", data=d)
            self.preview(c)
        except StaleDraft:
            raise
        except Exception:
            c = self.db.save(c, stage="collect", data=d)
            self.notice(
                c,
                "Luna could not prepare this brief. Reply here to retry; no run was submitted.",
                c["id"] + ":coach-error:" + str(c["version"]),
            )
            self.card(c)

    def preview(self, c):
        d = copy.deepcopy(c["data"])
        try:
            d["roster"] = self.resolve(
                d["depth"], effort_overrides=d.get("effort_overrides")
            )
            d.pop("roster_error", None)
        except ValueError as error:
            d.pop("roster", None)
            d["roster_error"] = str(error)
        c = self.db.save(c, stage="preview", data=d)
        self.card(c)
        return c

    def answer(self, c, answer, key, question=None):
        if c["stage"] != "interview":
            raise StaleDraft("Question no longer active.")
        d = copy.deepcopy(c["data"])
        q = d["analysis"]["questions"][len(d["answers"])]
        if question and q["id"] != question:
            raise StaleDraft("Question changed.")
        if not str(answer).strip():
            return
        d["answers"].append(
            {"question_id": q["id"], "answer": str(answer).strip()[:4000]}
        )
        c = self.db.save(c, data=d)
        if len(d["answers"]) == len(d["analysis"]["questions"]):
            self.coach(c)
        else:
            self.card(c)

    def action(self, user, action, value, key):
        self.member(user)
        c = self.db.get(value.get("id"))
        if not c or c["team"] != self.team or c["owner"] != user:
            raise AccessDenied("Only the request owner may change it.")
        if action == "moa_cancel":
            return self.cancel(c, key)
        with self.conversation_lock(c["id"]):
            c = self.db.get(c["id"])
            if value.get("v") != c["version"]:
                raise StaleDraft("Use the latest card.")
            d = copy.deepcopy(c["data"])
            if action == "moa_answer":
                return self.answer(c, value["answer"], key, value["question"])
            if c["stage"] != "preview":
                raise StaleDraft("Preview no longer active.")
            if action == "moa_run":
                return self.run(c)
            if action == "moa_depth":
                d.update(depth=value["depth"], depth_manual=True, effort_overrides={})
            elif action == "moa_advanced":
                d["advanced"] = not d.get("advanced")
            elif action == "moa_effort":
                d.setdefault("effort_overrides", {})[value["route"]] = value["effort"]
            elif action == "moa_edit":
                d.pop("analysis", None)
                d["answers"] = []
                c = self.db.save(c, stage="collect", data=d)
                self.notice(c, "Reply with your revised brief.", key)
                self.card(c)
                return
            else:
                return
            self.preview(self.db.save(c, data=d))

    def repository(self, brief):
        matches = re.findall(
            r"(?:https://github\.com/|\brepo\s+)("
            + re.escape(self.github_owner)
            + r"/[A-Za-z0-9_.-]+)",
            brief,
        )
        return matches[0].removesuffix(".git") if len(set(matches)) == 1 else None

    def check_repository(self, pointer):
        proc = subprocess.run(
            ["gh", "repo", "view", pointer, "--json", "isPrivate"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode:
            raise LaunchError("Could not validate this repository’s access.")
        if json.loads(proc.stdout)["isPrivate"] and pointer not in self.private_repos:
            raise LaunchError(
                "This private repository needs explicit approval in the Slack bot’s repository allowlist. Task-only and uploaded-file runs are available."
            )

    def run(self, c):
        if c["stage"] != "preview":
            raise StaleDraft("Run already submitted or draft changed.")
        d = copy.deepcopy(c["data"])
        try:
            roster = self.resolve(
                d["depth"], effort_overrides=d.get("effort_overrides")
            )
            pointer = self.repository(d["brief"])
            if pointer:
                self.check_repository(pointer)
        except ValueError as error:
            self.notice(c, str(error), c["id"] + ":launch-error:" + str(c["version"]))
            return
        body = {
            "transport": "slack",
            "goal": d["finalized"]["optimized_prompt"],
            "title": d["brief"].splitlines()[0][:140],
            "source_mode": "github" if pointer else "brief",
            "github_repository": pointer or "",
            "upload_ids": d["upload_ids"],
            "proposers": roster["proposers"],
            "refiners": roster["refiners"],
            "aggregator": roster["aggregator"],
            "options": roster["options"],
            "clarifications_resolved": d["answers"],
        }
        d["roster"] = roster
        d["launch_body"] = body
        d["share_token"] = secrets.token_urlsafe(32)
        job_id = "slack-" + c["id"]
        c = self.db.save(c, stage="launching", data=d, job_id=job_id)
        self.card(c)
        self.launch(c)

    def launch(self, c):
        profile = self.store.get_profile(c["profile_id"])
        if self.db.get(c["id"])["stage"] == "cancelled":
            return
        job = self.launcher.launch(
            profile, c["data"]["launch_body"], job_id=c["job_id"]
        )
        if self.db.get(c["id"])["stage"] == "cancelled":
            self.store.request_cancel(job["id"])
            if job["status"] == "queued":
                self.store.update_job(
                    job["id"],
                    status="cancelled",
                    phase="cancelled",
                    finished_at=time.time(),
                )
            return
        d = copy.deepcopy(c["data"])
        digest = hashlib.sha256(d["share_token"].encode()).hexdigest()
        if not self.store.get_run_share(digest):
            self.store.create_run_share(job["id"], profile["id"], digest)
        d["dashboard"] = self.base_url + "/shared/runs/" + d["share_token"]
        d["progress"] = (
            "Queued. I’ll update this card through preparation, proposals, review and synthesis."
        )
        c = self.db.save(c, stage="running", data=d)
        self.card(c)

    def cancel(self, c, key):
        if c["stage"] in TERMINAL:
            self.card(c)
            return
        if c["job_id"]:
            job = self.store.get_job(c["job_id"])
            if job and job["status"] in TERMINAL:
                self.card(c)
                return
            if job and self.store.request_cancel(job["id"]):
                if job["status"] == "queued":
                    self.store.update_job(
                        job["id"],
                        status="cancelled",
                        phase="cancelled",
                        finished_at=time.time(),
                    )
                else:
                    self.store.update_job(job["id"], status="cancelling")
                self.store.append_event(
                    job["id"], "warning", "Cancellation requested from Slack"
                )
        d = copy.deepcopy(c["data"])
        d["progress"] = "Cancelled."
        c = self.db.save(c, stage="cancelled", data=d)
        self.card(c)

    def monitor(self):
        for c in self.db.live():
            if c["stage"] == "launching":
                with self.conversation_lock(c["id"]):
                    self.launch(self.db.get(c["id"]))
                continue
            if c["stage"] == "coaching":
                continue
            job = self.store.get_job(c["job_id"])
            if not job:
                continue
            if job["status"] in TERMINAL:
                self.finish(c, job)
                continue
            latest = self.store.events_after(
                job["id"], max(0, c["data"].get("event_cursor", 0)), limit=500
            )
            relevant = [
                e
                for e in latest
                if e["kind"]
                in (
                    "phase",
                    "attachment-progress",
                    "attachment",
                    "warning",
                    "validation",
                    "error",
                )
            ]
            phase = job.get("phase", "queued")
            successes = []
            for filename, key in [
                ("layer1-manifest.json", "layer1"),
                ("manifest.json", "layer2"),
            ]:
                p = Path(job["session_dir"]) / filename
                if p.is_file():
                    try:
                        successes += [
                            r["agent_id"]
                            for r in json.loads(p.read_text()).get(key, [])
                            if r.get("success")
                        ]
                    except (ValueError, OSError):
                        pass
            queued = []
            if job["status"] == "queued":
                with self.store.connect() as conn:
                    ahead = conn.execute(
                        "SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling') AND created_at<?",
                        (job["created_at"],),
                    ).fetchone()[0]
                queued = [f"{ahead} run(s) ahead in the queue."]
            progress = (
                f"{job['status'].title()} · {phase} · {round(job['progress'] * 100)}%"
            )
            if queued:
                progress += "\n" + queued[0]
            if successes:
                progress += "\nPassed: " + ", ".join(successes)
            if relevant:
                progress += "\n" + relevant[-1]["message"][:500]
            d = copy.deepcopy(c["data"])
            if latest:
                d["event_cursor"] = latest[-1]["seq"]
            if (
                progress != d.get("progress")
                and time.time() - d.get("last_progress", 0) >= 12
            ):
                d.update(progress=progress, last_progress=time.time())
                c = self.db.save(c, data=d)
                self.card(c)
            elif latest:
                self.db.save(c, data=d)

    def finish(self, c, job):
        d = copy.deepcopy(c["data"])
        d["progress"] = job["status"].title()
        folder = Path(job["session_dir"])
        if job["status"] == "completed" and (folder / "final-plan.md").is_file():
            plan = (folder / "final-plan.md").read_text()
            manifest = json.loads((folder / "manifest.json").read_text())
            rows = [
                r
                for key in ("layer1", "layer2", "layer3")
                for r in manifest.get(key, [])
            ]
            passed = sum(bool(r.get("success")) for r in rows)
            lineage = json.loads((folder / "final-plan.json").read_text())
            confidence = (lineage.get("confidence") or {}).get("level", "unrecorded")
            tldr = re.search(r"## TL;DR\s*(.*?)(?=\n## |\Z)", plan, re.S)
            summary = tldr.group(1).strip() if tldr else plan[:2200]
            reply = (
                "*MoA-X finished*\n"
                + text(summary[:2400])
                + f"\n\n{passed}/{len(rows)} lanes passed · Confidence: {confidence}."
            )
            if passed < len(rows):
                reply += " Some lanes failed; inspect the report’s limitations."
            reply += (
                "\nThe complete plan and report follow as files.\n<"
                + d["dashboard"]
                + "|Open dashboard and results>"
            )
            self.db.put("final:" + c["id"], c, "post", {"text": reply})
            for name, title in [
                ("final-plan.md", "Complete MoA-X plan"),
                ("report.html", "MoA-X ensemble report"),
            ]:
                self.db.put(
                    "file:" + c["id"] + ":" + name,
                    c,
                    "file",
                    {"path": str(folder / name), "filename": name, "title": title},
                )
        else:
            message = (
                "MoA-X "
                + job["status"]
                + ". "
                + str(
                    job.get("summary")
                    or "Retained outputs are available on the dashboard."
                )[:1000]
            )
            if d.get("dashboard"):
                message += "\n<" + d["dashboard"] + "|Inspect the run>"
            self.db.put("final:" + c["id"], c, "post", {"text": message})
        c = self.db.save(c, stage=job["status"], data=d)
        self.card(c)
