"""Gemini CLI adapter.

Invokes `gemini -p` headlessly with --output-format json. Gemini's JSON
output mode wraps the model response in a `{"response": "<text>"}` shape;
the actual structured plan content is inside that text payload. We
extract the inner JSON and validate it against our schema in Python
since gemini-cli does not support arbitrary user-supplied schemas the
way codex does.

Subprocess isolation: each call gets its own TMPDIR. Gemini auth and
project state live in ~/.gemini/ which is shared across calls; the
orchestrator's flock prevents races.
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

from adapters import kill_proc_tree  # shared POSIX+Windows timeout handler


def _gemini_bin() -> str:
    """Binary name/path for gemini. Honors MOA_GEMINI_BIN env override."""
    return os.environ.get("MOA_GEMINI_BIN") or "gemini"


@dataclass
class GeminiResult:
    """Result of a single gemini invocation."""
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None


# Minimum gemini-cli version that supports `--approval-mode yolo` (the
# canonical unified approval flag that supersedes the legacy `--yolo`).
# Source: google-gemini/gemini-cli PR #4591. We use version detection as a
# safety net so a future migration to --approval-mode can fall back gracefully
# on older installs instead of failing with an opaque "unknown option" error.
_MIN_GEMINI_APPROVAL_MODE_VERSION = (0, 30, 0)


def _parse_semver(version_str: str) -> Optional[tuple[int, int, int]]:
    """Parse a semver string like '0.36.0' into a (major, minor, patch) tuple.

    Returns None if the string isn't a parseable semver. Tolerates leading
    'v' prefix ('v0.36.0' → (0, 36, 0)).
    """
    import re as _re
    m = _re.match(r"v?(\d+)\.(\d+)\.(\d+)", version_str.strip())
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except ValueError:
        return None


def _gemini_version() -> Optional[tuple[int, int, int]]:
    """Run `gemini --version` and parse the first line as semver.

    Returns None on any failure (binary missing, timeout, unparseable output)
    so callers can treat it as "unknown" rather than error.
    """
    try:
        proc = subprocess.run(
            [_gemini_bin(), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
    if not first_line:
        return None
    return _parse_semver(first_line[0])


def supports_approval_mode_flag() -> bool:
    """Return True if the installed gemini-cli supports `--approval-mode yolo`.

    Used by the adapter to pick between `--approval-mode yolo` (new, canonical)
    and `--yolo` (legacy, still works as of 0.36). Returns False on older
    installs so the adapter can fall back to the legacy flag safely.
    """
    version = _gemini_version()
    if version is None:
        return False  # unknown version → be conservative, use legacy flag
    return version >= _MIN_GEMINI_APPROVAL_MODE_VERSION


def check_available() -> tuple[bool, str]:
    """Verify gemini CLI is on PATH and authenticated.

    Returns (ok, message). The message includes the detected version when
    available so the orchestrator's preflight log shows it.
    """
    bin_name = _gemini_bin()
    if not shutil.which(bin_name):
        return False, (
            f"gemini CLI not found ({bin_name!r} not on PATH; "
            "install: npm i -g @google/gemini-cli, or set MOA_GEMINI_BIN)"
        )
    # Gemini stores auth state in ~/.gemini/. We check for the directory rather
    # than a specific file because the layout differs between auth methods
    # (OAuth vs API key vs Vertex AI).
    gemini_dir = Path.home() / ".gemini"
    if not gemini_dir.exists():
        return False, "gemini not authenticated (run: gemini, complete login)"

    version = _gemini_version()
    if version is None:
        return True, "ok (version unknown — `gemini --version` did not return parseable output)"

    version_str = ".".join(str(n) for n in version)
    min_str = ".".join(str(n) for n in _MIN_GEMINI_APPROVAL_MODE_VERSION)
    if version >= _MIN_GEMINI_APPROVAL_MODE_VERSION:
        return True, f"ok (v{version_str}, supports --approval-mode)"
    return True, (
        f"ok (v{version_str}; older than v{min_str} — orchestrator will use "
        f"legacy --yolo flag. Consider upgrading: npm i -g @google/gemini-cli)"
    )


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
        print(f"[gemini adapter] failed to write log {log_file}: {e}", file=_sys.stderr)


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str = os.environ.get("MOA_GEMINI_MODEL") or "gemini-2.5-pro",
    timeout_seconds: int = 900,
    log_file: Optional[Path] = None,
) -> GeminiResult:
    """Invoke gemini -p with the given prompt.

    Args:
        prompt: The full prompt text. Passed via -p flag.
        repo_path: Working directory. Gemini can read files inside this dir.
        model: Gemini model id. Default gemini-2.5-pro (override via
            MOA_GEMINI_MODEL env var). gemini-3.1-pro-preview is flaky.
        timeout_seconds: Hard wall-clock cap. Gemini with web search + file
            reads runs 60-300s; give it 900s for safety.
        log_file: Optional path to write the full gemini output to. ALWAYS
            written in every exit path so post-mortems never come up empty.

    Returns:
        GeminiResult with parsed inner payload (or None on failure).

    Note: Gemini does NOT support arbitrary --output-schema like codex does.
    The caller is responsible for validating the returned payload against
    the proposer/refiner schema. This adapter just extracts the inner JSON
    from gemini's response wrapper.

    Read-only discipline is enforced via the prompt body, not via a
    sandbox flag. gemini-cli has no --sandbox mode; --approval-mode plan
    would enforce read-only but blocks shell exec which defeats deep
    research. We use --yolo (full tool access) and rely on the prompt's
    explicit read-only rule.
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None

    try:
        tmpdir = tempfile.mkdtemp(prefix="moa-gemini-")
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["XDG_CACHE_HOME"] = str(Path(tmpdir) / "cache")
        # Prevent the subprocess from generating __pycache__/ and .pyc files
        # during execution.
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        # `--approval-mode yolo` is the canonical unified approval flag
        # introduced in gemini-cli PR #4591; `--yolo` remains as a legacy
        # fallback for older installs (< 0.30.0).
        if supports_approval_mode_flag():
            approval_args = ["--approval-mode", "yolo"]
        else:
            approval_args = ["--yolo"]

        # Prompt is sent via stdin, NOT as the value of -p. Refiner prompts
        # include the scout brief plus every proposer's full output (tens of
        # KB, sometimes >100KB) and can exceed ARG_MAX on macOS/Linux when
        # passed as an argv entry. Per gemini-cli --help, "-p, --prompt …
        # Appended to input on stdin (if any)." — so passing -p with an
        # empty string triggers headless mode and stdin supplies the body.
        cmd = [
            _gemini_bin(),
            "-m", model,
            *approval_args,  # auto-approve all tools; read-only enforced via prompt
            "--output-format", "json",
            "-p", "",  # empty value triggers headless mode; real prompt is on stdin
        ]

        try:
            # Use explicit Popen + killpg on timeout so we can tear down the
            # ENTIRE process group on timeout, not just the top-level gemini
            # binary. Gemini spawns web-fetch helpers and tool subprocesses
            # that would otherwise survive as orphans after a subprocess.run
            # timeout.
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(repo_path),
                start_new_session=True,  # isolate from parent signal group
            )
            try:
                stdout_text, stderr_text = proc.communicate(input=prompt, timeout=timeout_seconds)
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
                return GeminiResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds}s",
                )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            stderr_captured = f"gemini binary not found on PATH: {e}"
            return GeminiResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"gemini binary not found: {e}",
            )
        except OSError as e:
            duration = time.monotonic() - start
            stderr_captured = f"OSError launching gemini: {e}"
            return GeminiResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"OSError launching gemini: {e}",
            )

        if proc.returncode != 0:
            return GeminiResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=f"gemini exited with code {proc.returncode}",
            )

        payload = _extract_inner_json(stdout_captured)
        if payload is None:
            return GeminiResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=_diagnose_empty_response(stdout_captured, stderr_captured),
            )

        return GeminiResult(
            success=True, payload=payload, raw_stdout=stdout_captured,
            raw_stderr=stderr_captured, exit_code=0,
            duration_seconds=duration,
        )
    finally:
        _write_log_file(log_file, stdout_captured, stderr_captured)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _diagnose_empty_response(stdout: str, stderr: str) -> str:
    """Produce a specific error message when JSON extraction fails.

    The generic "could not extract inner JSON" message hides common root
    causes. Inspect stdout + stderr for known failure signatures and
    return the most informative message available.

    Signatures we recognize:
      * `response` field present but empty in the outer envelope (gemini
        formatting stage dropped the text — usually quota exhaustion on
        the utility model)
      * "exhausted your capacity" / "quota" / "rate limit" in stderr
      * authentication errors in stderr
      * stdout entirely empty
    """
    stderr_lower = (stderr or "").lower()
    quota_hit = any(
        phrase in stderr_lower
        for phrase in (
            "exhausted your capacity",
            "quota",
            "rate limit",
            "429",
        )
    )
    auth_hit = any(
        phrase in stderr_lower
        for phrase in (
            "unauthorized",
            "not authenticated",
            "401",
            "403",
            "invalid credentials",
        )
    )
    empty_response = False
    try:
        first_brace = (stdout or "").find("{")
        if first_brace >= 0:
            outer = json.loads(stdout[first_brace:])
            if isinstance(outer, dict) and isinstance(outer.get("response"), str):
                empty_response = outer["response"].strip() == ""
    except (json.JSONDecodeError, ValueError):
        pass

    if not stdout or not stdout.strip():
        return "gemini produced empty stdout (no envelope at all)"
    if empty_response and quota_hit:
        return (
            "gemini returned empty response field — utility-model quota "
            "exhausted during tool/formatting steps (see stderr for retry "
            "messages). Try re-running after quota reset, or reduce prompt "
            "complexity / research budget."
        )
    if empty_response:
        return (
            "gemini returned empty response field — the model ran but the CLI "
            "envelope stripped the output. Check stderr for auth, quota, or "
            "tool-loop errors."
        )
    if quota_hit:
        return (
            "gemini hit quota / rate-limit errors during the run "
            "(see stderr). Final envelope may be truncated or unparseable."
        )
    if auth_hit:
        return "gemini authentication error (see stderr). Re-run `gemini` interactively to re-auth."
    return "could not extract inner JSON from gemini response wrapper"


def _extract_inner_json(stdout: str) -> Optional[dict]:
    """Extract the inner JSON object from gemini --output-format json.

    Gemini's JSON output looks like:
        {"response": "<model text here>", "stats": {...}, ...}

    The actual structured proposer/refiner JSON we want is inside the
    "response" field, possibly wrapped in markdown code fences.
    """
    if not stdout:
        return None

    # Step 1: parse the outer wrapper
    outer = None
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        # Sometimes gemini emits leading log lines before the JSON. Find the
        # first { and try to parse from there.
        first_brace = stdout.find("{")
        if first_brace >= 0:
            try:
                outer = json.loads(stdout[first_brace:])
            except json.JSONDecodeError:
                pass

    inner_text = None
    if isinstance(outer, dict):
        # Common shapes across gemini-cli versions
        for key in ("response", "result", "text", "content", "message"):
            if key in outer and isinstance(outer[key], str):
                inner_text = outer[key]
                break
    if inner_text is None:
        # Fall back to the raw stdout if the wrapper shape was unexpected
        inner_text = stdout

    # Step 2: extract JSON from the inner text. May be in fences.
    candidates = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", inner_text, re.DOTALL):
        candidates.append(match.group(1).strip())

    # Step 3: also scan for bare top-level JSON objects in the inner text.
    # Cap at last 200KB — the schema payload is always near the end, and the
    # O(n²) brace-matching loop becomes unusably slow on large raw stdout.
    _MAX_BARE_SCAN = 200_000
    text = inner_text[-_MAX_BARE_SCAN:] if len(inner_text) > _MAX_BARE_SCAN else inner_text
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

    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue

    return None
