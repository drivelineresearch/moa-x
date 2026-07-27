"""Flask application factory and JSON/SSE control plane for MoA-X."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from .github import (
    GitHubWorkspaceError,
    allowed_owner,
    clone_repository,
    list_repositories,
    parse_repo_pointer,
)
from .monitoring import (
    DEFAULT_INTERVAL_SECONDS,
    OperationalError,
    ProviderHealthMonitor,
    capture_operational_error,
    configure_sentry,
)
from .providers import ROUTE_META, model_catalog, probe_provider, provider_catalog
from .prompt_coach import PromptCoachError, analyze as analyze_prompt
from .prompt_coach import finalize as finalize_prompt
from .store import Store
from .worker import JobWorker


WEBUI_DIR = Path(__file__).resolve().parent
HARNESS_DIR = WEBUI_DIR.parent
REPO_ROOT = HARNESS_DIR.parent
RUNNER = HARNESS_DIR / "scripts" / "run_moa.py"
ACTIVE_STATES = {"queued", "running", "cancelling"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "imported"}
LOCAL_FONT_FILES = {
    "GothamOffice-Regular.woff2": "font/woff2",
    "GothamOffice-Bold.woff2": "font/woff2",
}
PROFILE_COOKIE_NAME = "moax_profile_token"
PROFILE_TOKEN_MAX_AGE = 365 * 24 * 60 * 60


def _default_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return base / "moa-x"


def _default_local_font_dir() -> Path:
    configured = os.environ.get("MOA_WEBUI_LOCAL_FONT_DIR")
    if configured:
        return Path(configured).expanduser()
    return _default_data_dir() / "fonts"


def _local_fonts_ready(font_dir: Path) -> bool:
    return all((font_dir / filename).is_file() for filename in LOCAL_FONT_FILES)


def _local_font_css() -> str:
    return """
.gotham-office-ready {
  --font-display: "MoAX Gotham Office", "MoAX Gotham", Inter, ui-sans-serif, system-ui, sans-serif;
  --font-body: "MoAX Gotham Office", "MoAX Gotham", Inter, ui-sans-serif, system-ui, sans-serif;
}
""".strip()


def _roots_from_env() -> list[Path]:
    raw = os.environ.get("MOA_WEBUI_WORKSPACE_ROOTS")
    values = raw.split(os.pathsep) if raw else [str(Path.home())]
    return [Path(item).expanduser().resolve() for item in values if item]


def _safe_workspace(raw: str, roots: list[Path]) -> Path:
    if not raw:
        raise ValueError("workspace is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("workspace must be an existing directory")
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("workspace is outside the configured workspace roots")
    return path


def _phase_number(value: Any) -> int:
    text = str(value or "").lower()
    if "layer3" in text or "aggreg" in text or "decide" in text:
        return 3
    if "layer2" in text or "refin" in text or "review" in text:
        return 2
    if "layer1" in text or "propos" in text:
        return 1
    return 0


def _agent_views(job: dict[str, Any], session: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Resolve card state and retry guidance from persisted run checkpoints."""
    final_manifest = session / "manifest.json"
    layer1_manifest = session / "layer1-manifest.json"

    def read_manifest(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    final_doc = read_manifest(final_manifest)
    layer1_doc = read_manifest(layer1_manifest)
    manifest = final_doc or layer1_doc

    config = job.get("config") or {}
    options = config.get("options") or {}
    model_overrides = options.get("model_overrides") or {}
    effort_overrides = options.get("effort_overrides") or {}
    manifest_config = manifest.get("config") or {}
    layer1_config = (layer1_doc.get("config") or manifest_config)
    configured: dict[tuple[int, str], dict[str, Any]] = {}
    for layer, key, source_config in (
        (1, "proposers", layer1_config),
        (2, "refiners", manifest_config),
    ):
        for item in source_config.get(key, []) or []:
            if isinstance(item, dict) and item.get("name"):
                configured[(layer, str(item["name"]))] = item
    aggregator_config = manifest_config.get("aggregator")
    if isinstance(aggregator_config, dict) and aggregator_config.get("name"):
        configured[(3, str(aggregator_config["name"]))] = aggregator_config

    results: dict[tuple[int, str], dict[str, Any]] = {}
    for layer, key, source_doc in (
        (1, "layer1", layer1_doc or manifest),
        (2, "layer2", manifest),
        (3, "layer3", manifest),
    ):
        entries = source_doc.get(key) or []
        if not isinstance(entries, list):
            continue
        for item in entries:
            if isinstance(item, dict) and item.get("agent_id"):
                results[(layer, str(item["agent_id"]))] = item

    proposer_names = config.get("proposers") or [
        str(item["name"])
        for item in layer1_config.get("proposers", []) or []
        if isinstance(item, dict) and item.get("name")
    ] or [
        agent for layer, agent in results if layer == 1
    ]
    refiner_names = config.get("refiners") or [
        str(item["name"])
        for item in manifest_config.get("refiners", []) or []
        if isinstance(item, dict) and item.get("name")
    ] or [
        agent for layer, agent in results if layer == 2
    ]
    if "aggregator" in config:
        aggregator_name = config.get("aggregator")
    elif isinstance(aggregator_config, dict):
        aggregator_name = aggregator_config.get("name")
    else:
        aggregator_name = next(
            (agent for layer, agent in results if layer == 3), None
        )
    roster = [
        *((1, str(name), "proposer") for name in proposer_names),
        *((2, str(name), "refiner") for name in refiner_names),
    ]
    if aggregator_name:
        roster.append((3, str(aggregator_name), "aggregator"))

    current_phase = _phase_number(job.get("phase"))
    terminal = job.get("status") in TERMINAL_STATES
    retry = config.get("redispatch") or {}
    retry_phase = _phase_number(retry.get("phase"))
    retry_agents = {str(name) for name in retry.get("agents") or []}
    cards: list[dict[str, Any]] = []
    for layer, agent_id, role in roster:
        result = results.get((layer, agent_id))
        if (
            job.get("status") in ACTIVE_STATES
            and retry_phase == layer
            and agent_id in retry_agents
            and current_phase in {0, layer}
        ):
            # A targeted redispatch copies old checkpoints into the new
            # session. Do not present the copied result as current while that
            # lane is queued or rerunning.
            result = None
        route_config = configured.get((layer, agent_id), {})
        model = (
            model_overrides.get(agent_id)
            or route_config.get("model")
            or agent_id
        )
        effort = (
            effort_overrides.get(agent_id)
            or route_config.get("effort")
            or route_config.get("reasoning_effort")
        )
        display = ROUTE_META.get(agent_id, {}).get("label", agent_id)
        card: dict[str, Any] = {
            "id": agent_id,
            "name": f"{display} · {role}",
            "model": (
                f"{model} · {str(effort).title()} effort"
                if effort and str(effort).lower() != "default"
                else str(model)
            ),
            "role": role,
        }
        if result is not None:
            card["status"] = "completed" if result.get("success") else "failed"
            started_at = result.get("started_at")
            duration = result.get("duration_seconds")
            if isinstance(started_at, (int, float)):
                card["started_at"] = datetime.fromtimestamp(
                    started_at, UTC
                ).isoformat()
                if isinstance(duration, (int, float)):
                    card["finished_at"] = datetime.fromtimestamp(
                        started_at + max(0, duration), UTC
                    ).isoformat()
            else:
                card["started_at"] = started_at
            if result.get("error"):
                card["summary"] = str(result["error"])[:1000]
        elif job.get("status") == "cancelled":
            card["status"] = "cancelled"
            card["summary"] = "This lane did not finish because the run was cancelled."
        elif terminal or current_phase > layer:
            card["status"] = "blocked"
            card["summary"] = (
                "This lane did not run because an earlier stage ended the workflow."
            )
        elif current_phase == layer and job.get("status") == "running":
            card["status"] = "running"
        else:
            card["status"] = "queued"
        cards.append(card)

    recovery = None
    if job.get("status") == "failed":
        failed_layer1 = [
            agent for (layer, agent), item in results.items()
            if layer == 1 and not item.get("success")
        ]
        successful_layer1 = any(
            layer == 1 and item.get("success")
            for (layer, _), item in results.items()
        )
        failed_layer2 = [
            agent for (layer, agent), item in results.items()
            if layer == 2 and not item.get("success")
        ]
        if failed_layer1 and not successful_layer1:
            recovery = {"phase": "layer1", "agents": failed_layer1}
        elif failed_layer2:
            recovery = {"phase": "layer2", "agents": failed_layer2}
    return cards, recovery


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    session = Path(job["session_dir"])
    artifacts = {}
    for name, filename in (
        ("report", "report.html"),
        ("final_plan", "final-plan.md"),
        ("manifest", "manifest.json"),
        ("synthesis", "synthesis-input.md"),
    ):
        if (session / filename).exists():
            artifacts[name] = f"/api/jobs/{job['id']}/artifacts/{filename}"
    job["artifacts"] = artifacts
    job["active"] = job["status"] in ACTIVE_STATES
    config = job.get("config") or {}
    job["roster"] = {
        "proposers": config.get("proposers") or [],
        "refiners": config.get("refiners") or [],
        "aggregator": config.get("aggregator"),
    }
    job["agents"], job["recovery"] = _agent_views(job, session)
    if job["agents"]:
        if not job["roster"]["proposers"]:
            job["roster"]["proposers"] = [
                item["id"] for item in job["agents"] if item["role"] == "proposer"
            ]
        if not job["roster"]["refiners"]:
            job["roster"]["refiners"] = [
                item["id"] for item in job["agents"] if item["role"] == "refiner"
            ]
        if not job["roster"]["aggregator"]:
            job["roster"]["aggregator"] = next(
                (
                    item["id"]
                    for item in job["agents"]
                    if item["role"] == "aggregator"
                ),
                None,
            )
    for field in ("created_at", "started_at", "finished_at"):
        if isinstance(job.get(field), (int, float)):
            job[field] = datetime.fromtimestamp(job[field], UTC).isoformat()
    return job


def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    event["type"] = event.get("kind", "message")
    if isinstance(event.get("created_at"), (int, float)):
        event["created_at"] = datetime.fromtimestamp(
            event["created_at"], UTC
        ).isoformat()
    return event


def _dated_view(record: dict[str, Any]) -> dict[str, Any]:
    for field in ("created_at", "updated_at", "last_checked_at"):
        if isinstance(record.get(field), (int, float)):
            record[field] = datetime.fromtimestamp(record[field], UTC).isoformat()
    return record


def _upload_view(upload: dict[str, Any]) -> dict[str, Any]:
    upload = _dated_view(dict(upload))
    upload.pop("stored_path", None)
    upload["url"] = f"/api/uploads/{upload['id']}/content"
    return upload


def _profile_view(profile: dict[str, Any]) -> dict[str, Any]:
    profile = _dated_view(dict(profile))
    profile["name"] = profile["display_name"]
    return profile


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    # Load repo-local .env before initializing integrations. Existing process
    # environment values retain precedence.
    from .providers import harness_config

    harness_config.apply_config_to_env()
    sentry_enabled = not (test_config or {}).get("TESTING") and configure_sentry()
    data_dir = _default_data_dir()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["PREFERRED_URL_SCHEME"] = "https"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config.from_mapping(
        DATABASE=str(data_dir / "webui.sqlite3"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        GITHUB_WORKSPACE_DIR=str(data_dir / "workspaces" / "github"),
        GITHUB_OWNER=allowed_owner(),
        BRIEF_WORKSPACE_DIR=str(data_dir / "workspaces" / "brief"),
        MAX_UPLOAD_BYTES=25 * 1024 * 1024,
        MAX_CONTENT_LENGTH=251 * 1024 * 1024,
        WORKSPACE_ROOTS=_roots_from_env(),
        LOCAL_FONT_DIR=str(_default_local_font_dir()),
        START_WORKER=True,
        START_PROVIDER_MONITOR=True,
        PROVIDER_MONITOR_INTERVAL_SECONDS=float(
            os.environ.get(
                "MOA_PROVIDER_MONITOR_INTERVAL_SECONDS",
                DEFAULT_INTERVAL_SECONDS,
            )
        ),
        SSE_POLL_SECONDS=0.6,
        PROFILE_COOKIE_SECURE=False,
    )
    if test_config:
        app.config.update(test_config)
    app.config["WORKSPACE_ROOTS"] = [
        Path(path).expanduser().resolve() for path in app.config["WORKSPACE_ROOTS"]
    ]
    upload_dir = Path(app.config["UPLOAD_DIR"]).expanduser().resolve()
    github_workspace_dir = (
        Path(app.config["GITHUB_WORKSPACE_DIR"]).expanduser().resolve()
    )
    brief_workspace_dir = (
        Path(app.config["BRIEF_WORKSPACE_DIR"]).expanduser().resolve()
    )
    local_font_dir = Path(app.config["LOCAL_FONT_DIR"]).expanduser().resolve()
    for private_dir in (upload_dir, github_workspace_dir, brief_workspace_dir):
        private_dir.mkdir(parents=True, exist_ok=True)
        try:
            private_dir.chmod(0o700)
        except OSError:
            pass
    if github_workspace_dir not in app.config["WORKSPACE_ROOTS"]:
        app.config["WORKSPACE_ROOTS"].append(github_workspace_dir)

    store = Store(Path(app.config["DATABASE"]))
    # A read-only/test app may share the database with the production worker.
    # Only the worker-owning process may reconcile orphaned active jobs;
    # otherwise opening a second UI instance would falsely interrupt live runs.
    if app.config["START_WORKER"]:
        interrupted_jobs = store.reconcile_interrupted_jobs()
        if interrupted_jobs:
            capture_operational_error(
                OperationalError(
                    f"{len(interrupted_jobs)} active run(s) interrupted by Web UI restart"
                ),
                operation="worker.restart_interrupted_jobs",
                context={
                    "interrupted_count": len(interrupted_jobs),
                    "job_ids": interrupted_jobs[:20],
                },
            )
    worker = JobWorker(store, RUNNER)
    app.extensions["moa_store"] = store
    app.extensions["moa_worker"] = worker
    app.extensions["moa_sentry_enabled"] = sentry_enabled
    if app.config["START_WORKER"]:
        worker.start()
    if app.config["START_WORKER"] and app.config["START_PROVIDER_MONITOR"]:
        provider_monitor = ProviderHealthMonitor(
            lambda: provider_catalog(probe=True),
            interval_seconds=app.config["PROVIDER_MONITOR_INTERVAL_SECONDS"],
        )
        provider_monitor.start()
        app.extensions["moa_provider_monitor"] = provider_monitor

    def current_profile() -> dict[str, Any] | None:
        token = str(request.cookies.get(PROFILE_COOKIE_NAME) or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,160}", token):
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return store.get_profile_by_token_hash(digest)

    def require_profile(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            profile = current_profile()
            if not profile:
                return _error("private browser session required", 401)
            return view(profile, *args, **kwargs)

        return wrapped

    def owned_job(
        profile: dict[str, Any], job_id: str
    ) -> dict[str, Any] | None:
        job = store.get_job(job_id)
        if not job or job.get("profile_id") != profile["id"]:
            return None
        return job

    @app.get("/")
    @app.get("/new")
    @app.get("/runs")
    @app.get("/runs/<job_id>")
    @app.get("/providers")
    def index(job_id: str | None = None):
        return render_template(
            "index.html",
            bootstrap={
                "workspace": str(REPO_ROOT),
                "github_owner": app.config["GITHUB_OWNER"],
            },
            local_font_stylesheet=_local_fonts_ready(local_font_dir),
        )

    @app.get("/local-assets/fonts.css")
    def local_font_stylesheet():
        if not _local_fonts_ready(local_font_dir):
            abort(404)
        return Response(
            _local_font_css(),
            mimetype="text/css",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/local-assets/fonts/<filename>")
    def local_font(filename: str):
        mimetype = LOCAL_FONT_FILES.get(filename)
        if mimetype is None or not _local_fonts_ready(local_font_dir):
            abort(404)
        return send_file(
            local_font_dir / filename,
            mimetype=mimetype,
            conditional=True,
            max_age=86_400,
        )

    @app.get("/favicon.ico")
    def favicon():
        return send_file(
            WEBUI_DIR / "static" / "images" / "favicon.png",
            mimetype="image/png",
            max_age=86_400,
        )

    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "database": str(store.path),
                "runner": str(RUNNER),
                "worker": bool(app.config["START_WORKER"]),
            }
        )

    @app.get("/api/workspaces")
    @require_profile
    def workspaces(profile: dict[str, Any]):
        roots = app.config["WORKSPACE_ROOTS"]
        recent = []
        seen = set()
        for job in store.list_jobs(limit=100, profile_id=profile["id"]):
            workspace = job["workspace"]
            if workspace not in seen:
                seen.add(workspace)
                recent.append(workspace)
        github_workspaces = store.list_github_workspaces(
            profile_id=profile["id"]
        )
        for workspace in github_workspaces:
            path = workspace["local_path"]
            if path not in seen and Path(path).is_dir():
                seen.add(path)
                recent.append(path)
        return jsonify(
            {
                "workspaces": [
                    {
                        "path": path,
                        "name": Path(path).name or path,
                        "source": (
                            "github"
                            if any(
                                item["local_path"] == path
                                for item in github_workspaces
                            )
                            else "local"
                        ),
                    }
                    for path in dict.fromkeys([str(REPO_ROOT), *recent])
                    if any(
                        Path(path).resolve() == root
                        or root in Path(path).resolve().parents
                        for root in roots
                    )
                ],
                "roots": [str(root) for root in roots],
                "recent": recent[:20],
                "current": str(REPO_ROOT),
            }
        )

    @app.post("/api/profiles")
    def profiles():
        body = request.get_json(silent=True) or {}
        profile_id = str(body.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", profile_id):
            return _error("profile id must be 6-80 URL-safe characters")
        session_profile = current_profile()
        existing = store.get_profile(profile_id)
        if session_profile and session_profile["id"] != profile_id:
            return _error(
                "this browser is already bound to another private profile",
                409,
            )
        if (
            existing
            and not session_profile
            and store.profile_token_claimed(profile_id)
        ):
            return _error("profile is private to another browser", 403)
        display_name = str(
            body.get("display_name")
            or body.get("name")
            or (existing or {}).get("display_name")
            or "Local user"
        ).strip()[:80]
        settings = (
            body["settings"]
            if isinstance(body.get("settings"), dict)
            else (existing or {}).get("settings") or {}
        )
        profile = store.upsert_profile(profile_id, display_name, settings)
        token = None
        if not session_profile:
            token = secrets.token_urlsafe(48)
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not store.claim_profile_token(profile_id, digest):
                return _error("profile could not be claimed", 409)
            profile = store.get_profile(profile_id) or profile
        response = jsonify(_profile_view(profile))
        if token:
            response.set_cookie(
                PROFILE_COOKIE_NAME,
                token,
                max_age=PROFILE_TOKEN_MAX_AGE,
                httponly=True,
                secure=bool(app.config["PROFILE_COOKIE_SECURE"]),
                samesite="Strict",
                path="/",
            )
        return response

    @app.get("/api/session")
    def profile_session():
        profile = current_profile()
        return jsonify(_profile_view(profile) if profile else None)

    @app.get("/api/profiles/<profile_id>")
    @require_profile
    def get_profile(profile: dict[str, Any], profile_id: str):
        if profile["id"] != profile_id:
            return _error("profile not found", 404)
        return jsonify(_profile_view(profile))

    @app.get("/api/uploads")
    @require_profile
    def uploads(profile: dict[str, Any]):
        limit = min(max(request.args.get("limit", 100, type=int), 1), 250)
        return jsonify(
            {
                "uploads": [
                    _upload_view(upload)
                    for upload in store.list_uploads(
                        profile_id=profile["id"], limit=limit
                    )
                ]
            }
        )

    @app.post("/api/uploads")
    @require_profile
    def create_upload(profile: dict[str, Any]):
        incoming_files = request.files.getlist("files") or request.files.getlist(
            "file"
        )
        incoming_files = [
            item for item in incoming_files if item and item.filename
        ]
        if not incoming_files:
            return _error("one or more multipart files are required")
        if len(incoming_files) > 10:
            return _error("at most 10 files may be uploaded at once")
        profile_id = profile["id"]
        profile_dir = upload_dir / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        uploaded = []
        for incoming in incoming_files:
            filename = secure_filename(Path(incoming.filename).name)[:160]
            if not filename:
                return _error("file name is invalid")
            upload_id = uuid.uuid4().hex
            temporary = profile_dir / f".{upload_id}.tmp"
            stored = profile_dir / upload_id
            digest = hashlib.sha256()
            size = 0
            try:
                with temporary.open("xb") as output:
                    while chunk := incoming.stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > app.config["MAX_UPLOAD_BYTES"]:
                            return _error(
                                f"{filename} exceeds the 25 MB file limit", 413
                            )
                        digest.update(chunk)
                        output.write(chunk)
                if size == 0:
                    return _error(f"{filename} is empty")
                temporary.replace(stored)
                upload = store.insert_upload(
                    {
                        "id": upload_id,
                        "profile_id": profile_id,
                        "original_name": filename,
                        "stored_path": str(stored),
                        "content_type": incoming.mimetype,
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                        "created_at": time.time(),
                    }
                )
                uploaded.append(_upload_view(upload))
            finally:
                if temporary.exists():
                    temporary.unlink()
        return jsonify({"uploads": uploaded}), 201

    @app.get("/api/uploads/<upload_id>/content")
    @require_profile
    def upload_content(profile: dict[str, Any], upload_id: str):
        upload = store.get_upload(upload_id)
        if not upload or upload.get("profile_id") != profile["id"]:
            return _error("upload not found", 404)
        path = Path(upload["stored_path"])
        if not path.is_file() or upload_dir not in path.resolve().parents:
            return _error("uploaded file is unavailable", 404)
        return send_file(
            path,
            mimetype=upload.get("content_type") or "application/octet-stream",
            as_attachment=True,
            download_name=upload["original_name"],
        )

    @app.get("/api/uploads/<upload_id>")
    @require_profile
    def get_upload(profile: dict[str, Any], upload_id: str):
        upload = store.get_upload(upload_id)
        if not upload or upload.get("profile_id") != profile["id"]:
            return _error("upload not found", 404)
        return jsonify(_upload_view(upload))

    @app.get("/api/github/repos")
    @require_profile
    def github_repositories(profile: dict[str, Any]):
        try:
            raw_repos = list_repositories(app.config["GITHUB_OWNER"])
        except GitHubWorkspaceError as exc:
            capture_operational_error(
                exc,
                operation="github.list_repositories",
                context={"owner": app.config["GITHUB_OWNER"]},
            )
            return _error(str(exc), 502)
        local_by_id = {
            item["id"]: item["local_path"]
            for item in store.list_github_workspaces(
                profile_id=profile["id"]
            )
            if Path(item["local_path"]).is_dir()
        }
        repos = []
        for item in raw_repos:
            full_name = item["nameWithOwner"]
            default_ref = item.get("defaultBranchRef") or {}
            repos.append(
                {
                    "id": full_name,
                    "name": item["name"],
                    "full_name": full_name,
                    "description": item.get("description") or "",
                    "default_branch": default_ref.get("name") or "main",
                    "defaultBranch": default_ref.get("name") or "main",
                    "private": bool(item.get("isPrivate")),
                    "url": item.get("url"),
                    "pushed_at": item.get("pushedAt"),
                    "pushedAt": item.get("pushedAt"),
                    "local_path": local_by_id.get(full_name),
                }
            )
        return jsonify(
            {"owner": app.config["GITHUB_OWNER"], "repositories": repos}
        )

    @app.post("/api/workspaces/github")
    @require_profile
    def github_workspace(profile: dict[str, Any]):
        body = request.get_json(silent=True) or {}
        pointer = str(body.get("repo") or "").strip()
        git_ref = (
            str(body.get("ref") or body.get("github_ref") or "").strip() or None
        )
        try:
            owner, repo = parse_repo_pointer(
                pointer, app.config["GITHUB_OWNER"]
            )
        except ValueError as exc:
            return _error(str(exc))
        try:
            result = clone_repository(
                pointer,
                github_workspace_dir,
                git_ref=git_ref,
                owner_allowlist=app.config["GITHUB_OWNER"],
            )
        except (GitHubWorkspaceError, ValueError) as exc:
            if isinstance(exc, GitHubWorkspaceError):
                capture_operational_error(
                    exc,
                    operation="github.clone_workspace",
                    context={"owner": app.config["GITHUB_OWNER"]},
                )
            return _error(str(exc), 502)
        workspace = store.upsert_github_workspace(
            owner=owner,
            repo=repo,
            profile_id=profile["id"],
            local_path=result["path"],
            remote_url=result["remote_url"],
        )
        response = _dated_view(workspace)
        response["path"] = response.pop("local_path")
        response["created"] = result["created"]
        return jsonify(response), 201 if result["created"] else 200

    @app.get("/api/providers")
    def providers():
        refresh = request.args.get("refresh", "1") != "0"
        return jsonify({"providers": provider_catalog(probe=refresh)})

    @app.post("/api/prompt-helper/analyze")
    @require_profile
    def prompt_helper_analyze(profile: dict[str, Any]):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(
                analyze_prompt(
                    body.get("brief"),
                    context_mode=body.get("context_mode"),
                    attachment_count=body.get("attachment_count"),
                )
            )
        except ValueError as exc:
            return _error(str(exc))
        except PromptCoachError as exc:
            capture_operational_error(
                exc,
                operation="prompt_coach.analyze",
            )
            return _error(str(exc), 503)

    @app.post("/api/prompt-helper/finalize")
    @require_profile
    def prompt_helper_finalize(profile: dict[str, Any]):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(
                finalize_prompt(
                    body.get("brief"),
                    questions=body.get("questions"),
                    answers=body.get("answers"),
                    context_mode=body.get("context_mode"),
                    attachment_count=body.get("attachment_count"),
                )
            )
        except ValueError as exc:
            return _error(str(exc))
        except PromptCoachError as exc:
            capture_operational_error(
                exc,
                operation="prompt_coach.finalize",
            )
            return _error(str(exc), 503)

    @app.post("/api/providers/<provider_id>/probe")
    def provider_probe(provider_id: str):
        try:
            return jsonify(probe_provider(provider_id))
        except KeyError:
            return _error("unknown provider", 404)

    @app.post("/api/providers/probe")
    def providers_probe():
        return jsonify({"providers": provider_catalog(probe=True)})

    @app.get("/api/models")
    def models():
        try:
            return jsonify({"models": model_catalog()})
        except Exception as exc:
            capture_operational_error(
                exc,
                operation="providers.model_catalog",
            )
            return _error(f"could not load model catalog: {exc}", 500)

    @app.get("/api/jobs")
    @require_profile
    def jobs(profile: dict[str, Any]):
        limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
        return jsonify(
            {
                "jobs": [
                    _job_view(job)
                    for job in store.list_jobs(
                        limit=limit, profile_id=profile["id"]
                    )
                ]
            }
        )

    @app.post("/api/jobs")
    @require_profile
    def create_job(profile: dict[str, Any]):
        body = request.get_json(silent=True) or {}
        goal = str(body.get("goal") or "").strip()
        if not goal:
            return _error("goal is required")
        if len(goal) > 100_000:
            return _error("goal is too large")
        profile_id = profile["id"]

        job_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        source_info = None
        source_mode = str(body.get("source_mode") or "").strip().lower()
        github_pointer = str(body.get("github_repository") or "").strip()
        if source_mode == "brief":
            workspace = brief_workspace_dir / job_id
            workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
            try:
                workspace.chmod(0o700)
            except OSError:
                pass
            source_info = {"type": "brief"}
        elif source_mode == "github" or github_pointer:
            git_ref = str(body.get("github_ref") or "").strip() or None
            try:
                owner, repo = parse_repo_pointer(
                    github_pointer, app.config["GITHUB_OWNER"]
                )
                result = clone_repository(
                    github_pointer,
                    github_workspace_dir,
                    git_ref=git_ref,
                    owner_allowlist=app.config["GITHUB_OWNER"],
                )
            except (ValueError, GitHubWorkspaceError) as exc:
                if isinstance(exc, GitHubWorkspaceError):
                    capture_operational_error(
                        exc,
                        operation="github.clone_for_job",
                        context={"owner": app.config["GITHUB_OWNER"]},
                    )
                return _error(
                    str(exc), 400 if isinstance(exc, ValueError) else 502
                )
            store.upsert_github_workspace(
                owner=owner,
                repo=repo,
                profile_id=profile_id,
                local_path=result["path"],
                remote_url=result["remote_url"],
            )
            workspace = Path(result["path"]).resolve()
            source_info = {
                "type": "github",
                "repository": github_pointer,
                "ref": result.get("git_ref") or git_ref,
                "remote_url": result["remote_url"],
            }
        else:
            try:
                workspace = _safe_workspace(
                    str(body.get("workspace") or ""),
                    app.config["WORKSPACE_ROOTS"],
                )
            except ValueError as exc:
                return _error(str(exc))

        attachment_ids = body.get("upload_ids")
        if attachment_ids is None:
            attachment_ids = body.get("attachments")
        upload_ids = _string_list(attachment_ids)
        selected_uploads = []
        for upload_id in upload_ids:
            upload = store.get_upload(upload_id)
            if not upload or upload.get("profile_id") != profile_id:
                return _error(f"upload not found: {upload_id}")
            source = Path(upload["stored_path"]).resolve()
            if not source.is_file() or upload_dir not in source.parents:
                return _error(f"upload is unavailable: {upload_id}", 409)
            selected_uploads.append((upload, source))

        session_dir = workspace / ".moa" / job_id
        session_dir.mkdir(parents=True, exist_ok=False)
        uploaded_files = []
        if selected_uploads:
            inputs_dir = session_dir / "inputs"
            inputs_dir.mkdir()
            for index, (upload, source) in enumerate(selected_uploads, start=1):
                filename = f"{index:02d}-{upload['original_name']}"
                target = inputs_dir / filename
                shutil.copy2(source, target)
                uploaded_files.append(
                    {
                        "upload_id": upload["id"],
                        "name": upload["original_name"],
                        "path": str(target.relative_to(session_dir)),
                        "size_bytes": upload["size_bytes"],
                        "sha256": upload["sha256"],
                    }
                )
        scout = {
            "session_id": job_id,
            "frozen_spec": goal,
            "repo_path": str(workspace),
            "source": source_info or {"type": "local"},
            "focus_files": body.get("focus_files") or [],
            "focus_topics": body.get("focus_topics") or [],
            "in_scope": body.get("in_scope") or [],
            "out_of_scope": body.get("out_of_scope") or [],
            "clarifications_resolved": body.get("clarifications_resolved") or [],
            "uploaded_files": uploaded_files,
        }
        (session_dir / "scout-brief.json").write_text(
            json.dumps(scout, indent=2), encoding="utf-8"
        )
        config = {
            "proposers": _string_list(body.get("proposers")),
            "refiners": _string_list(body.get("refiners")),
            "aggregator": str(body.get("aggregator") or "opus"),
            "options": (
                body.get("options")
                if isinstance(body.get("options"), dict)
                else {}
            ),
            "upload_ids": upload_ids,
        }
        job = store.insert_job(
            {
                "id": job_id,
                "profile_id": profile_id,
                "title": str(body.get("title") or goal.splitlines()[0])[:140],
                "workspace": str(workspace),
                "session_dir": str(session_dir),
                "goal": goal,
                "status": "queued",
                "config": config,
                "created_at": time.time(),
            }
        )
        if uploaded_files:
            store.append_event(
                job_id,
                "attachment-progress",
                (
                    f"Queued {len(uploaded_files)} reference "
                    f"{'file' if len(uploaded_files) == 1 else 'files'} "
                    "for local preparation"
                ),
                {
                    "file_count": len(uploaded_files),
                    "stage": "queued",
                },
            )
        store.append_event(job_id, "job", "Job queued", {"position": "pending"})
        worker.wake()
        return jsonify(_job_view(job)), 201

    @app.get("/api/jobs/<job_id>")
    @require_profile
    def get_job(profile: dict[str, Any], job_id: str):
        job = owned_job(profile, job_id)
        if not job:
            return _error("job not found", 404)
        return jsonify(_job_view(job))

    @app.post("/api/jobs/<job_id>/cancel")
    @require_profile
    def cancel_job(profile: dict[str, Any], job_id: str):
        if not owned_job(profile, job_id):
            return _error("job not found", 404)
        if not worker.cancel(job_id):
            return _error("job is no longer cancellable", 409)
        return jsonify(_job_view(store.get_job(job_id) or {})), 202

    @app.post("/api/jobs/<job_id>/redispatch")
    @require_profile
    def redispatch(profile: dict[str, Any], job_id: str):
        source = owned_job(profile, job_id)
        if not source:
            return _error("job not found", 404)
        body = request.get_json(silent=True) or {}
        agents = _string_list(body.get("agents"))
        phase = str(body.get("phase") or "layer1")
        if phase not in {"layer1", "layer2"}:
            return _error("phase must be layer1 or layer2")
        if source["status"] in ACTIVE_STATES:
            return _error("wait for the current job to stop before redispatch", 409)
        redispatch_id = source["id"] + "-retry-" + uuid.uuid4().hex[:5]
        config = dict(source["config"])
        if agents:
            config["redispatch"] = {"phase": phase, "agents": agents}
        else:
            # The one-click UI action is a full clean retry. Targeted callers
            # can pass {phase, agents} to reuse successful checkpoint outputs.
            config.pop("redispatch", None)
        target = Path(source["workspace"]) / ".moa" / redispatch_id
        if agents:
            shutil.copytree(source["session_dir"], target)
        else:
            target.mkdir(parents=True)
            source_inputs = Path(source["session_dir"]) / "inputs"
            if source_inputs.is_dir():
                shutil.copytree(source_inputs, target / "inputs")
        scout = json.loads(
            (Path(source["session_dir"]) / "scout-brief.json").read_text(
                encoding="utf-8"
            )
        )
        scout["session_id"] = redispatch_id
        (target / "scout-brief.json").write_text(
            json.dumps(scout, indent=2), encoding="utf-8"
        )
        job = store.insert_job(
            {
                "id": redispatch_id,
                "profile_id": source.get("profile_id"),
                "title": f"Retry: {source['title']}"[:140],
                "workspace": source["workspace"],
                "session_dir": str(target),
                "goal": source["goal"],
                "status": "queued",
                "config": config,
                "created_at": time.time(),
            }
        )
        store.append_event(
            redispatch_id,
            "job",
            "Redispatch queued",
            {"source_job_id": job_id},
        )
        worker.wake()
        return jsonify(_job_view(job)), 201

    @app.get("/api/jobs/<job_id>/logs")
    @require_profile
    def logs(profile: dict[str, Any], job_id: str):
        job = owned_job(profile, job_id)
        if not job:
            return _error("job not found", 404)
        tail = min(max(request.args.get("tail", 250, type=int), 1), 5000)
        path = Path(job["session_dir"]) / "webui.log"
        lines = (
            path.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
            if path.exists()
            else []
        )
        return jsonify({"job_id": job_id, "lines": lines})

    @app.get("/api/jobs/<job_id>/events")
    @require_profile
    def events(profile: dict[str, Any], job_id: str):
        if not owned_job(profile, job_id):
            return _error("job not found", 404)
        after = request.args.get(
            "after", request.headers.get("Last-Event-ID", "0"), type=int
        ) or 0

        def stream():
            cursor = after
            idle = 0
            while True:
                batch = store.events_after(job_id, cursor)
                if batch:
                    idle = 0
                    for raw_event in batch:
                        event = _event_view(raw_event)
                        cursor = event["seq"]
                        wire_kind = (
                            "worker-error"
                            if event["kind"] == "error"
                            else event["kind"]
                        )
                        yield (
                            f"id: {cursor}\n"
                            f"event: {wire_kind}\n"
                            f"data: {json.dumps(event)}\n\n"
                        )
                else:
                    idle += 1
                    if idle % 20 == 0:
                        yield ": keepalive\n\n"
                job = store.get_job(job_id)
                if job and job["status"] in TERMINAL_STATES and not batch:
                    break
                time.sleep(float(app.config["SSE_POLL_SECONDS"]))

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/<job_id>/artifacts/<path:filename>")
    @require_profile
    def artifact(
        profile: dict[str, Any], job_id: str, filename: str
    ):
        job = owned_job(profile, job_id)
        if not job:
            return _error("job not found", 404)
        if filename not in {
            "report.html",
            "final-plan.md",
            "final-plan.json",
            "manifest.json",
            "synthesis-input.md",
        }:
            abort(404)
        path = Path(job["session_dir"]) / filename
        if not path.is_file():
            abort(404)
        return send_file(path)

    @app.post("/api/jobs/<job_id>/share")
    @require_profile
    def create_report_share(profile: dict[str, Any], job_id: str):
        job = owned_job(profile, job_id)
        report = Path(job["session_dir"]) / "report.html" if job else None
        if not job or job.get("status") != "completed" or not report.is_file():
            return _error(
                "a completed report is required before it can be shared", 409
            )
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        store.create_report_share(
            job_id=job_id, profile_id=profile["id"], token_hash=digest
        )
        return jsonify({"url": f"/shared/reports/{token}"}), 201

    @app.delete("/api/jobs/<job_id>/share")
    @require_profile
    def revoke_report_share(profile: dict[str, Any], job_id: str):
        if not owned_job(profile, job_id):
            return _error("job not found", 404)
        revoked = store.revoke_report_shares(
            job_id=job_id, profile_id=profile["id"]
        )
        return jsonify({"revoked": revoked})

    @app.get("/shared/reports/<token>")
    def shared_report(token: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,160}", token):
            abort(404)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        share = store.get_active_report_share(digest)
        job = store.get_job(share["job_id"]) if share else None
        report = Path(job["session_dir"]) / "report.html" if job else None
        if not job or job.get("status") != "completed" or not report.is_file():
            abort(404)
        response = send_file(report, mimetype="text/html", conditional=True)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.post("/api/history/import")
    @require_profile
    def history_import(profile: dict[str, Any]):
        body = request.get_json(silent=True) or {}
        try:
            workspace = _safe_workspace(
                str(body.get("workspace") or ""),
                app.config["WORKSPACE_ROOTS"],
            )
        except ValueError as exc:
            return _error(str(exc))
        imported = _import_history(
            store, workspace, profile_id=profile["id"]
        )
        return jsonify({"imported": imported, "count": len(imported)})

    return app


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for item in value
        if (text := str(item).strip())
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", text)
    ]


def _import_history(
    store: Store, workspace: Path, *, profile_id: str | None = None
) -> list[str]:
    root = workspace / ".moa"
    if not root.is_dir():
        return []
    imported = []
    for session in sorted(root.iterdir(), reverse=True):
        scout_path = session / "scout-brief.json"
        if not session.is_dir() or not scout_path.is_file():
            continue
        existing = store.get_job(session.name)
        if existing:
            if (
                profile_id
                and existing.get("profile_id") is None
                and store.claim_job_profile(session.name, profile_id)
            ):
                imported.append(session.name)
            continue
        try:
            scout = json.loads(scout_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifest_path = session / "manifest.json"
        status = "imported"
        summary = "Historical MoA session"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                successes = sum(
                    bool(item.get("success"))
                    for key in ("layer1", "layer2", "layer3")
                    for item in manifest.get(key, [])
                )
                summary = (
                    f"Historical run with {successes} successful agent results."
                )
                status = "completed"
            except (OSError, json.JSONDecodeError):
                status = "failed"
        created = session.stat().st_mtime
        store.insert_job(
            {
                "id": session.name,
                "profile_id": profile_id,
                "title": str(
                    scout.get("frozen_spec") or session.name
                ).splitlines()[0][:140],
                "workspace": str(workspace),
                "session_dir": str(session),
                "goal": str(scout.get("frozen_spec") or ""),
                "status": status,
                "phase": "complete" if status == "completed" else status,
                "progress": 1 if status == "completed" else 0,
                "config": {"historical": True},
                "summary": summary,
                "created_at": created,
                "imported": True,
            }
        )
        store.append_event(
            session.name, "history", "Historical session imported"
        )
        imported.append(session.name)
    return imported


def main() -> None:
    app = create_app()
    host = os.environ.get("MOA_WEBUI_HOST", "127.0.0.1")
    port = int(os.environ.get("MOA_WEBUI_PORT", "7340"))
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    else:
        serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
