"""OpenCode CLI adapter (multi-lab via `opencode run`).

Invokes `opencode run` headlessly. OpenCode is transport for several unrelated
model labs; the curated roster currently uses DeepSeek, Qwen (Alibaba), and
Grok (xAI). Kimi remains resolvable only for archived provenance. User-defined
routes may target other OpenCode providers. Model ids are `provider/model`
strings, e.g. `opencode-go/deepseek-v4-pro`, `opencode-go/grok-4.5`, or
`qwen-token-plan/qwen3.8-max-preview`.

OpenCode has no JSON envelope in default text mode — the model's final
text goes straight to stdout, so we pull the inner JSON payload with the
shared `extract_json_from_text` helper (fenced or bare top-level object,
longest-first) and validate it orchestrator-side. There is no
`--output-schema` equivalent, so this adapter is schema-unenforced like
the other schema-unenforced adapters used by older MoA-X releases.

Prompt delivery: OpenCode does NOT read stdin (the feature request was
declined upstream) and a single argv entry is capped at MAX_ARG_STRLEN
(128 KB on Linux). Normal prompts — especially broadcast refiners — are
therefore written to a file and passed with `-f`. Bounded repair prompts
that fit safely below that limit are passed inline so the model receives
the malformed JSON directly instead of trying to read a long JSON line
through OpenCode's truncated file-view tool.

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
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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
    "XAI_API_KEY",  # xAI Grok (built-in `grok` provider → xai/grok-4.5)
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


_MAX_INLINE_PROMPT_BYTES = 96 * 1024


def _config_for_model(
    model: str,
    *,
    allow_webfetch: bool = True,
    allow_tools: bool = True,
) -> dict:
    """Build the isolated OpenCode config, adding known custom providers.

    Qwen Token Plan is not an OpenCode built-in. The official Qwen/OpenCode
    setup uses a custom provider plus a dedicated `sk-sp-` key. Keep the key
    out of this generated file by using OpenCode's env substitution syntax.
    """
    config = json.loads(json.dumps(_READONLY_CONFIG))
    if not allow_tools:
        # Repair has the complete payload and schema inline. Deny every tool,
        # including subagents and user-configured integrations, so it cannot
        # waste the bounded pass re-reading files or repeating research.
        config["permission"] = {"*": "deny"}
    elif not allow_webfetch:
        config["permission"]["webfetch"] = "deny"
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
    # single re-dispatch usually recovers. The orchestrator uses this field to
    # distinguish incomplete transport output from schema-invalid JSON.
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


def check_models_available(models: Iterable[str]) -> dict[str, tuple[bool, str]]:
    """Report readiness for each configured OpenCode model route.

    OpenCode can hold credentials for several unrelated providers. A generic
    ``auth list`` success therefore cannot prove that a specific model route is
    usable. Keep the Web UI honest by matching the route prefix to either its
    persisted provider account or the corresponding environment credential.
    """
    requested = list(dict.fromkeys(models))
    bin_name = _opencode_bin()
    if not shutil.which(bin_name):
        detail = f"opencode CLI not found ({bin_name!r} not on PATH)"
        return {model: (False, detail) for model in requested}

    try:
        proc = subprocess.run(
            [bin_name, "auth", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        listed = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        listed = ""
        probe_error = f"opencode auth probe failed: {exc}"
    else:
        probe_error = ""

    def ready(model: str) -> tuple[bool, str]:
        provider = model.split("/", 1)[0].lower()
        if provider == "opencode-go":
            ok = "opencode go" in listed or bool(os.environ.get("OPENCODE_API_KEY"))
            return ok, "OpenCode Go account ready" if ok else "OpenCode Go login required"
        if provider == _QWEN_TOKEN_PLAN_PROVIDER_ID:
            ok = bool(os.environ.get("QWEN_TOKEN_PLAN_API_KEY"))
            return ok, "Qwen Token Plan key ready" if ok else "QWEN_TOKEN_PLAN_API_KEY required"
        if provider == "fireworks-ai":
            ok = "fireworks ai" in listed or bool(os.environ.get("FIREWORKS_API_KEY"))
            return ok, "Fireworks account ready" if ok else "Fireworks login or key required"
        if provider == "xai":
            ok = bool(os.environ.get("XAI_API_KEY"))
            return ok, "xAI key ready" if ok else "XAI_API_KEY required"

        env_name = f"{provider.upper().replace('-', '_')}_API_KEY"
        ok = bool(os.environ.get(env_name))
        if ok:
            return True, f"{env_name} ready"
        if probe_error:
            return False, probe_error
        return False, f"no credential detected for OpenCode provider {provider!r}"

    return {model: ready(model) for model in requested}


def check_model_available(model: str) -> tuple[bool, str]:
    """Convenience wrapper for callers checking one OpenCode route."""
    return check_models_available([model])[model]


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


def _extract_schema_candidate(stdout: str, required_keys: set[str]) -> Optional[dict]:
    """Extract the response root even when it is missing required fields."""
    payload = extract_json_from_text(stdout, required_keys=required_keys)
    if payload is not None or not required_keys:
        return payload

    # GLM occasionally emits each subsequent proposer plan item after closing
    # the surrounding plan/root brackets, even though the fields and remaining
    # top-level collections are present. Re-open those item boundaries, then
    # discard only an incomplete final research-source object if generation
    # stopped midway through it. Accept the recovery only when the repaired
    # object has every required proposer root key; strict schema and evidence
    # validation still run in the orchestrator.
    if {"plan", "research_sources"}.issubset(required_keys):
        step_boundary = re.compile(
            r'}(?:\]\})*\]*,?\{?"step":'
        )

        def restore_step_boundary(match: re.Match[str]) -> str:
            previous = stdout[match.start() - 1] if match.start() else ""
            # Some malformed boundaries retain the risks-array close outside
            # the match; others consume it in the extra closing-bracket run.
            return '},{"step":' if previous == "]" else ']},{"step":'

        repaired = step_boundary.sub(restore_step_boundary, stdout)
        recovered = extract_json_from_text(
            repaired, required_keys=required_keys
        )
        if recovered is not None:
            return recovered

        sources_at = repaired.find('"research_sources":[')
        cut_at = repaired.rfind(',{"url":', sources_at)
        while sources_at >= 0 and cut_at > sources_at:
            recovered = extract_json_from_text(
                repaired[:cut_at] + "]}",
                required_keys=required_keys,
            )
            if recovered is not None:
                return recovered
            cut_at = repaired.rfind(',{"url":', sources_at, cut_at)

    # A missing required field is schema-invalid, not unparseable. Keep a
    # root-shaped object so the orchestrator's bounded repair pass can correct
    # it. The structural-signature threshold prevents a nested object that
    # happens to contain agent_id from being mistaken for the response root.
    candidate = extract_json_from_text(stdout)
    signature_matches = (
        len(required_keys.intersection(candidate))
        if isinstance(candidate, dict)
        else 0
    )
    minimum_signature = max(2, len(required_keys) // 2)
    if (
        isinstance(candidate, dict)
        and isinstance(candidate.get("agent_id"), str)
        and signature_matches >= minimum_signature
    ):
        return candidate
    return None


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str,
    schema_path: Optional[Path] = None,
    timeout_seconds: int = 1200,
    log_file: Optional[Path] = None,
    reasoning_effort: Optional[str] = None,
    allow_webfetch: bool = True,
    allow_tools: bool = True,
) -> OpenCodeResult:
    """Invoke `opencode run` with the given prompt.

    Args:
        prompt: The full prompt text. Normal calls write it to a file and
            attach it with `-f`; the READ_ONLY_RULE is prepended. Tool-free
            repair calls pass a safely bounded prompt inline.
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
        allow_webfetch: Whether the isolated OpenCode policy permits webfetch.
            Repair-only calls disable it so a schema correction cannot repeat
            research or consume additional external context.
        allow_tools: Whether OpenCode tools are available. Repair-only calls
            deny every tool and receive the payload inline when it fits below
            Linux's per-argument limit.

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
        config_path.write_text(
            json.dumps(
                _config_for_model(
                    model,
                    allow_webfetch=allow_webfetch,
                    allow_tools=allow_tools,
                )
            ),
            encoding="utf-8",
        )
        env["OPENCODE_CONFIG"] = str(config_path)

        full_prompt = (
            READ_ONLY_RULE + "\n\n" + prompt
            if allow_tools
            else prompt
        )
        inline_prompt = (
            not allow_tools
            and len(full_prompt.encode("utf-8")) <= _MAX_INLINE_PROMPT_BYTES
        )
        prompt_file: Optional[Path] = None
        if not inline_prompt:
            # Keep attached prompts inside the session's .moa/ directory when
            # possible so OpenCode sees a project-local file.
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
            (
                full_prompt
                if inline_prompt
                else "Read the attached file in full and follow its instructions "
                "exactly. Output only the requested JSON object."
            ),
            "-m", model,
            "--dir", str(repo_path),
            "--dangerously-skip-permissions",
            # --print-logs routes progress/logs to stderr. Without it, current
            # opencode (>=1.18) buffers on a TTY-style renderer and hangs forever
            # when stdout/stderr are pipes (headless subprocess), producing an
            # empty-output timeout. Logs on stderr don't affect stdout payload
            # extraction. --log-level ERROR keeps stderr quiet so failure
            # diagnosis stays accurate.
            "--print-logs", "--log-level", "ERROR",
        ]
        if reasoning_effort:
            cmd.extend(["--variant", reasoning_effort])
        if prompt_file is not None:
            cmd.extend(["-f", str(prompt_file)])

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
                    error_message=_timeout_error_message(
                        timeout_seconds, stdout_captured, stderr_captured
                    ),
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
        payload = _extract_schema_candidate(stdout_captured, required_keys)
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

    transient_empty=True when stdout is empty or contains an incomplete /
    malformed model response and stderr shows no quota or auth signal. Tool
    calls can emit 403/404/transport noise on stderr even when the model route
    itself worked, so a non-empty model response takes precedence over stderr
    classifiers. Quota and auth failures with no model output remain
    non-transient.
    """
    stderr_lower = (stderr or "").lower()
    quota_hit = bool(
        re.search(
            r"\b(?:rate[ _-]*limit(?:ed)?|quota (?:exceeded|exhausted)|"
            r"insufficient (?:balance|credits?)|balance (?:exhausted|too low)|"
            r"payment required|monthly spending limit|past invoices?|"
            r"account [^\n]{0,80}\bsuspended)\b",
            stderr_lower,
        )
    )
    auth_hit = any(
        p in stderr_lower
        for p in ("unauthorized", "401", "403", "invalid api key", "not authenticated", "no credentials")
    )
    routing_hit = any(
        p in stderr_lower
        for p in ("not found", "404", "unsupported model", "model not found")
    )
    if stdout and stdout.strip():
        return (
            "opencode produced non-empty but unparseable/incomplete JSON under "
            "a clean auth state. Likely transient — one re-dispatch may recover."
        ), True
    if quota_hit:
        return ("opencode hit rate-limit / quota errors (see stderr). Check the "
                "provider's dashboard or the relevant *_API_KEY budget."), False
    if auth_hit:
        return ("opencode authentication error (see stderr). Run `opencode auth "
                "login` or export the provider's API key."), False
    if routing_hit:
        return ("opencode provider/model routing error (see stderr). Check the "
                "custom provider base URL, transport, and model id."), False
    return ("opencode produced empty stdout under a clean exit (no quota/auth "
            "signal). Likely transient — one re-dispatch may recover."), True


def _timeout_error_message(seconds: int, stdout: str, stderr: str) -> str:
    """Preserve the provider cause when OpenCode's own retries hit our cap."""
    diagnosis, _ = _diagnose_failure(stdout, stderr)
    return f"timeout after {seconds}s; {diagnosis}"
