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
from .repositories import RepositoryDirectory, RepositoryPolicy
from .messages import message_content

TERMINAL = {"completed", "failed", "cancelled"}


def wants_help(content):
    value = content.lower().strip().rstrip("?!. ")
    if value in {"help", "start", "hi", "hello", "hey", "hey there", "moa-x"}:
        return True
    return bool(re.fullmatch(
        r"(?:(?:hey(?: there)?|hi|hello)[\s,!:–-]+)?(?:do you have|is) moa[- ]?x (?:working|online|running|ready|available)(?: yet| now)?", value))


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
        repository_directory=None,
    ):
        self.store, self.root, self.client = store, Path(root), client
        self.db = SlackStore(store)
        self.team, self.bot_user, self.base_url = team, bot_user, base_url.rstrip("/")
        self.github_owner, self.private_repos = github_owner, set(private_repos)
        self.repository_policy = RepositoryPolicy(github_owner, frozenset(private_repos))
        self.repositories = repository_directory or RepositoryDirectory(self.repository_policy)
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

    def enqueue(self, kind, payload, *, fast_cancel=True):
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
            dm = event.get("channel_type") == "im" or str(
                event.get("channel", "")
            ).startswith("D")
            mention = event.get(
                "type"
            ) == "app_mention" or "<@" + self.bot_user + ">" in str(
                event.get("text", "")
            )
            if not dm and not mention:
                thread = event.get("thread_ts")
                if not thread or not self.db.find(
                    self.team, event.get("channel"), thread, event.get("user")
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
            priority = message_content(event, self.bot_user).lower() == "cancel"
            inserted = self.db.receive(key, {"kind": "message", "event": event, "priority": priority},
                reaction={"channel": event["channel"], "timestamp": event["ts"], "name": "eyes"})
            if priority and fast_cancel:
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
                        "priority": action_name == "moa_cancel",
                    },
                )
                if action_name == "moa_cancel" and fast_cancel:
                    c = self.db.get(value.get("id"))
                    if c and c["owner"] == user and c["team"] == self.team:
                        self.cancel(c, key)
                        self.db.done("slack_inbox", {"id": key})
            return True
        return False

    def process_priority(self, item):
        # Native Slack envelopes already identify the actor. Only an existing
        # owned conversation can be cancelled; never start a model call here.
        p = item["payload"]
        try:
            if p["kind"] == "action":
                c = self.db.get(p["value"].get("id"))
                user = p["user"]
            else:
                e = p["event"]
                user = e.get("user")
                c = self.db.find(self.team, e.get("channel"), e.get("thread_ts") or e["ts"], user)
                if not c and str(e.get("channel", "")).startswith("D"):
                    active = self.db.active(self.team, e["channel"], user)
                    if len(active) == 1:
                        c = active[0]
            if c and c["owner"] == user and c["team"] == self.team:
                self.cancel(c, item["id"])
            self.db.done("slack_inbox", item)
        except Exception as error:
            self.db.retry("slack_inbox", item, type(error).__name__, delay=1)

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
                "MoA-X is available to full members of the configured workspace."
            )
        self.members[user] = (time.time() + 300, info)
        return info

    def card(self, c, *, force=False):
        self.db.put("card:" + c["id"], c, "card", card(c), force=force)

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
        content = message_content(event, self.bot_user)
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
        help_request = wants_help(content) and not event.get("files")
        if c and help_request:
            self.notice(c, "👋 MoA-X is online. Send a task with `new: your task` (and optional reference files). 🧠 Luna asks up to three questions, then you choose depth/effort and press *🚀 Run MoA-X*. Use `status` or `cancel` for this request.", key)
            return
        if c and command == "cancel":
            return self.cancel(c, key)
        if c and command == "status":
            self.card(c, force=True)
            return
        if c and c["stage"] == "coaching" and not command.startswith("new:"):
            self.notice(c, "Luna is still preparing this brief. You can edit it from the next card.", key)
            return
        if c and c["stage"] in ("launching", "running") and not command.startswith("new:"):
            self.notice(
                c,
                "This run’s inputs are frozen. Start another request with “new: your task” or a new mention.",
                key,
            )
            return
        if not c or c["stage"] in TERMINAL or command.startswith("new:"):
            if c and c["stage"] in ("collect", "coaching", "interview", "preview"):
                self.db.cancel(c["id"])
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
                "brief": "" if help_request else content[:20000],
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
            if help_request:
                self.card(c)
                return
        with self.conversation_lock(c["id"]):
            c = self.db.get(c["id"])
            if c["stage"] in ("coaching", "launching", "running"):
                self.notice(c, "This request is still processing. Use the next card, or start another with “new: your task”.", key)
                return
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
            if not content and not file_ids and c["stage"] in ("interview", "preview"):
                self.card(c)
                return
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
                d.pop("finalized", None)
                d["answers"] = []
            elif c["stage"] == "collect" and content:
                if not (command == "retry" and d.get("coach_error")):
                    d["brief"] = content[:20000]
                d.pop("analysis", None)
                d.pop("finalized", None)
                d["answers"] = []
            elif file_ids:
                d.pop("analysis", None)
                d.pop("finalized", None)
                d["answers"] = []
                if content and c["stage"] not in ("interview", "preview"):
                    d["brief"] = content[:20000]
            d.pop("coach_error", None)
            c = self.db.save(c, stage="collect", data=d)
            if not d.get("brief"):
                c = self.db.save(c, stage="collect")
                self.card(c)
                return
            self.coach(c)

    def coach(self, c):
        with self.conversation_lock(c["id"]):
            c = self.db.get(c["id"])
            if c["stage"] not in ("collect", "coaching", "interview"):
                return
            return self._coach(c)

    def _coach(self, c):
        d = copy.deepcopy(c["data"])
        c = self.db.save(c, stage="coaching", data=d)
        self.card(c)
        try:
            d["repository"] = None
            repository_context = d["brief"] + "\n" + "\n".join(str(answer.get("answer") or "") for answer in d.get("answers", []))
            pointer = self.repository(repository_context)
            if pointer:
                self.check_repository(pointer)
            d["repository"] = pointer
            d.pop("repository_error", None)
            c = self.db.save(c, data=d)
            self.card(c)
            references = self.files.context(c)
            if pointer:
                references += "\nSelected repository: " + pointer + ". The ensemble will inspect this repository; coaching does not have its source yet."
            if d.get("prior_summary"):
                references += "\nPrior run:\n" + d["prior_summary"]
            if "analysis" not in d:
                d["analysis"] = self.analyze(
                    d["brief"],
                    restricted=True,
                    context_mode="github" if pointer else "brief",
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
                    self.notice(c, d["analysis"]["summary"], c["id"] + ":poor-fit:" + str(c["version"]))
                    self.card(c)
                    return
            d["finalized"] = self.finalize(
                d["brief"],
                restricted=True,
                questions=d["analysis"]["questions"],
                answers=d["answers"],
                context_mode="github" if pointer else "brief",
                attachment_count=len(d["upload_ids"]),
                planning_depth=d["depth"],
                reference_summary=references[:8000],
            )
            c = self.db.save(c, stage="preview", data=d)
            self.preview(c)
        except StaleDraft:
            raise
        except LaunchError as error:
            d["repository_error"] = str(error)
            c = self.db.save(c, stage="collect", data=d)
            self.notice(c, str(error), c["id"] + ":repository-error:" + str(c["version"]))
            self.card(c)
        except Exception:
            d["coach_error"] = True
            c = self.db.save(c, stage="collect", data=d)
            self.notice(
                c,
                "Luna could not prepare this brief. Reply “retry” to try again, or send a revised brief; no run was submitted.",
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
                self.card(self.db.save(c, data=d))
                return
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
        return self.repositories.resolve(brief)

    def check_repository(self, pointer):
        from harness.webui.github import parse_repo_pointer
        parse_repo_pointer(pointer, self.github_owner)
        proc = subprocess.run(
            ["gh", "repo", "view", pointer, "--json", "isPrivate"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode:
            raise LaunchError("Could not validate this repository’s access.")
        if json.loads(proc.stdout)["isPrivate"] and not self.repository_policy.permits_private(pointer):
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
            pointer = d.get("repository") or self.repository(d["brief"])
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
        if self.db.get(c["id"])["stage"] != "launching":
            return
        try:
            job = self.launcher.launch(
                profile, c["data"]["launch_body"], job_id=c["job_id"],
                prepared_context=getattr(self.files, "prepared_context", lambda c: None)(c),
            )
        except LaunchError as error:
            latest = self.db.get(c["id"])
            if latest["stage"] == "launching":
                latest = self.db.save(latest, stage="preview")
                self.notice(latest, str(error), latest["id"] + ":launch-error:" + str(latest["version"]))
                self.card(latest)
            return
        if self.db.get(c["id"])["stage"] == "cancelled":
            self.store.request_cancel(job["id"])
            return
        d = copy.deepcopy(c["data"])
        digest = hashlib.sha256(d["share_token"].encode()).hexdigest()
        if not self.store.get_run_share(digest):
            self.store.create_run_share(job["id"], profile["id"], digest)
        d["dashboard"] = self.base_url + "/shared/runs/" + d["share_token"]
        d["progress"] = (
            "Queued. I’ll update this card through preparation, proposals, review and synthesis."
        )
        try:
            c = self.db.save(c, stage="running", data=d)
        except StaleDraft:
            if self.db.get(c["id"])["stage"] == "cancelled":
                self.store.request_cancel(job["id"])
                self.store.revoke_run_shares(job["id"], c["profile_id"])
                return
            raise
        self.card(c)

    def cancel(self, c, key):
        c = self.db.get(c["id"])
        if c["stage"] in TERMINAL:
            self.card(c)
            return
        if c["job_id"]:
            job = self.store.get_job(c["job_id"])
            if job and job["status"] in TERMINAL:
                self.card(c)
                return
            self.store.revoke_run_shares(c["job_id"], c["profile_id"])
            if job and self.store.request_cancel(job["id"]):
                self.store.append_event(
                    job["id"], "warning", "Cancellation requested from Slack"
                )
        c = self.db.cancel(c["id"])
        self.card(c)

    def resume_launch(self, c):
        lock = self.conversation_lock(c["id"])
        if not lock.acquire(blocking=False):
            return
        try:
            self.launch(self.db.get(c["id"]))
        finally:
            lock.release()

    def monitor(self):
        for c in self.db.live():
            try:
                self._monitor(c)
            except StaleDraft:
                continue
            except Exception:
                # One malformed artifact or failed API operation must not
                # starve progress for every other conversation.
                import logging
                logging.getLogger("moa.slack").warning("One run could not be reconciled; retrying")

    def _monitor(self, c):
        if c["stage"] == "launching":
            return
        if c["stage"] == "coaching":
            return
        job = self.store.get_job(c["job_id"])
        if not job:
            return
        if job["status"] in TERMINAL:
            self.finish(c, job)
            return
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
            or (e["kind"] == "log" and re.match(
                r"^\[orchestrator\] [A-Za-z0-9_-]+: (?:retrying|repairing)", e["message"]
            ))
            or (
                e["kind"] == "log"
                and re.match(
                    r"^\[orchestrator\]\s+[A-Za-z0-9_-]+ (?:proposer|refiner|aggregator): (?:OK|FAIL)",
                    e["message"],
                )
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
        d = copy.deepcopy(c["data"])
        if relevant:
            d["last_detail"] = relevant[-1]["message"][:500]
        if d.get("last_detail"):
            progress += "\n" + d["last_detail"]
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
            try:
                manifest = json.loads((folder / "manifest.json").read_text())
                rows = [r for key in ("layer1", "layer2", "layer3") for r in manifest.get(key, [])]
                passed = sum(bool(r.get("success")) for r in rows)
                lineage = json.loads((folder / "final-plan.json").read_text())
                confidence = (lineage.get("confidence") or {}).get("level", "unrecorded")
                verification = f"{passed}/{len(rows)} lanes passed · Confidence: {confidence}."
                if passed < len(rows):
                    verification += " Some lanes failed; inspect the report’s limitations."
            except (OSError, ValueError, TypeError, AttributeError):
                verification = "Verification metadata is unavailable; inspect the retained plan and report before relying on the result."
            tldr = re.search(r"## TL;DR\s*(.*?)(?=\n## |\Z)", plan, re.S)
            summary = tldr.group(1).strip() if tldr else plan[:2200]
            reply = (
                "✅ *MoA-X finished*\n"
                + text(summary[:2400])
                + "\n\n" + verification
            )
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
                if not (folder / name).is_file():
                    continue
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
