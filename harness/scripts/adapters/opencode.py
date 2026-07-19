"""OpenCode CLI adapter (multi-lab via `opencode run`).

Invokes `opencode run` headlessly. OpenCode is the harness we route
Chinese-lab frontier models through — GLM (Zhipu), Kimi (Moonshot), and
Qwen (Alibaba Cloud Token Plan) — plus Fireworks-hosted variants. Model ids
are `provider/model` strings, e.g.
`opencode-go/glm-5.2`, `opencode-go/kimi-k2.7-code` (the defaults), the
direct-provider `zhipuai/glm-5.2` / `moonshotai/kimi-k2.7-code`, or
`fireworks-ai/accounts/fireworks/models/glm-5p2`, or
`qwen-token-plan/qwen3.7-max`.

OpenCode has no JSON envelope in default text mode — the model's final
text goes straight to stdout, so we pull the inner JSON payload with the
shared `extract_json_from_text` helper (fenced or bare top-level object,
longest-first) and validate it orchestrator-side. There is no
`--output-schema` equivalent, so this adapter is schema-unenforced like
the cursor adapter.

Prompt delivery: OpenCode does NOT read stdin (the feature request was
declined upstream) and a single argv entry is capped at MAX_ARG_STRLEN
(128 KB on Linux). Refiner prompts — scout brief plus every proposer's
full output — blow past that, so the adapter writes the prompt to a file
and passes it with `-f`, plus a short positional instruction to read and
follow it.

Read-only discipline is enforced two ways: the shared READ_ONLY_RULE is
prepended to the prompt, and a `permission` block that denies `edit` and
all `bash` (writes and reads alike — the model still has the native
read/grep/glob/webfetch tools for repo grounding) is written to a temp
config pointed at by OPENCODE_CONFIG (which opencode MERGES into its config
chain, it does not replace the user's global config). Explicit `deny` is
honored even under `--dangerously-skip-permissions`.

Subprocess isolation: each call gets its own TMPDIR and config file via
env override. OpenCode auth/session state lives under
~/.local/share/opencode/ which is shared across calls; the orchestrator's
flock prevents concurrent MoA invocations from racing on it.
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

# API-key env vars that authenticate at least one opencode provider without an
# interactive `opencode auth login`. Presence of any one lets preflight pass
# even when `opencode auth list` is empty (env-key auth doesn't register there).
_PROVIDER_KEY_ENVS = (
    "ZHIPU_API_KEY",
    "MOONSHOT_API_KEY",
    "FIREWORKS_API_KEY",
    "OPENCODE_API_KEY",
    "OPENROUTER_API_KEY",
    "QWEN_TOKEN_PLAN_API_KEY",
)

# Read-only permission policy handed to opencode via OPENCODE_CONFIG. Denying
# edit + bash outright still leaves the native read/grep/glob/webfetch tools,
# which is all a planning proposer needs. `deny` is honored even under
# --dangerously-skip-permissions.
_READONLY_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
        "edit": "deny",
        "bash": {"*": "deny"},
        "webfetch": "allow",
    },
}

_QWEN_TOKEN_PLAN_PROVIDER_ID = "qwen-token-plan"
_QWEN_TOKEN_PLAN_BASE_URL = (
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
)


def _config_for_model(model: str) -> dict:
    """Build the isolated OpenCode config, adding known custom providers.

    Qwen Token Plan is not an OpenCode built-in. The official Qwen/OpenCode
    setup uses a custom provider plus a dedicated `sk-sp-` key. Keep the key
    out of this generated file by using OpenCode's env substitution syntax.
    """
    config = json.loads(json.dumps(_READONLY_CONFIG))
    prefix = f"{_QWEN_TOKEN_PLAN_PROVIDER_ID}/"
    if model.startswith(prefix):
        model_id = model[len(prefix):]
        config["provider"] = {
            _QWEN_TOKEN_PLAN_PROVIDER_ID: {
                # The Token Plan URL is explicitly OpenAI-compatible. Using
                # OpenCode's OpenAI-compatible transport ensures it appends
                # /chat/completions instead of Anthropic's /messages route.
                "npm": "@ai-sdk/openai-compatible",
                "name": "Qwen Cloud Token Plan",
                "options": {
                    "baseURL": _QWEN_TOKEN_PLAN_BASE_URL,
                    "apiKey": "{env:QWEN_TOKEN_PLAN_API_KEY}",
                },
                "models": {
                    model_id: {
                        "name": model_id,
                        "options": {
                            "thinking": {"type": "enabled", "budgetTokens": 8192}
                        },
                    }
                },
            }
        }
    return config


@dataclass
class OpenCodeResult:
    """Result of a single opencode invocation."""
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None
    # True when the run exited cleanly but produced no parseable payload and
    # stderr showed no quota/auth signal — the transient empty-output flake a
    # single re-dispatch usually recovers. Mirrors the cursor field so
    # the orchestrator's redispatch path treats all schema-unenforced harnesses
    # uniformly.
    transient_empty: bool = False


def _opencode_bin() -> str:
    """Binary name/path for opencode. Honors MOA_OPENCODE_BIN env override."""
    return os.environ.get("MOA_OPENCODE_BIN") or "opencode"


def check_available() -> tuple[bool, str]:
    """Verify the opencode CLI is on PATH and has some usable auth.

    Hard requirement: the binary is on PATH. Auth is softer — `opencode auth
    list` shows interactively-logged-in providers, but env-var keys (ZHIPU_API_KEY
    etc.) authenticate without registering there, so their presence also passes.
    A wrong/expired credential still surfaces in the real call, same as the
    other adapters' lenient preflights.
    """
    bin_name = _opencode_bin()
    if not shutil.which(bin_name):
        return False, (
            f"opencode CLI not found ({bin_name!r} not on PATH; "
            "install: curl -fsSL https://opencode.ai/install | bash, "
            "or set MOA_OPENCODE_BIN)"
        )

    try:
        proc = subprocess.run(
            [bin_name, "auth", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, f"opencode auth list probe failed: {e}"

    listed = (proc.stdout or "").strip()
    env_keys = [k for k in _PROVIDER_KEY_ENVS if os.environ.get(k)]

    if proc.returncode == 0 and listed and "0 credentials" not in listed.lower():
        return True, "ok (opencode auth list has credentials)"
    if env_keys:
        return True, f"ok (provider key env: {', '.join(env_keys)})"
    return False, (
        "opencode has no credentials (run: opencode auth login, or export a "
        "provider key such as ZHIPU_API_KEY / MOONSHOT_API_KEY / FIREWORKS_API_KEY)"
    )


def _write_log_file(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    """Write the adapter's captured output to disk, swallowing IO errors.

    Called from the finally block of run(), so it must never raise -- any IO
    failure while writing the log is printed to stderr and ignored.
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
        print(f"[opencode adapter] failed to write log {log_file}: {e}", file=_sys.stderr)


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str,
    schema_path: Optional[Path] = None,
    timeout_seconds: int = 1200,
    log_file: Optional[Path] = None,
) -> OpenCodeResult:
    """Invoke `opencode run` with the given prompt.

    Args:
        prompt: The full prompt text. Written to a file and attached with
            `-f`; the READ_ONLY_RULE is prepended. NOT passed on argv
            (opencode has no stdin and argv is ARG_MAX-capped).
        repo_path: Working directory, passed via `--dir` and Popen cwd.
        model: `provider/model` id, e.g. "zhipuai/glm-5.2".
        schema_path: Optional output schema. Its top-level required keys keep
            extraction from accepting a valid nested object when the model's
            surrounding root object is malformed.
        timeout_seconds: Hard wall-clock cap. Default 1200s, matching siblings.
        log_file: Optional path to write the full opencode output. ALWAYS
            written in every exit path so post-mortems never come up empty.
            When provided, the prompt file is written alongside it (inside the
            session's .moa/ dir, so opencode reads it without an
            external-directory prompt).

    Returns:
        OpenCodeResult with the parsed payload (or None on failure).
    """
    start = time.monotonic()
    stdout_captured = ""
    stderr_captured = ""
    tmpdir: Optional[str] = None

    try:
        tmpdir = tempfile.mkdtemp(prefix="moa-opencode-")
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["XDG_CACHE_HOME"] = str(Path(tmpdir) / "cache")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        # Read-only permission policy via a temp config file.
        config_path = Path(tmpdir) / "opencode.json"
        config_path.write_text(json.dumps(_config_for_model(model)), encoding="utf-8")
        env["OPENCODE_CONFIG"] = str(config_path)

        # Prompt goes in a file (see module docstring). Keep it inside the
        # session's .moa/ dir (next to log_file) when we have one, so opencode
        # reads it as a project-local file; otherwise fall back to the tmpdir
        # (--dangerously-skip-permissions auto-approves the external read).
        full_prompt = READ_ONLY_RULE + "\n\n" + prompt
        if log_file is not None:
            prompt_file = log_file.with_name(log_file.stem + ".prompt.md")
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            prompt_file = Path(tmpdir) / "opencode-prompt.md"
        prompt_file.write_text(full_prompt, encoding="utf-8")

        # Arg order matters: `-f/--file` is a greedy yargs ARRAY option, so the
        # positional message must come BEFORE it (or -f would swallow the
        # message string as a second "file" and error "File not found"). Keep
        # -f last with nothing after it. `--dangerously-skip-permissions`
        # auto-approves any permission not explicitly denied by OPENCODE_CONFIG
        # (which denies edit + bash), so reads/webfetch work but writes can't.
        cmd = [
            _opencode_bin(),
            "run",
            "Read the attached file in full and follow its instructions exactly. "
            "Output only the requested JSON object.",
            "-m", model,
            "--dir", str(repo_path),
            "--dangerously-skip-permissions",
            "-f", str(prompt_file),
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(repo_path),
                start_new_session=True,
            )
            try:
                stdout_text, stderr_text = proc.communicate(timeout=timeout_seconds)
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
                return OpenCodeResult(
                    success=False, payload=None, raw_stdout=stdout_captured,
                    raw_stderr=stderr_captured, exit_code=-1,
                    duration_seconds=duration,
                    error_message=f"timeout after {timeout_seconds}s",
                )
        except FileNotFoundError as e:
            duration = time.monotonic() - start
            stderr_captured = f"opencode binary not found on PATH: {e}"
            return OpenCodeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"opencode binary not found: {e}",
            )
        except OSError as e:
            duration = time.monotonic() - start
            stderr_captured = f"OSError launching opencode: {e}"
            return OpenCodeResult(
                success=False, payload=None, raw_stdout="",
                raw_stderr=stderr_captured, exit_code=-1,
                duration_seconds=duration,
                error_message=f"OSError launching opencode: {e}",
            )

        if proc.returncode != 0:
            msg, transient = _diagnose_failure(stdout_captured, stderr_captured)
            return OpenCodeResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=f"opencode exited with code {proc.returncode}: {msg}",
                transient_empty=transient,
            )

        required_keys = set()
        if schema_path is not None:
            try:
                required_keys = set(json.loads(schema_path.read_text(encoding="utf-8")).get("required", []))
            except (OSError, json.JSONDecodeError):
                required_keys = set()
        payload = extract_json_from_text(stdout_captured, required_keys=required_keys)
        if payload is None:
            msg, transient = _diagnose_failure(stdout_captured, stderr_captured)
            return OpenCodeResult(
                success=False, payload=None, raw_stdout=stdout_captured,
                raw_stderr=stderr_captured, exit_code=proc.returncode,
                duration_seconds=duration,
                error_message=msg,
                transient_empty=transient,
            )

        return OpenCodeResult(
            success=True, payload=payload, raw_stdout=stdout_captured,
            raw_stderr=stderr_captured, exit_code=0,
            duration_seconds=duration,
        )
    finally:
        _write_log_file(log_file, stdout_captured, stderr_captured)
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _diagnose_failure(stdout: str, stderr: str) -> tuple[str, bool]:
    """Diagnose why the run yielded no payload. Returns (message, transient_empty).

    transient_empty=True only when stdout is empty / has no parseable JSON and
    stderr shows no quota or auth signal — the recoverable empty-output flake.
    Quota and auth failures are non-transient (a retry won't help).
    """
    stderr_lower = (stderr or "").lower()
    quota_hit = any(
        p in stderr_lower
        for p in ("rate limit", "quota", "429", "exceeded", "insufficient balance")
    )
    auth_hit = any(
        p in stderr_lower
        for p in ("unauthorized", "401", "403", "invalid api key", "not authenticated", "no credentials")
    )
    routing_hit = any(
        p in stderr_lower
        for p in ("not found", "404", "unsupported model", "model not found")
    )
    if quota_hit:
        return ("opencode hit rate-limit / quota errors (see stderr). Check the "
                "provider's dashboard or the relevant *_API_KEY budget."), False
    if auth_hit:
        return ("opencode authentication error (see stderr). Run `opencode auth "
                "login` or export the provider's API key."), False
    if routing_hit:
        return ("opencode provider/model routing error (see stderr). Check the "
                "custom provider base URL, transport, and model id."), False
    if not stdout or not stdout.strip():
        return ("opencode produced empty stdout under a clean exit (no quota/auth "
                "signal). Likely transient — re-dispatch typically recovers."), True
    return "could not extract a JSON payload from opencode output", False
