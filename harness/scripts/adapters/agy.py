"""Antigravity (``agy``) CLI adapter.

AGY is the preferred Google consumer-account path. Authentication belongs to
the CLI and is deliberately not copied into MoA-X: subprocesses inherit the
same account state as an interactive ``agy`` invocation on this machine.

The read-only contract is fail closed. Every invocation includes both
``--mode plan`` and ``--sandbox``. Large MoA prompts are written beneath the
active session and the argv contains only a short instruction pointing at the
file, avoiding ARG_MAX failures.
"""
from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from adapters import READ_ONLY_RULE, extract_json_from_text, kill_proc_tree

_MIN_VERSION = (1, 1, 5)
_VERSION_RE = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")
_REQUIRED_HELP_FLAGS = (
    "--print",
    "--mode",
    "--sandbox",
    "--model",
    "--dangerously-skip-permissions",
)


class AgyWorkspaceIsolationError(RuntimeError):
    """Raised when a Git workspace cannot be mirrored safely for AGY."""


@dataclass
class AgyResult:
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None
    transient_empty: bool = False


def _agy_bin() -> str:
    return os.environ.get("MOA_AGY_BIN") or "agy"


def _parse_version(text: str) -> Optional[tuple[int, int, int]]:
    match = _VERSION_RE.search(text)
    return tuple(int(part) for part in match.groups()) if match else None


def list_models(*, timeout_seconds: int = 20) -> tuple[bool, list[str], str]:
    """Return the stable model slugs visible to the current AGY account."""
    bin_name = _agy_bin()
    if not shutil.which(bin_name):
        return False, [], f"agy CLI not found ({bin_name!r} not on PATH)"
    try:
        proc = subprocess.run(
            [bin_name, "models"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, [], f"agy models probe failed: {exc}"
    models = [
        line.strip()
        for line in (proc.stdout or "").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "Usage:"))
    ]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output").strip().splitlines()[0]
        return False, [], f"agy models exited {proc.returncode}: {detail}"
    if not models:
        return False, [], "agy models returned no model slugs (account may not be eligible)"
    return True, models, f"{len(models)} models available"


def check_available() -> tuple[bool, str]:
    """Verify AGY, required safety flags, version, and persisted account state."""
    bin_name = _agy_bin()
    if not shutil.which(bin_name):
        return False, (
            f"agy CLI not found ({bin_name!r} not on PATH; install Antigravity "
            "CLI and sign in with the account you want MoA-X to use)"
        )
    try:
        version_proc = subprocess.run(
            [bin_name, "--version"], capture_output=True, text=True, timeout=10
        )
        help_proc = subprocess.run(
            [bin_name, "--help"], capture_output=True, text=True, timeout=10
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, f"agy capability probe failed: {exc}"
    version_text = (version_proc.stdout or "") + (version_proc.stderr or "")
    version = _parse_version(version_text)
    if version_proc.returncode != 0 or version is None:
        return False, f"could not determine agy version: {version_text.strip() or 'no output'}"
    if version < _MIN_VERSION:
        return False, (
            f"agy {'.'.join(map(str, version))} is too old; need 1.1.5+ for "
            "stable model slugs and reliable headless output"
        )
    help_text = (help_proc.stdout or "") + (help_proc.stderr or "")
    missing = [flag for flag in _REQUIRED_HELP_FLAGS if flag not in help_text]
    if help_proc.returncode != 0 or missing:
        return False, f"agy lacks required safe headless capabilities: {missing}"
    models_ok, models, detail = list_models()
    if not models_ok:
        return False, detail
    return True, (
        f"agy {'.'.join(map(str, version))}; persisted account ready; "
        f"{len(models)} model(s)"
    )


def _build_cmd(
    bin_name: str,
    *,
    instruction: str,
    model: str,
    timeout_seconds: int,
    internal_log: Path,
    reasoning_effort: Optional[str] = None,
) -> list[str]:
    """Build the AGY command. Safety flags are intentionally unconditional."""
    cmd = [
        bin_name,
        "--print", instruction,
        "--mode", "plan",
        "--sandbox",
        "--dangerously-skip-permissions",
        "--model", model,
        "--print-timeout", f"{timeout_seconds}s",
        "--log-file", str(internal_log),
    ]
    # AGY's currently available model ids encode depth (``-low``, ``-medium``,
    # or ``-high``). Supplying --effort as well makes the CLI reject a model
    # selection when the two disagree, so never forward a separate flag.
    # Keep the parameter for the common adapter interface and old callers.
    return cmd


def _write_capture(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _copy_untracked_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_file():
        shutil.copy2(source, target)


@contextmanager
def _isolated_git_workspace(
    repo_path: Path,
    session_dir: Path,
) -> Iterator[Path]:
    """Mirror Git-visible state into a disposable AGY worktree.

    AGY's terminal sandbox can bootstrap commands through ``uv run``. In a
    project without a lockfile, that bootstrap may create ``uv.lock`` and
    ``.venv`` before the command sandbox is fully established. A detached
    worktree contains those side effects while retaining tracked, staged,
    dirty, and untracked context from the operator's working tree.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        # Task-only Web UI workspaces are intentionally not Git repositories.
        # AGY still has plan mode, its native sandbox, and UV_NO_SYNC there.
        yield repo_path
        return

    git_root = Path(probe.stdout.strip()).resolve()
    try:
        repo_relative = repo_path.resolve().relative_to(git_root)
    except ValueError as exc:
        raise AgyWorkspaceIsolationError(
            f"repository path {repo_path} is outside Git root {git_root}"
        ) from exc

    sandbox_parent = session_dir / "sandboxes"
    sandbox_parent.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix="agy-worktree-", dir=sandbox_parent))
    # git worktree accepts a missing or empty target. Removing the empty
    # mkdtemp directory lets Git own its complete lifecycle.
    sandbox.rmdir()
    registered = False
    try:
        add = subprocess.run(
            [
                "git", "worktree", "add", "--detach", "--force",
                str(sandbox), "HEAD",
            ],
            cwd=git_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if add.returncode != 0:
            detail = (add.stderr or add.stdout).decode("utf-8", "replace").strip()
            raise AgyWorkspaceIsolationError(
                f"could not create disposable AGY worktree: {detail[:300]}"
            )
        registered = True

        diff = subprocess.run(
            ["git", "diff", "--binary", "--full-index", "HEAD", "--", "."],
            cwd=git_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if diff.returncode != 0:
            raise AgyWorkspaceIsolationError(
                "could not capture dirty tracked state for AGY isolation"
            )
        if diff.stdout:
            apply = subprocess.run(
                ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                cwd=sandbox,
                input=diff.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
            if apply.returncode != 0:
                detail = apply.stderr.decode("utf-8", "replace").strip()
                raise AgyWorkspaceIsolationError(
                    f"could not mirror dirty tracked state for AGY: {detail[:300]}"
                )

        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=git_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if untracked.returncode != 0:
            raise AgyWorkspaceIsolationError(
                "could not enumerate untracked state for AGY isolation"
            )
        try:
            session_relative = session_dir.resolve().relative_to(git_root).as_posix()
        except ValueError:
            session_relative = ""
        for raw in untracked.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", "surrogateescape")
            if ".moa" in Path(relative).parts:
                continue
            if session_relative and (
                relative == session_relative
                or relative.startswith(session_relative.rstrip("/") + "/")
            ):
                continue
            _copy_untracked_file(git_root / relative, sandbox / relative)

        yield sandbox / repo_relative
    finally:
        if registered:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(sandbox)],
                cwd=git_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=git_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str,
    timeout_seconds: int = 1200,
    log_file: Optional[Path] = None,
    prompt_file: Optional[Path] = None,
    schema_path: Optional[Path] = None,
    reasoning_effort: Optional[str] = None,
) -> AgyResult:
    """Run AGY headlessly using the CLI's already-authenticated local account."""
    started = time.monotonic()
    stdout = ""
    stderr = ""
    scratch_dir: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if prompt_file is None:
            if log_file is not None:
                prompt_file = log_file.parents[1] / "prompts" / f"{log_file.stem}.md"
            else:
                scratch_dir = tempfile.TemporaryDirectory(prefix="moa-agy-")
                prompt_file = Path(scratch_dir.name) / "prompt.md"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(READ_ONLY_RULE + "\n\n" + prompt, encoding="utf-8")

        internal_log = (
            log_file.with_name(log_file.stem + "-agy.log")
            if log_file is not None
            else prompt_file.with_suffix(".agy.log")
        )
        internal_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            session_dir = log_file.parents[1] if log_file is not None else prompt_file.parent
            with _isolated_git_workspace(repo_path, session_dir) as isolated_repo:
                active_prompt = prompt_file
                if isolated_repo.resolve() != repo_path.resolve():
                    active_prompt = isolated_repo / ".moa-agent-input" / prompt_file.name
                    active_prompt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(prompt_file, active_prompt)
                instruction = (
                    f"Read the complete task from {active_prompt.resolve()}. "
                    "Treat that file as instructions, inspect the current "
                    "repository read-only, and return only the requested JSON "
                    "object. Do not modify files."
                )
                cmd = _build_cmd(
                    _agy_bin(),
                    instruction=instruction,
                    model=model,
                    timeout_seconds=timeout_seconds,
                    internal_log=internal_log,
                    reasoning_effort=reasoning_effort,
                )
                child_env = os.environ.copy()
                # Defense in depth for AGY's terminal sandbox bootstrap. The
                # disposable worktree is the primary containment boundary.
                child_env["UV_NO_SYNC"] = "1"
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(isolated_repo),
                    env=child_env,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = proc.communicate(timeout=timeout_seconds + 15)
                except subprocess.TimeoutExpired:
                    kill_proc_tree(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=5)
                    except Exception:
                        stdout, stderr = "", ""
                    stderr = (stderr or "") + f"\n[orchestrator] timeout after {timeout_seconds}s"
                    return AgyResult(
                        False, None, stdout or "", stderr, -1,
                        time.monotonic() - started,
                        f"timeout after {timeout_seconds}s",
                    )
        except (AgyWorkspaceIsolationError, FileNotFoundError, OSError) as exc:
            return AgyResult(
                False, None, "", str(exc), -1, time.monotonic() - started,
                f"could not launch agy: {exc}",
            )

        stdout = stdout or ""
        stderr = stderr or ""
        if proc.returncode != 0:
            detail = (stderr or stdout or "no output").strip()
            return AgyResult(
                False, None, stdout, stderr, proc.returncode,
                time.monotonic() - started,
                f"agy exited with code {proc.returncode}: {detail[:300]}",
            )
        required_keys: Optional[set[str]] = None
        if schema_path is not None:
            try:
                required_keys = set(
                    json.loads(schema_path.read_text(encoding="utf-8")).get("required") or []
                )
            except (OSError, json.JSONDecodeError):
                required_keys = None
        payload = extract_json_from_text(stdout, required_keys=required_keys)
        if payload is None:
            empty = not stdout.strip()
            return AgyResult(
                False, None, stdout, stderr, proc.returncode,
                time.monotonic() - started,
                "agy returned empty output" if empty else "could not extract JSON object from agy output",
                transient_empty=empty,
            )
        return AgyResult(
            True, payload, stdout, stderr, proc.returncode,
            time.monotonic() - started,
        )
    finally:
        _write_capture(log_file, stdout, stderr)
        if scratch_dir is not None:
            scratch_dir.cleanup()
