"""Claude Code CLI adapter (Sonnet proposer/refiner).

Invokes `claude -p` headlessly with --json-schema for guaranteed JSON
shape. Claude Code supports arbitrary JSON Schema enforcement natively
via the --json-schema flag, which writes the validated object to
`.structured_output` in the outer result envelope. This makes parsing
cleaner than the OpenCode adapter (where we strip fences) and on par with Codex
(which uses --output-schema).

Key differences from the other adapters:
- Read-only discipline is enforced with a hard `--tools` allowlist containing
  only Read/Grep/Glob/WebSearch/WebFetch, plus the shared prompt rule. Claude
  Code has no filesystem-sandbox flag, but mutating tools are unavailable.
- --bare mode is NOT usable here because it strictly requires
  ANTHROPIC_API_KEY (OAuth / keychain auth is ignored in bare mode).
  We use full mode with --safe-mode and --dangerously-skip-permissions
  instead. Safe mode preserves auth while preventing machine/user/project
  hooks from writing bookkeeping files into the repository during a
  read-only planning run.
- Claude Code emits a terminal result event in its JSONL stream that includes:
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
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import READ_ONLY_RULE, extract_json_from_text, remaining_timeout, drain_after_timeout, git_safe_env

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
# Claude Code safe mode disables hooks, plugins, MCP servers, skills, and
# CLAUDE.md auto-discovery while leaving the authenticated account and built-in
# read tools available. This matters in parallel MoA runs: one user hook that
# writes into the shared repository would otherwise make the workspace
# immutability guard conservatively invalidate every concurrent lane.
CLAUDE_ISOLATION_FLAG = "--safe-mode"


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


def _schema_json_for_cli(schema_path: Path) -> str:
    """Return compact schema JSON accepted by Claude Code's CLI validator."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    # Claude Code 2.1.x resolves `$schema` as a reference and rejects the
    # otherwise-valid Draft 2020-12 URI. The dialect declaration is metadata,
    # not a validation constraint, so omit it only from the inline CLI copy.
    schema.pop("$schema", None)
    return json.dumps(schema, separators=(",", ":"))


def _build_cmd(
    bin_name: str,
    *,
    model: str,
    schema_json: str,
    system_prompt_suffix: str,
    reasoning_effort: Optional[str] = None,
    tools: str = SONNET_READONLY_TOOLS,
) -> list[str]:
    """Build an authenticated but customization-isolated Claude invocation."""
    cmd = [
        bin_name,
        "-p",
        CLAUDE_ISOLATION_FLAG,
        "--model", model,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json", "--verbose",
        "--json-schema", schema_json,
        "--append-system-prompt", system_prompt_suffix,
        "--tools", tools,
    ]
    if reasoning_effort:
        cmd.extend(["--effort", reasoning_effort])
    return cmd


def run(
    *,
    prompt: str,
    schema_path: Path,
    repo_path: Path,
    model: str = "sonnet",
    timeout_seconds: int = 1200,  # orchestrator always passes --sonnet-timeout
    log_file: Optional[Path] = None,
    temperature_shim: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    allow_research: bool = True,
) -> ClaudeResult:
    """Invoke claude -p with the given prompt and schema.

    Args:
        prompt: The full prompt text. Sent over stdin to avoid Linux argv size
            limits when aggregation includes several large model artifacts.
        schema_path: Path to JSON Schema file. Passed to --json-schema.
        repo_path: Working directory. Claude Code's Read tool can access this
            tree without --add-dir (cwd is implicitly readable).
        model: Explicit Claude model id. The curated route pins
            `claude-sonnet-5-5` so provenance does not depend on a rolling alias.
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
        allow_research: When false, expose no Claude tools. A bounded schema
            repair already has the invalid object and must not inspect files
            or repeat web research.

    Returns:
        ClaudeResult with the parsed structured_output (or None on failure).
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None
    workspace_context = ExitStack()

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
            schema_json = _schema_json_for_cli(schema_path)
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
        if model.startswith("claude-fable-"):
            # Fable Max can exhaust the CLI's 64k default on thinking alone.
            # Preserve a deliberately configured lower limit.
            env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "128000")
            limit = env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
            prompt += (f"\nThinking and the final answer share one response limit of {limit} tokens. "
                       "Use reasoning to settle decisions, then write the deliverable once in the final output. "
                       "Do not draft the entire deliverable in thinking and repeat it in the answer.\n")

        # The prompt is deliberately not placed on argv. Large synthesis
        # payloads can exceed Linux MAX_ARG_STRLEN even when total ARG_MAX has
        # headroom; Claude print mode accepts its text input on stdin.
        system_prompt_suffix = READ_ONLY_RULE
        if temperature_shim:
            # The claude CLI has no --temperature flag. Append a diversity
            # directive so self-moa instances produce meaningfully different
            # proposals, approximating the temperature=0.7 declared in the YAML.
            system_prompt_suffix = READ_ONLY_RULE + "\n\n" + temperature_shim
        cmd = _build_cmd(
            _claude_bin(),
            model=model,
            schema_json=schema_json,
            system_prompt_suffix=system_prompt_suffix,
            reasoning_effort=reasoning_effort,
            tools=SONNET_READONLY_TOOLS if allow_research else "",
        )

        if os.environ.get("MOA_RESTRICT_WORKSPACE") == "1":
            cmd.remove(CLAUDE_ISOLATION_FLAG)
            cmd.remove("--dangerously-skip-permissions")
            cmd += ["--restricted", "--strict-mcp-config"]
            env = {k: v for k, v in env.items() if not k.startswith("SLACK_")}
            if allow_research:
                from adapters.agy import _isolated_git_workspace
                original_repo = repo_path.resolve()
                repo_path = workspace_context.enter_context(
                    _isolated_git_workspace(original_repo, Path(tmpdir), deadline=start + timeout_seconds)
                )
                prompt = prompt.replace(str(original_repo), str(repo_path.resolve()))

        try:
            # Use explicit Popen + killpg on timeout so we can tear down the
            # ENTIRE process group on timeout, not just the top-level claude
            # binary. Claude Code spawns Haiku subagents, WebFetch helpers,
            # and tool subprocesses that would otherwise survive as orphans
            # after a subprocess.run timeout.
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=git_safe_env(env),
                cwd=str(repo_path),
                start_new_session=True,  # isolate from parent signal group
            )
            try:
                stdout_text, stderr_text = proc.communicate(
                    input=prompt,
                    timeout=remaining_timeout(start, timeout_seconds, reserve_cleanup=True),
                )
                duration = time.monotonic() - start
                stdout_captured = stdout_text or ""
                stderr_captured = stderr_text or ""
            except subprocess.TimeoutExpired as timeout_error:
                stdout_captured, stderr_captured = drain_after_timeout(proc, timeout_error, start, timeout_seconds)
                stderr_captured = (stderr_captured or "") + f"\n[orchestrator] timeout after {timeout_seconds:g}s"
                duration = time.monotonic() - start
                return ClaudeResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds:g}s",
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

        envelope_error = _envelope_error(stdout_captured)
        if proc.returncode != 0 or envelope_error:
            return ClaudeResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=envelope_error or f"claude exited with code {proc.returncode}",
            )

        try:
            required_keys = set(
                json.loads(schema_path.read_text(encoding="utf-8")).get("required") or []
            )
        except (OSError, json.JSONDecodeError):
            required_keys = None
        payload = _extract_structured_output(
            stdout_captured, required_keys=required_keys
        )
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
        workspace_context.close()
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _result_envelope(stdout: str) -> Optional[dict]:
    """Only a terminal result may supply payload or failure diagnostics.

    Streaming preserves tool/progress/usage events on timeout; those events
    must never be mistaken for the final answer.
    """
    try:
        value = json.loads(stdout)
        if isinstance(value, dict) and (value.get("type") == "result" or
                ("type" not in value and any(key in value for key in ("result", "structured_output", "is_error")))):
            return value
    except ValueError:
        pass
    result = None
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            result = value
    return result


def _envelope_error(stdout: str) -> Optional[str]:
    """Surface only an explicit CLI error envelope, never model response text."""
    outer = _result_envelope(stdout)
    if not isinstance(outer, dict) or outer.get("is_error") is not True:
        return None
    detail = outer.get("error") or outer.get("result") or "Claude returned an error envelope"
    if not isinstance(detail, str):
        detail = json.dumps(detail)
    status = outer.get("api_error_status")
    prefix = f"HTTP {status}: " if type(status) is int and 100 <= status < 600 else ""
    return prefix + detail[:1000]


def _extract_structured_output(
    stdout: str, *, required_keys: Optional[set[str]] = None
) -> Optional[dict]:
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

    outer = _result_envelope(stdout)

    if not isinstance(outer, dict) or outer.get("is_error") is True:
        return None

    # Preferred path: structured_output field populated by --json-schema
    structured = outer.get("structured_output")
    if isinstance(structured, dict):
        return structured

    # Fallback path: parse the free-text `result` field
    result_text = outer.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        return None

    return extract_json_from_text(result_text, required_keys=required_keys)
