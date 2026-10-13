"""Version-aware OpenCode CLI adapter.

V2 uses a private server, a disposable repository snapshot, an explicit
read-only agent, and NDJSON text/error events. V1 keeps its original flags and
OPENCODE_CONFIG policy. Large prompts use files; bounded repairs are inline.
Authentication remains in the underlying CLI's persisted account state.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from adapters import READ_ONLY_RULE, extract_json_from_text, kill_proc_tree
from adapters.agy import AgyWorkspaceIsolationError, _isolated_git_workspace

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
    "XAI_API_KEY",  # xAI Grok (built-in `grok` provider → xai/grok-4.7)
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


def _cli_major_version() -> int:
    """Fail closed when the invocation contract cannot be determined."""
    proc = subprocess.run(
        [_opencode_bin(), "--version"], capture_output=True, text=True, timeout=10
    )
    match = re.search(r"\bv?(\d+)\.\d+\.\d+\b", proc.stdout or "")
    if proc.returncode != 0 or match is None or int(match.group(1)) not in {1, 2}:
        raise RuntimeError("unsupported or unknown OpenCode CLI version")
    return int(match.group(1))


def _v2_config_for_model(model: str, *, allow_webfetch: bool, allow_tools: bool) -> dict:
    rules = [{"action": "*", "resource": "*", "effect": "deny"}]
    if allow_tools:
        rules += [
            {"action": action, "resource": "*", "effect": "allow"}
            for action in ("read", "glob", "grep")
        ]
        if allow_webfetch:
            rules += [
                {"action": action, "resource": "*", "effect": "allow"}
                for action in ("webfetch", "websearch")
            ]
        rules += [
            {"action": "read", "resource": pattern, "effect": "deny"}
            for pattern in ("*.env", "*.env.*")
        ]
    agent = {
        "description": "MoA-X read-only planner",
        "mode": "primary",
        "system": READ_ONLY_RULE,
        "permissions": rules,
    }
    if "qwen" in model.lower():
        agent["steps"] = _QWEN_MAX_AGENT_STEPS
    settings = {
        "$schema": "https://opencode.ai/config.json",
        "permissions": rules,
        "agents": {"moa-reader": agent},
        # Headless runs cannot complete the first-search provider chooser.
        "websearch": {"provider": "random"} if allow_tools and allow_webfetch else False,
    }
    if model.startswith(f"{_QWEN_TOKEN_PLAN_PROVIDER_ID}/"):
        model_id = model.split("/", 1)[1]
        settings["providers"] = {
            _QWEN_TOKEN_PLAN_PROVIDER_ID: {
                "name": "Qwen Cloud Token Plan",
                "env": ["QWEN_TOKEN_PLAN_API_KEY"],
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {"baseURL": _QWEN_TOKEN_PLAN_BASE_URL},
                "models": {model_id: {"name": model_id}},
            }
        }
    return settings


@contextmanager
def _v2_workspace(repo_path: Path, scratch: Path, *, allow_tools: bool):
    """V2 ignores OPENCODE_CONFIG: install policy only in a private workspace."""
    if not allow_tools:
        workspace = scratch / "workspace"
        workspace.mkdir()
        yield workspace
        return
    with _isolated_git_workspace(repo_path, scratch) as workspace:
        if workspace.resolve() == repo_path.resolve():
            workspace = scratch / "workspace"
            shutil.copytree(
                repo_path, workspace,
                ignore=shutil.ignore_patterns(".moa", ".venv", "node_modules", "__pycache__"),
                symlinks=True,
            )
        workspace.mkdir(parents=True, exist_ok=True)
        yield workspace


def _v2_response(stdout: str) -> tuple[str, str]:
    """Keep final text separate from tools; surface typed error events."""
    texts, errors = [], []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
        elif event.get("type") == "error":
            error = event.get("error") or {}
            errors.append(f"{error.get('type', 'error')}: {error.get('message', '')}")
    return "\n".join(texts), "\n".join(errors)

# Qwen 3.8 Max tends to issue one tool call per model turn. Without an
# agent-step ceiling a broadcast review can consume the entire subprocess
# timeout in repo reads and die before emitting its JSON. OpenCode's `steps`
# limit converts the next turn to text-only, preserving a finalization pass.
_QWEN_MAX_AGENT_STEPS = 14


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
        config["agent"] = {
            "build": {
                "steps": _QWEN_MAX_AGENT_STEPS,
                # A nested agent has its own step budget and can outlive the
                # parent call. Broadcast refiners already receive every
                # proposal and can do the required checks directly.
                "permission": {"task": "deny"},
            }
        }
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

    catalog: set[str] | None = None
    try:
        if _cli_major_version() == 2:
            model_proc = subprocess.run(
                [bin_name, "models", "--standalone"],
                capture_output=True, text=True, timeout=25,
            )
            if model_proc.returncode != 0:
                return {model: (False, "OpenCode model catalog probe failed") for model in requested}
            catalog = {line.strip() for line in model_proc.stdout.splitlines() if "/" in line}
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        # V1 and mocked/older clients retain credential-specific checks.
        pass

    def ready(model: str) -> tuple[bool, str]:
        provider = model.split("/", 1)[0].lower()
        if catalog is not None and provider != _QWEN_TOKEN_PLAN_PROVIDER_ID and model.split("#", 1)[0] not in catalog:
            return False, f"{model} is absent from the current OpenCode catalog"
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
        model: `provider/model` id, e.g. "zhipuai/glm-5.3".
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
    workspace_stack = ExitStack()

    try:
        major = _cli_major_version()
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

        active_repo = repo_path
        if major == 2:
            active_repo = workspace_stack.enter_context(
                _v2_workspace(repo_path, Path(tmpdir), allow_tools=allow_tools)
            )
            private_config = active_repo / ".opencode" / "opencode.json"
            private_config.parent.mkdir(parents=True, exist_ok=True)
            private_config.write_text(json.dumps(_v2_config_for_model(
                model, allow_webfetch=allow_webfetch, allow_tools=allow_tools
            )), encoding="utf-8")
            env.pop("OPENCODE_CONFIG", None)
            env.pop("OPENCODE_CONFIG_CONTENT", None)
            # Persisted account state stays in the default data directory;
            # unrelated user plugins/MCP/config do not enter the private server.
            env["XDG_CONFIG_HOME"] = str(Path(tmpdir) / "config")
            selected_model = f"{model}#{reasoning_effort}" if reasoning_effort else model
            cmd = [_opencode_bin(), "run", "--standalone", "--format", "json",
                   "--model", selected_model, "--agent", "moa-reader", "--auto"]
            cmd.append(full_prompt if inline_prompt else
                       "Read the attached task completely. The active directory is a "
                       "private repository snapshot. Cite repository-relative paths "
                       "and return only the requested JSON object.")
            if prompt_file is not None:
                attached = active_repo / ".moa-agent-input" / "prompt.md"
                attached.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(prompt_file, attached)
                cmd.extend(["--file", str(attached)])
        else:
            # V1's file option is greedy: keep it last after the message.
            cmd = [_opencode_bin(), "run", full_prompt if inline_prompt else
                   "Read the attached file in full and return the requested JSON object.",
                   "-m", model, "--dir", str(repo_path),
                   "--dangerously-skip-permissions", "--print-logs", "--log-level", "ERROR"]
            if reasoning_effort:
                cmd.extend(["--variant", reasoning_effort])
            if prompt_file is not None:
                cmd.extend(["-f", str(prompt_file)])

        # V2 chooses its root from PWD before process.cwd(). Updating cwd
        # alone silently selects the caller's repository and skips our policy.
        env["PWD"] = str(active_repo.resolve())
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(active_repo),
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

        model_stdout = stdout_captured
        if major == 2:
            model_stdout, event_errors = _v2_response(stdout_captured)
            if event_errors:
                stderr_captured += "\n" + event_errors
                msg, _ = _diagnose_failure("", stderr_captured)
                return OpenCodeResult(
                    False, None, stdout_captured, stderr_captured,
                    proc.returncode or 1, duration, msg, transient_empty=False,
                )

        if proc.returncode != 0:
            msg, transient = _diagnose_failure(model_stdout, stderr_captured)
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
        payload = _extract_schema_candidate(model_stdout, required_keys)
        if payload is None:
            msg, transient = _diagnose_failure(model_stdout, stderr_captured)
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
    except (RuntimeError, OSError, AgyWorkspaceIsolationError, subprocess.TimeoutExpired) as exc:
        return OpenCodeResult(False, None, stdout_captured, str(exc), -1,
                              time.monotonic() - start, str(exc))
    finally:
        _write_log_file(log_file, stdout_captured, stderr_captured)
        workspace_stack.close()
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
    if any(flag in stderr_lower for flag in ("unrecognized flag", "unexpected positional", "unknown flag", "unsupported or unknown opencode")):
        return "opencode CLI invocation is incompatible with the installed version", False
    if any(marker in stderr_lower for marker in ("provider.no-route", "model unavailable:")):
        return "opencode model route is unavailable; refresh the configured model ID", False
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
