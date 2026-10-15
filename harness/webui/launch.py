"""Shared launch and upload services for browser and Slack transports."""

from __future__ import annotations
import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from . import github
from .providers import harness_config, model_catalog


class LaunchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _string_list(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return (
        [str(part).strip() for part in value or [] if str(part).strip()]
        if isinstance(value, list)
        else []
    )


def _safe_workspace(raw, roots):
    if not raw:
        raise LaunchError("workspace is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise LaunchError("workspace must be an existing directory")
    if not any(path == root or root in path.parents for root in roots):
        raise LaunchError("workspace is outside the configured workspace roots")
    return path


parse_repo_pointer = github.parse_repo_pointer
GitHubWorkspaceError = github.GitHubWorkspaceError


class LaunchService:
    def __init__(
        self,
        store,
        *,
        upload_dir,
        github_workspace_dir,
        brief_workspace_dir,
        github_owner,
        workspace_roots,
        wake=None,
        catalog=None,
        clone=None,
    ):
        self.store = store
        self.upload_dir = Path(upload_dir).resolve()
        self.github_workspace_dir = Path(github_workspace_dir).resolve()
        self.brief_workspace_dir = Path(brief_workspace_dir).resolve()
        self.github_owner = github_owner
        self.workspace_roots = [Path(root).resolve() for root in workspace_roots]
        self.wake = wake
        self.catalog = catalog or (lambda: model_catalog(probe=False))
        self.clone = clone or github.clone_repository
        for folder in [
            self.upload_dir,
            self.github_workspace_dir,
            self.brief_workspace_dir,
        ]:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)

    def launch(self, profile, body, *, job_id=None, prepared_context=None):
        if job_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", job_id):
                raise LaunchError("invalid job id")
            existing = self.store.get_job(job_id)
            if existing:
                if existing["profile_id"] != profile["id"]:
                    raise LaunchError("job belongs to another principal", 403)
                return existing
        store = self.store
        upload_dir = self.upload_dir
        github_workspace_dir = self.github_workspace_dir
        brief_workspace_dir = self.brief_workspace_dir
        goal = str(body.get("goal") or "").strip()
        if not goal:
            raise LaunchError("goal is required")
        if len(goal) > 100_000:
            raise LaunchError("goal is too large")
        requested_proposers = _string_list(body.get("proposers"))
        requested_refiners = _string_list(body.get("refiners"))
        requested_aggregator = str(body.get("aggregator") or "codex-sol")
        route_roles = {
            str(item["id"]): set(item.get("roles") or []) for item in self.catalog()
        }
        route_roles.update(
            {
                name: set(roles)
                for name, roles in harness_config.PROVIDER_ALLOWED_ROLES.items()
                if not roles
            }
        )
        for role, route_ids in (
            ("proposer", requested_proposers),
            ("refiner", requested_refiners),
            ("aggregator", [requested_aggregator]),
        ):
            disallowed = [
                route_id
                for route_id in route_ids
                if route_id in route_roles and role not in route_roles[route_id]
            ]
            if disallowed:
                names = ", ".join(disallowed)
                if disallowed == ["fable"]:
                    detail = "Fable models are aggregator-only"
                else:
                    detail = "the route is disabled or restricted to another role"
                raise LaunchError(
                    f"{names} {'is' if len(disallowed) == 1 else 'are'} "
                    f"not allowed in the {role} layer; {detail}"
                )
        options = dict(body.get("options")) if isinstance(body.get("options"), dict) else {}
        for internal in ("restart_count", "resume_checkpoints"):
            options.pop(internal, None)
        depth = options.get("depth", body.get("preset", "balanced"))
        if depth not in {"quick", "balanced", "thorough"}:
            raise LaunchError("Choose quick, balanced, or thorough depth.")
        profile_id = profile["id"]

        job_id = job_id or (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        )
        source_info = None
        source_mode = str(body.get("source_mode") or "").strip().lower()
        github_pointer = str(body.get("github_repository") or "").strip()
        if source_mode == "brief":
            workspace = brief_workspace_dir / job_id
            workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
            try:
                workspace.chmod(0o700)
            except OSError:
                pass
            source_info = {"type": "brief"}
        elif source_mode == "github" or github_pointer:
            git_ref = str(body.get("github_ref") or "").strip() or None
            try:
                owner, repo = parse_repo_pointer(github_pointer, self.github_owner)
                result = self.clone(
                    github_pointer,
                    github_workspace_dir,
                    git_ref=git_ref,
                    owner_allowlist=self.github_owner,
                )
            except (ValueError, GitHubWorkspaceError) as exc:
                raise LaunchError(
                    str(exc), 400 if isinstance(exc, ValueError) else 502
                ) from exc
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
                "commit": result.get("commit"),
                "fetched_at": result.get("fetched_at"),
                "remote_url": result["remote_url"],
            }
        else:
            try:
                workspace = _safe_workspace(
                    str(body.get("workspace") or ""),
                    self.workspace_roots,
                )
            except ValueError as exc:
                raise LaunchError(str(exc))

        attachment_ids = body.get("upload_ids")
        if attachment_ids is None:
            attachment_ids = body.get("attachments")
        upload_ids = _string_list(attachment_ids)
        selected_uploads = []
        for upload_id in upload_ids:
            upload = store.get_upload(upload_id)
            if not upload or upload.get("profile_id") != profile_id:
                raise LaunchError(f"upload not found: {upload_id}")
            source = Path(upload["stored_path"]).resolve()
            if not source.is_file() or upload_dir not in source.parents:
                raise LaunchError(f"upload is unavailable: {upload_id}", 409)
            selected_uploads.append((upload, source))

        session_dir = workspace / ".moa" / job_id
        session_dir.mkdir(parents=True, exist_ok=True)
        uploaded_files = []
        if selected_uploads:
            inputs_dir = session_dir / "inputs"
            inputs_dir.mkdir(exist_ok=True)
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
            "pipeline_version": 2,
            "depth": depth,
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
        if uploaded_files and prepared_context:
            # This argument is supplied only by the local Slack gateway, never
            # accepted from an HTTP payload. The worker verifies bytes/settings
            # against the cached signature before reusing extraction.
            scout["attachment_context"] = prepared_context
            (session_dir / "attachment-context.md").write_text(
                prepared_context["markdown"], encoding="utf-8"
            )
        (session_dir / "scout-brief.json").write_text(
            json.dumps(scout, indent=2), encoding="utf-8"
        )
        config = {
            "transport": body.get("transport", "browser"),
            "proposers": requested_proposers,
            "refiners": requested_refiners,
            "aggregator": requested_aggregator,
            "options": options,
            "upload_ids": upload_ids,
            "source": source_info or {"type": "local"},
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
        if source_info and source_info.get("commit"):
            store.append_event(
                job_id, "source", "Fresh GitHub snapshot pinned at " + source_info["commit"][:12],
                source_info,
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
        if self.wake:
            self.wake()
        return job
