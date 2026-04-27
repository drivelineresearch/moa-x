"""Cursor CLI adapter (multi-lab via cursor-agent).

Invokes `cursor-agent -p` headlessly with --output-format json. Cursor's
JSON envelope is structurally identical to claude-cli's outer envelope
without --json-schema set:

    {"type": "result", "is_error": false, "result": "<MODEL TEXT>",
     "usage": {"inputTokens": ..., "outputTokens": ...}, ...}

Cursor has no native --output-schema equivalent, so the orchestrator
validates the parsed payload against the proposer/refiner schema
Python-side (gemini-style). The adapter just extracts the inner JSON
from the `result` text.

Subprocess isolation: each call gets its own TMPDIR via env override.
Cursor session/auth state lives under ~/.cursor/ which is shared
across calls; the orchestrator's flock prevents concurrent MoA
invocations from racing on it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import READ_ONLY_RULE, kill_proc_tree


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


def _cursor_bin() -> str:
    """Binary name/path for cursor-agent. Honors MOA_CURSOR_BIN env override."""
    return os.environ.get("MOA_CURSOR_BIN") or "cursor-agent"


def check_available() -> tuple[bool, str]:
    """Verify cursor-agent CLI is on PATH and authenticated.

    Cursor stores subscription auth under ~/.cursor/. API-billed mode uses
    CURSOR_API_KEY in the environment. Either is acceptable.
    """
    bin_name = _cursor_bin()
    if not shutil.which(bin_name):
        return False, (
            f"cursor-agent CLI not found ({bin_name!r} not on PATH; "
            "install: curl https://cursor.com/install -fsS | bash, "
            "or set MOA_CURSOR_BIN)"
        )
    cursor_dir = Path.home() / ".cursor"
    has_subscription = cursor_dir.exists()
    has_api_key = bool(os.environ.get("CURSOR_API_KEY"))
    if not (has_subscription or has_api_key):
        return False, (
            "cursor-agent not authenticated (run: cursor-agent login, "
            "or set CURSOR_API_KEY)"
        )
    auth_mode = "subscription" if has_subscription else "API-billed"
    return True, f"ok ({auth_mode})"


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
        prompt: The full prompt text. Read-only directive is prepended.
        repo_path: Working directory; passed via Popen cwd=.
        model: Model id (e.g. "gpt-5.5", "claude-sonnet-4-6", "grok-4.20").
        timeout_seconds: Hard wall-clock cap. Default 1200s, matching siblings.
        log_file: Optional path to write the full cursor output. ALWAYS
            written in every exit path so post-mortems never come up empty.

    Returns:
        CursorResult with parsed inner payload (or None on failure).

    Note: Cursor has no --output-schema flag. Schema validation happens
    orchestrator-side after this returns, same as gemini.
    """
    raise NotImplementedError("Implemented in Phase 4.3")


def _extract_payload(stdout: str) -> Optional[dict]:
    """Stub — implemented in Phase 4.2."""
    raise NotImplementedError("Implemented in Phase 4.2")
