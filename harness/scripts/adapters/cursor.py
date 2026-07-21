"""Cursor CLI adapter (multi-lab via cursor-agent).

Invokes `cursor-agent -p --mode plan` headlessly with
--output-format json. Cursor's JSON envelope is structurally identical
to claude-cli's outer envelope without --json-schema set:

    {"type": "result", "is_error": false, "result": "<MODEL TEXT>",
     "usage": {"inputTokens": ..., "outputTokens": ...}, ...}

Read-only discipline is enforced at the prompt level via the shared
READ_ONLY_RULE prepended to the prompt, the same way the claude/opencode
adapters do it. (Current cursor-agent removed the `--mode plan` flag that
previously gave a CLI-level guarantee.) See docs/cursor.md.

Cursor has no --output-schema equivalent (codex-style hard schema
enforcement), so the orchestrator validates the parsed payload against
the proposer/refiner schema Python-side. The adapter just extracts the
inner JSON from the `result` text via the shared extract_json_from_text.

Subprocess isolation: each call gets its own TMPDIR via env override.
Cursor session/auth state lives under ~/.cursor/ which is shared
across calls; the orchestrator's flock prevents concurrent MoA
invocations from racing on it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import READ_ONLY_RULE, extract_json_from_text, kill_proc_tree


@dataclass
class CursorResult:
    """Result of a single cursor-agent invocation."""
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None
    # True when the failure looks like a transient empty-envelope: cursor
    # returned subtype:success / is_error:false but result was empty, and
    # neither stderr nor envelope showed quota/auth signal. This pattern is
    # recoverable by re-dispatch; the orchestrator surfaces it to the user.
    transient_empty: bool = False


def _cursor_bin() -> str:
    """Binary name/path for the Cursor CLI.

    Honors MOA_CURSOR_BIN when set. Otherwise probes PATH: the CLI shipped as
    `cursor-agent` and was later renamed to `agent`, so we prefer `cursor-agent`
    (still aliased on most installs) and fall back to `agent`. NOTE: the bare
    `cursor` binary is the IDE launcher, not the headless agent — never use it.
    Falls back to the `cursor-agent` name so the not-found error stays coherent.
    """
    override = os.environ.get("MOA_CURSOR_BIN")
    if override:
        return override
    for candidate in ("cursor-agent", "agent"):
        if shutil.which(candidate):
            return candidate
    return "cursor-agent"


def check_available() -> tuple[bool, str]:
    """Verify cursor-agent CLI is on PATH and authenticated.

    Subscription auth lives under ~/.cursor/; API-billed mode uses
    CURSOR_API_KEY. Both pass through `cursor-agent whoami`, which
    exits 0 with the user's identity when authenticated. We use whoami
    as the real auth probe so stale tokens / expired sessions surface
    here instead of N seconds into a real call.
    """
    bin_name = _cursor_bin()
    if not shutil.which(bin_name):
        return False, (
            f"cursor-agent CLI not found ({bin_name!r} not on PATH; "
            "install: curl https://cursor.com/install -fsS | bash, "
            "or set MOA_CURSOR_BIN)"
        )

    try:
        proc = subprocess.run(
            [bin_name, "whoami"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, f"cursor-agent whoami probe failed: {e}"

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        first_line = msg[0] if msg else "(no output)"
        return False, (
            f"cursor-agent not authenticated ({first_line}; "
            "run: cursor-agent login, or set CURSOR_API_KEY)"
        )

    has_api_key = bool(os.environ.get("CURSOR_API_KEY"))
    auth_mode = "API-billed" if has_api_key else "subscription"
    identity = (proc.stdout or "").strip().splitlines()
    detail = identity[0] if identity else f"ok ({auth_mode})"
    return True, f"{detail} ({auth_mode})"


def _write_log_file(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    """Write the adapter's captured output to disk, swallowing IO errors.

    Called from the finally block of run(), so it must never raise --
    any IO failure while writing the log is logged to stderr and ignored.
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
        print(f"[cursor adapter] failed to write log {log_file}: {e}", file=_sys.stderr)


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str,
    timeout_seconds: int = 1200,
    log_file: Optional[Path] = None,
) -> CursorResult:
    """Invoke cursor-agent -p with the given prompt.

    Args:
        prompt: The full prompt text. Read-only enforcement is via the
            shared READ_ONLY_RULE prepended to the stdin prompt (current
            cursor-agent removed --mode plan).
        repo_path: Working directory; passed via Popen cwd=.
        model: Model id (e.g. "gpt-5.5", "claude-sonnet-4-6", "grok-4.20").
        timeout_seconds: Hard wall-clock cap. Default 1200s, matching siblings.
        log_file: Optional path to write the full cursor output. ALWAYS
            written in every exit path so post-mortems never come up empty.

    Returns:
        CursorResult with parsed inner payload (or None on failure).

    Note: Cursor has no --output-schema flag. Schema validation happens
    orchestrator-side after this returns.
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None

    try:
        tmpdir = tempfile.mkdtemp(prefix="moa-cursor-")
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["XDG_CACHE_HOME"] = str(Path(tmpdir) / "cache")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        # Current cursor-agent (>=2025.10) removed `--mode plan` — passing it now
        # exits 1 with "unknown option '--mode'", which broke the whole cursor
        # harness. `--trust` is retained: it bypasses the interactive
        # workspace-trust prompt that otherwise aborts a headless run in an
        # untrusted directory. With plan mode gone, read-only discipline is
        # enforced at the prompt level via the shared READ_ONLY_RULE (prepended
        # to the stdin prompt below), the same way the claude/opencode adapters
        # do it.
        #
        # Prompt is sent via stdin, NOT as a positional argv entry. Refiner
        # prompts include the scout brief plus every proposer's full output
        # (tens of KB) and can exceed ARG_MAX on macOS/Linux. cursor-agent
        # reads stdin when no positional prompt is given. Codex does the
        # same; opencode can't (no stdin) so it takes the prompt by file.
        cmd = [
            _cursor_bin(),
            "-p",
            "--model", model,
            "--output-format", "json",
            "--trust",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(repo_path),
                start_new_session=True,
            )
            try:
                stdout_text, stderr_text = proc.communicate(
                    input=READ_ONLY_RULE + "\n\n" + prompt, timeout=timeout_seconds)
                duration = time.monotonic() - start
                stdout_captured = stdout_text or ""
                stderr_captured = stderr_text or ""
            except subprocess.TimeoutExpired:
                kill_proc_tree(proc)
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=5)
                    stdout_captured = stdout_text or ""
                    stderr_captured = (stderr_text or "") + f"\n[orchestrator] timeout after {timeout_seconds}s"
                except Exception:
                    stdout_captured = ""
                    stderr_captured = f"[orchestrator] timeout after {timeout_seconds}s; could not drain pipes"
                duration = time.monotonic() - start
                return CursorResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds}s",
                )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            stderr_captured = f"cursor-agent binary not found on PATH: {e}"
            return CursorResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"cursor-agent binary not found: {e}",
            )
        except OSError as e:
            duration = time.monotonic() - start
            stderr_captured = f"OSError launching cursor-agent: {e}"
            return CursorResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"OSError launching cursor-agent: {e}",
            )

        if proc.returncode != 0:
            return CursorResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=f"cursor-agent exited with code {proc.returncode}",
            )

        # Surface in-envelope errors specifically before generic extract.
        envelope_error = _envelope_error_message(stdout_captured)
        if envelope_error is not None:
            return CursorResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=envelope_error,
            )

        payload = _extract_payload(stdout_captured)
        if payload is None:
            msg, transient = _diagnose_failure(stdout_captured, stderr_captured)
            return CursorResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=msg,
                transient_empty=transient,
            )

        return CursorResult(
            success=True, payload=payload, raw_stdout=stdout_captured,
            raw_stderr=stderr_captured, exit_code=0,
            duration_seconds=duration,
        )
    finally:
        _write_log_file(log_file, stdout_captured, stderr_captured)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _envelope_error_message(stdout: str) -> Optional[str]:
    """Return a user-facing message if the outer envelope reports is_error.

    Returns None when the envelope is fine (or unparseable — let the
    generic _diagnose_failure path handle that).
    """
    if not stdout:
        return None
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        first_brace = stdout.find("{")
        if first_brace < 0:
            return None
        try:
            outer = json.loads(stdout[first_brace:])
        except json.JSONDecodeError:
            return None
    if not isinstance(outer, dict) or not outer.get("is_error"):
        return None
    msg = outer.get("result") or outer.get("error") or "(no message in envelope)"
    return f"cursor-agent reported is_error: {msg}"


def _diagnose_failure(stdout: str, stderr: str) -> tuple[str, bool]:
    """Diagnose why _extract_payload failed. Returns (message, transient_empty).

    transient_empty=True only when:
      * envelope parsed cleanly with success semantics (no is_error)
      * `result` text is empty / whitespace
      * stderr shows no quota / auth / rate-limit signal

    Empirically this is the dominant cursor-agent flake: the run reports
    success but yields no model output. A single retry recovers cleanly.
    """
    stderr_lower = (stderr or "").lower()
    quota_hit = any(p in stderr_lower for p in ("rate limit", "quota", "429", "exceeded"))
    auth_hit = any(p in stderr_lower for p in ("unauthorized", "401", "invalid api key", "not authenticated"))
    if not stdout or not stdout.strip():
        return "cursor-agent produced empty stdout", False
    if quota_hit:
        return ("cursor-agent hit rate-limit / quota errors (see stderr). "
                "Check your Cursor subscription dashboard or CURSOR_API_KEY budget."), False
    if auth_hit:
        return ("cursor-agent authentication error (see stderr). "
                "Re-run `cursor-agent login` or set CURSOR_API_KEY."), False

    envelope_result = _envelope_result_text(stdout)
    if envelope_result is not None and not envelope_result.strip():
        return (
            "cursor-agent returned empty result text under a success envelope "
            "(no quota or auth signal). Likely transient — re-dispatch typically recovers."
        ), True

    return "could not extract payload from cursor-agent result text", False


def _envelope_result_text(stdout: str) -> Optional[str]:
    """Return the `result` field text from the cursor envelope, or None.

    None means the envelope is unparseable or doesn't carry a string `result`
    field. An empty string ("") is meaningful: the envelope parsed but the
    model produced no output (the transient pattern).
    """
    if not stdout:
        return None
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        first_brace = stdout.find("{")
        if first_brace < 0:
            return None
        try:
            outer = json.loads(stdout[first_brace:])
        except json.JSONDecodeError:
            return None
    if not isinstance(outer, dict):
        return None
    result = outer.get("result")
    return result if isinstance(result, str) else None


def _extract_payload(stdout: str) -> Optional[dict]:
    """Extract the inner JSON object from cursor-agent --output-format json.

    Cursor's outer envelope:
        {"type": "result", "is_error": <bool>, "result": "<text>",
         "usage": {...}, "session_id": "...", ...}

    When `is_error: true`, returns None — the orchestrator will surface
    the result text as the error message via run()'s caller.

    Otherwise extracts JSON from `result`, which may contain:
      - bare JSON (preferred — when prompt instructs no fences)
      - fenced JSON (```json ... ```)
      - JSON with surrounding prose
    """
    if not stdout:
        return None

    # Step 1: parse the outer wrapper
    outer = None
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        # Sometimes cursor emits leading log lines before the JSON
        first_brace = stdout.find("{")
        if first_brace >= 0:
            try:
                outer = json.loads(stdout[first_brace:])
            except json.JSONDecodeError:
                pass

    if not isinstance(outer, dict):
        return None
    if outer.get("is_error"):
        return None

    result_text = outer.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        return None

    # Step 2: extract the JSON payload from the inner text (fences or a bare
    # balanced object, longest-first). Shared with the opencode adapter.
    return extract_json_from_text(result_text)
