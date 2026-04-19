"""Codex CLI adapter.

Invokes `codex exec` headlessly with --output-schema for guaranteed JSON
shape. Codex is the only one of the three frontier CLIs that natively
enforces an arbitrary user-supplied JSON Schema on the model's final
output, which is why we don't need post-hoc validation here -- codex
already did it.

Subprocess isolation: each call gets its own TMPDIR via env override.
Codex auth lives in ~/.codex/auth.json which is shared across calls;
the orchestrator's flock prevents concurrent MoA invocations from racing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import kill_proc_tree  # shared POSIX+Windows timeout handler


@dataclass
class CodexResult:
    """Result of a single codex invocation."""
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None


def _codex_bin() -> str:
    """Binary name/path for codex. Honors MOA_CODEX_BIN env override."""
    return os.environ.get("MOA_CODEX_BIN") or "codex"


def check_available() -> tuple[bool, str]:
    """Verify codex CLI is on PATH and authenticated."""
    bin_name = _codex_bin()
    if not shutil.which(bin_name):
        return False, (
            f"codex CLI not found ({bin_name!r} not on PATH; "
            "install: npm i -g @openai/codex, or set MOA_CODEX_BIN)"
        )
    auth_file = Path.home() / ".codex" / "auth.json"
    if not auth_file.exists():
        return False, "codex not authenticated (run: codex login)"
    return True, "ok"


def _write_log_file(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    """Write the adapter's captured output to disk, swallowing IO errors.

    Called from the finally block of run(), so it must never raise -- any
    IO failure while writing the log is printed to stderr and ignored so the
    orchestrator sees the real result instead of a write failure masking it.
    """
    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )
    except OSError as e:
        import sys as _sys
        print(f"[codex adapter] failed to write log {log_file}: {e}", file=_sys.stderr)


def run(
    *,
    prompt: str,
    schema_path: Path,
    repo_path: Path,
    model: str = "gpt-5.4",
    reasoning_effort: str = "high",
    timeout_seconds: int = 900,
    log_file: Optional[Path] = None,
) -> CodexResult:
    """Invoke codex exec with the given prompt and schema.

    Args:
        prompt: The full prompt text. Sent via stdin to codex exec.
        schema_path: Path to JSON Schema file. Codex enforces it via --output-schema.
        repo_path: Working directory for codex (--cd). Codex can read repo from here.
        model: Codex model id. Default gpt-5.4.
        reasoning_effort: low | medium | high | xhigh. Default high. Dropped from
            xhigh in v0.2.2 after the first dogfood run showed xhigh + aggressive
            web research blew past 900s. xhigh is still available via the
            --codex-effort CLI flag for tasks where quality trumps latency
            (e.g. refinement-only runs where the proposer output already exists).
        timeout_seconds: Hard wall-clock cap. Default 900s.
        log_file: Optional path to write the full codex output to. ALWAYS
            written in every exit path so post-mortems never come up empty.

    Returns:
        CodexResult with parsed payload (or None on failure).
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None

    try:
        if not schema_path.exists():
            stderr_captured = f"Schema file not found: {schema_path}"
            return CodexResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=0.0, error_message="missing schema",
            )

        # Per-call TMPDIR for state isolation. Auth dir (~/.codex) is shared but
        # the orchestrator's flock prevents concurrent invocations.
        tmpdir = tempfile.mkdtemp(prefix="moa-codex-")
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["XDG_CACHE_HOME"] = str(Path(tmpdir) / "cache")
        # Prevent the subprocess from generating __pycache__/ and .pyc files
        # during execution.
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        cmd = [
            _codex_bin(),
            "--ask-for-approval", "never",  # never prompt; errors bubble up instead
            "exec",
            "--model", model,
            "-c", f"model_reasoning_effort={reasoning_effort}",
            "--sandbox", "read-only",  # hard filesystem guarantee: no writes
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--output-schema", str(schema_path),
            "-C", str(repo_path),
            "-",  # read prompt from stdin
        ]

        try:
            # Use explicit Popen + killpg on timeout so we can tear down the
            # ENTIRE process group on timeout, not just the top-level codex
            # binary. Codex spawns sandbox workers and web-fetch helpers that
            # would otherwise survive as orphans after a subprocess.run timeout.
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,  # isolate from parent signal group
            )
            try:
                stdout_text, stderr_text = proc.communicate(
                    input=prompt, timeout=timeout_seconds
                )
                duration = time.monotonic() - start
                stdout_captured = stdout_text or ""
                stderr_captured = stderr_text or ""
            except subprocess.TimeoutExpired:
                kill_proc_tree(proc)
                # Drain pipes to prevent deadlock (second communicate after kill)
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=5)
                    stdout_captured = stdout_text or ""
                    stderr_captured = (stderr_text or "") + f"\n[orchestrator] timeout after {timeout_seconds}s"
                except Exception:
                    stdout_captured = ""
                    stderr_captured = f"[orchestrator] timeout after {timeout_seconds}s; could not drain pipes"
                duration = time.monotonic() - start
                return CodexResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds}s",
                )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            stderr_captured = f"codex binary not found on PATH: {e}"
            return CodexResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"codex binary not found: {e}",
            )
        except OSError as e:
            duration = time.monotonic() - start
            stderr_captured = f"OSError launching codex: {e}"
            return CodexResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"OSError launching codex: {e}",
            )

        if proc.returncode != 0:
            return CodexResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=f"codex exited with code {proc.returncode}",
            )

        payload = _extract_json_payload(stdout_captured)
        if payload is None:
            return CodexResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message="could not extract JSON payload from codex stdout",
            )

        return CodexResult(
            success=True, payload=payload, raw_stdout=stdout_captured,
            raw_stderr=stderr_captured, exit_code=0,
            duration_seconds=duration,
        )
    finally:
        _write_log_file(log_file, stdout_captured, stderr_captured)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_json_payload(stdout: str) -> Optional[dict]:
    """Extract the final JSON object from codex stdout.

    codex exec emits framing lines (workdir, model, session id, etc.), then
    the model's reasoning, then the final 'codex' output. With --output-schema
    set, the final assistant message is the validated JSON. We find the last
    well-formed JSON object in the stream and return it.
    """
    if not stdout:
        return None

    # Strategy: try to find a fenced JSON block first; otherwise scan for the
    # last balanced { ... } that parses successfully.
    # Codex with --output-schema typically emits the JSON as the final block
    # without fences, so the second strategy is the common path.

    candidates = []

    # 1. Fenced JSON blocks
    import re
    for match in re.finditer(r"```json\s*\n(.*?)\n```", stdout, re.DOTALL):
        candidates.append(match.group(1).strip())

    # 2. Bare top-level JSON objects: walk from end, find balanced braces
    # Find every '{' position; for each, try to parse from there to the end
    # and from there to every subsequent '}'. This is O(n^2) worst case but
    # codex output is bounded.
    text = stdout
    for start in range(len(text)):
        if text[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for end in range(start, len(text)):
            ch = text[end]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : end + 1])
                    break

    # Try candidates from longest to shortest (assume the schema-conformant
    # output is the largest JSON object in the stream)
    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue

    return None
