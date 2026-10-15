"""Allowlisted GitHub workspace discovery and cloning through authenticated gh."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ALLOWED_OWNER = "drivelineresearch"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class GitHubWorkspaceError(RuntimeError):
    pass


def _validate_owner(value: str) -> str:
    owner = value.strip()
    if not OWNER_RE.fullmatch(owner) or owner.startswith("-") or owner.endswith("-"):
        raise GitHubWorkspaceError(
            "MOA_WEBUI_GITHUB_OWNER must be one GitHub user or organization name"
        )
    return owner


def allowed_owner() -> str:
    """Return the single GitHub owner explicitly allowed by this server."""
    return _validate_owner(
        os.environ.get("MOA_WEBUI_GITHUB_OWNER") or DEFAULT_ALLOWED_OWNER
    )


def parse_repo_pointer(
    value: str, owner_allowlist: str | None = None
) -> tuple[str, str]:
    """Accept only the exact allowlisted owner/name pointer shape."""
    allowed = (
        _validate_owner(owner_allowlist)
        if owner_allowlist is not None
        else allowed_owner()
    )
    parts = value.strip().split("/")
    if (
        len(parts) != 2
        or parts[0] != allowed
        or not REPO_RE.fullmatch(parts[1])
        or parts[1] in {".", ".."}
    ):
        raise ValueError(
            f"repo must be an exact {allowed}/<repository> pointer"
        )
    return parts[0], parts[1]


def validate_git_ref(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    ref = value.strip()
    if (
        not REF_RE.fullmatch(ref)
        or ".." in ref
        or "@{" in ref
        or "//" in ref
        or ref.endswith(("/", ".", ".lock"))
    ):
        raise ValueError("github_ref is not a safe branch or tag name")
    return ref


def _gh_bin() -> str:
    return os.environ.get("MOA_WEBUI_GH_BIN") or "gh"


def _run_gh(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    binary = _gh_bin()
    if not shutil.which(binary):
        raise GitHubWorkspaceError("GitHub CLI is not installed")
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubWorkspaceError("GitHub CLI request timed out") from exc
    except OSError as exc:
        raise GitHubWorkspaceError(f"Could not launch GitHub CLI: {exc}") from exc


def list_repositories(owner_allowlist: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    owner = (
        _validate_owner(owner_allowlist)
        if owner_allowlist is not None
        else allowed_owner()
    )
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("repository discovery limit must be between 1 and 10000")
    proc = _run_gh(
        [
            "repo",
            "list",
            owner,
            "--limit",
            str(limit),
            "--json",
            "name,nameWithOwner,description,isPrivate,url,pushedAt,defaultBranchRef",
        ],
        timeout=30,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output").strip().splitlines()[0]
        raise GitHubWorkspaceError(f"GitHub repository listing failed: {detail}")
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GitHubWorkspaceError("GitHub CLI returned invalid repository data") from exc
    if not isinstance(rows, list):
        raise GitHubWorkspaceError("GitHub CLI returned an unexpected repository list")
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("nameWithOwner") == f"{owner}/{row.get('name')}"
        and REPO_RE.fullmatch(str(row.get("name") or ""))
    ]


def clone_repository(
    pointer: str,
    workspace_root: Path,
    *,
    git_ref: str | None = None,
    owner_allowlist: str | None = None,
) -> dict[str, Any]:
    """Fetch a new private snapshot; never reuse or mutate a prior run's tree."""
    owner, repo = parse_repo_pointer(pointer, owner_allowlist)
    git_ref = validate_git_ref(git_ref)
    root = workspace_root.resolve()
    owner_root = root / "snapshots" / owner / repo
    try:
        for directory in (root, root / "snapshots", owner_root.parent, owner_root):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        temporary = Path(tempfile.mkdtemp(prefix=f".{repo}-", dir=owner_root))
    except OSError as exc:
        raise GitHubWorkspaceError("Could not create a private GitHub snapshot") from exc
    try:
        args = [
            "repo",
            "clone",
            f"{owner}/{repo}",
            str(temporary),
            "--",
            "--depth=1",
        ]
        if git_ref:
            args.extend(["--branch", git_ref])
        proc = _run_gh(args, timeout=180)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "no output").strip().splitlines()[0]
            raise GitHubWorkspaceError(f"GitHub clone failed: {detail}")
        if not (temporary / ".git").is_dir():
            raise GitHubWorkspaceError("GitHub clone completed without a Git workspace")
        try:
            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", "-C", str(temporary), *args],
                    capture_output=True, text=True, timeout=30, shell=False,
                )
                if result.returncode:
                    raise GitHubWorkspaceError("Could not pin the fetched GitHub snapshot")
                return result.stdout.strip()

            commit = git("rev-parse", "HEAD")
            if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
                raise GitHubWorkspaceError("GitHub clone returned an invalid commit")
            resolved_ref = git_ref or git("branch", "--show-current")
            git("checkout", "--detach", "--quiet", commit)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubWorkspaceError("Could not pin the fetched GitHub snapshot") from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        target = owner_root / (commit[:12] + "-" + uuid.uuid4().hex[:12])
        temporary.replace(target)
    except OSError as exc:
        raise GitHubWorkspaceError("Could not prepare the fetched GitHub snapshot") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "owner": owner,
        "repo": repo,
        "path": str(target),
        "remote_url": f"https://github.com/{owner}/{repo}",
        "created": True,
        "git_ref": resolved_ref,
        "commit": commit,
        "fetched_at": fetched_at,
    }
