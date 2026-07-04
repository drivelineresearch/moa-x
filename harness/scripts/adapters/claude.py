"""Claude Code CLI adapter (Sonnet proposer/refiner).

Invokes `claude -p` headlessly with --json-schema for guaranteed JSON
shape. Claude Code supports arbitrary JSON Schema enforcement natively
via the --json-schema flag, which writes the validated object to
`.structured_output` in the outer result envelope. This makes parsing
cleaner than the cursor/opencode adapters (where we strip fences) and on par with codex
(which uses --output-schema).

Key differences from the other adapters:
- Read-only discipline is enforced via --append-system-prompt text, not
  via a filesystem sandbox. Claude Code has no sandbox flag; the contract
  is "the model has been told not to write, and we trust the model".
- --bare mode is NOT usable here because it strictly requires
  ANTHROPIC_API_KEY (OAuth / keychain auth is ignored in bare mode).
  We use full mode + --dangerously-skip-permissions instead.
- Claude Code emits an outer JSON envelope that includes:
    {
      "type": "result",
      "result": "<free-text response>",
      "structured_output": {...},  # only when --json-schema was set
      "session_id": "...",
      "usage": {...},
      "modelUsage": {...},
      ...
    }
  When --json-schema is set, `structured_output` is the validated object
  and `result` is typically empty. When --json-schema is NOT set, the
  parseable JSON (if any) is inside the `result` string, possibly in
  ```json fences. This adapter handles both cases.

Subprocess isolation: each call gets its own TMPDIR via env override.
Claude Code session state lives under ~/.claude/ which is shared across
calls; the orchestrator's flock prevents concurrent MoA runs from
racing on that state.
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

from adapters import READ_ONLY_RULE, kill_proc_tree  # shared POSIX+Windows timeout handler

# The claude CLI has no --temperature flag. For self-moa, each proposer/refiner
# instance injects this directive to approximate temperature=0.7 diversity.
# Without it, multiple sonnet instances would converge on nearly identical
# outputs, defeating the purpose of the self-moa arm.
TEMPERATURE_DIVERSITY_SHIM = (
    "DIVERSITY DIRECTIVE: You are one of several independent instances "
    "producing proposals in parallel. Deliberately vary your approach: "
    "choose a different entry point into the problem, emphasise different "
    "tradeoffs, and vary the depth vs breadth balance compared to a default "
    "response. Do NOT hedge by trying to cover all angles — commit to one "
    "coherent perspective. This is essential for the ensemble to produce "
    "meaningful diversity."
)


@dataclass
class ClaudeResult:
    """Result of a single claude -p invocation."""
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None


def _claude_bin() -> str:
    """Binary name/path for claude. Honors MOA_CLAUDE_BIN env override."""
    return os.environ.get("MOA_CLAUDE_BIN") or "claude"


def check_available() -> tuple[bool, str]:
    """Verify claude CLI is on PATH and authenticated.

    Claude Code stores auth in ~/.claude/ (OAuth) or via environment
    variable (ANTHROPIC_API_KEY). We accept either. A minimal round-trip
    would be more authoritative but would cost a few cents per preflight,
    so we check for the presence of auth state instead and let the real
    invocation surface any auth failure.
    """
    bin_name = _claude_bin()
    if not shutil.which(bin_name):
        return False, (
            f"claude CLI not found ({bin_name!r} not on PATH; "
            "install: https://docs.claude.com/en/docs/claude-code/quickstart, "
            "or set MOA_CLAUDE_BIN)"
        )
    claude_dir = Path.home() / ".claude"
    has_oauth = claude_dir.exists() and (claude_dir / "settings.json").exists()
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not (has_oauth or has_api_key):
        return False, (
            "claude not authenticated (run: claude, complete login, "
            "or set ANTHROPIC_API_KEY)"
        )
    return True, "ok"


# Read-only tool set for sonnet: only tools that CANNOT mutate state. No
# Agent (prevents recursive subagent spawning, which was the speed killer in
# the first dogfood run). No Bash/Edit/Write (hard tool-level read-only,
# stronger than trusting the prompt). No NotebookEdit, TodoWrite, etc.
SONNET_READONLY_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch"


def _write_log_file(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    """Write the adapter's captured output to disk, swallowing IO errors.

    Called from the finally block of run(), so it must never raise -- any
    IO failure while writing the log is logged to stderr and ignored so the
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
        sys_stderr = __import__("sys").stderr
        print(f"[claude adapter] failed to write log {log_file}: {e}", file=sys_stderr)


def run(
    *,
    prompt: str,
    schema_path: Path,
    repo_path: Path,
    model: str = "claude-sonnet-4-6",
    timeout_seconds: int = 1200,  # orchestrator always passes --sonnet-timeout
    log_file: Optional[Path] = None,
    temperature_shim: Optional[str] = None,
) -> ClaudeResult:
    """Invoke claude -p with the given prompt and schema.

    Args:
        prompt: The full prompt text. Passed as the positional arg to claude -p.
        schema_path: Path to JSON Schema file. Passed to --json-schema.
        repo_path: Working directory. Claude Code's Read tool can access this
            tree without --add-dir (cwd is implicitly readable).
        model: Claude model id. Default claude-sonnet-4-6.
        timeout_seconds: Hard wall-clock cap. Default 1200s (20 min) because
            sonnet with full tool access was observed taking >900s in the
            first dogfood run. The restricted tool set (no Agent) should
            bring it closer to the ~3-4 min typical, but we leave headroom.
        log_file: Optional path to write the full claude output to. ALWAYS
            written in every exit path so post-mortems never come up empty.
        temperature_shim: Optional text appended to --append-system-prompt to
            simulate temperature diversity. The claude CLI has no --temperature
            flag; for self-moa we inject an explicit diversity directive instead.
            When None the READ_ONLY_RULE is used alone (moa-x behavior, unchanged).

    Returns:
        ClaudeResult with the parsed structured_output (or None on failure).
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None

    try:
        if not schema_path.exists():
            stderr_captured = f"Schema file not found: {schema_path}"
            return ClaudeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=0.0, error_message="missing schema",
            )

        # Load the schema once so we can pass it inline to --json-schema. Claude
        # Code's --json-schema flag accepts a JSON string, NOT a path.
        try:
            schema_json = schema_path.read_text(encoding="utf-8")
            json.loads(schema_json)  # validate it's parseable
        except (OSError, json.JSONDecodeError) as e:
            stderr_captured = f"Could not load schema {schema_path}: {e}"
            return ClaudeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=0.0, error_message="invalid schema file",
            )

        tmpdir = tempfile.mkdtemp(prefix="moa-claude-")
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["XDG_CACHE_HOME"] = str(Path(tmpdir) / "cache")
        # Prevent the subprocess from generating __pycache__/ and .pyc files
        # during execution.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Disable claude's own telemetry for these subprocess runs to keep the
        # parent session's telemetry clean. The parent still reports the MoA
        # wrapper call normally.
        env.setdefault("CLAUDE_CODE_DISABLE_TELEMETRY", "1")

        # `--tools` in claude-cli is variadic (<tools...>) and greedily
        # consumes subsequent positional args. Use the `--` separator to
        # unambiguously mark the end of option parsing so the final positional
        # is treated as the prompt, not another tool name.
        system_prompt_suffix = READ_ONLY_RULE
        if temperature_shim:
            # The claude CLI has no --temperature flag. Append a diversity
            # directive so self-moa instances produce meaningfully different
            # proposals, approximating the temperature=0.7 declared in the YAML.
            system_prompt_suffix = READ_ONLY_RULE + "\n\n" + temperature_shim
        cmd = [
            _claude_bin(),
            "-p",
            "--model", model,
            "--dangerously-skip-permissions",
            "--output-format", "json",
            "--json-schema", schema_json,
            "--append-system-prompt", system_prompt_suffix,
            "--tools", SONNET_READONLY_TOOLS,
            "--",
            prompt,
        ]

        try:
            # Use explicit Popen + killpg on timeout so we can tear down the
            # ENTIRE process group on timeout, not just the top-level claude
            # binary. Claude Code spawns Haiku subagents, WebFetch helpers,
            # and tool subprocesses that would otherwise survive as orphans
            # after a subprocess.run timeout.
            proc = subprocess.Popen(
                cmd,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(repo_path),
                start_new_session=True,  # isolate from parent signal group
            )
            try:
                stdout_text, stderr_text = proc.communicate(timeout=timeout_seconds)
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
                return ClaudeResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds}s",
                )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            stderr_captured = f"claude binary not found on PATH: {e}"
            return ClaudeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"claude binary not found: {e}",
            )
        except OSError as e:
            duration = time.monotonic() - start
            stderr_captured = f"OSError launching claude: {e}"
            return ClaudeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"OSError launching claude: {e}",
            )

        if proc.returncode != 0:
            return ClaudeResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=f"claude exited with code {proc.returncode}",
            )

        payload = _extract_structured_output(stdout_captured)
        if payload is None:
            return ClaudeResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message="could not extract structured_output or parseable JSON from claude stdout",
            )

        return ClaudeResult(
            success=True, payload=payload, raw_stdout=stdout_captured,
            raw_stderr=stderr_captured, exit_code=0,
            duration_seconds=duration,
        )
    finally:
        # ALWAYS write the log file (even on error/timeout/exception) so
        # post-mortem is possible. Then clean up the tmpdir.
        _write_log_file(log_file, stdout_captured, stderr_captured)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_structured_output(stdout: str) -> Optional[dict]:
    """Extract the validated payload from claude -p JSON output.

    Claude Code's outer envelope with --json-schema set:
        {
          "type": "result",
          "result": "",
          "structured_output": {...},   <-- what we want
          "session_id": "...",
          ...
        }

    Without --json-schema:
        {
          "type": "result",
          "result": "```json\n{...}\n```",   <-- may have fences
          ...
        }

    This function handles both. It also tolerates leading log lines
    (claude-cli sometimes emits version info or warnings before the
    JSON envelope).
    """
    if not stdout:
        return None

    outer = None
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        # Find the first top-level JSON object in the stream
        first_brace = stdout.find("{")
        while first_brace >= 0:
            try:
                outer = json.loads(stdout[first_brace:])
                break
            except json.JSONDecodeError:
                first_brace = stdout.find("{", first_brace + 1)

    if not isinstance(outer, dict):
        return None

    # Preferred path: structured_output field populated by --json-schema
    structured = outer.get("structured_output")
    if isinstance(structured, dict):
        return structured

    # Fallback path: parse the free-text `result` field
    result_text = outer.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        return None

    # Try fenced JSON first
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", result_text, re.DOTALL):
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue

    # Then scan for a balanced top-level object
    candidates = []
    text = result_text
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
