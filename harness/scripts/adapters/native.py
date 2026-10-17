"""Shared bounded execution for the optional native Muse and DeepSeek CLIs.

Each invocation has an independent repository snapshot and configuration home.
Only terminal answer events are parsed; tool output cannot masquerade as JSON.
The orchestrator still owns validation, retry, and repair deadlines.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import READ_ONLY_RULE, drain_after_timeout, extract_json_from_text, remaining_timeout
from adapters.agy import _isolated_git_workspace


@dataclass
class NativeResult:
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh").expanduser()


def deepseek_key() -> str:
    """Use only the native credential name, never another provider's key."""
    if "DEEPSEEK_API_KEY" in os.environ:
        return os.environ["DEEPSEEK_API_KEY"]
    path = dsh_home() / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*", line)
            if match:
                value = match[1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value
    return ""


def muse_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "muse"


@contextmanager
def muse_auth(config: Path, deadline: float):
    """Isolate customization without discarding refreshed browser credentials.

    Serialize native MoA callers while the CLI owns its private credential copy.
    A concurrent external login wins over our copy (compare before replacement).
    """
    source = muse_config_home() / "auth.json"
    if os.environ.get("META_API_KEY") or not source.is_file():
        yield
        return
    import fcntl
    lock_path = source.parent / ".moa-auth.lock"
    with lock_path.open("a") as lock:
        lock_path.chmod(0o600)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Muse credential lock exhausted the invocation deadline")
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        original = source.read_bytes()
        target = config / "auth.json"
        target.write_bytes(original)
        target.chmod(0o600)
        try:
            yield
        finally:
            if target.is_file() and source.is_file():
                refreshed = target.read_bytes()
                if refreshed != original and source.read_bytes() == original:
                    # Validate before replacing a credential file after a crash.
                    if isinstance(json.loads(refreshed), dict):
                        fd, name = tempfile.mkstemp(prefix=".moa-auth-", dir=source.parent)
                        try:
                            with os.fdopen(fd, "wb") as handle:
                                handle.write(refreshed)
                            os.replace(name, source)
                        finally:
                            Path(name).unlink(missing_ok=True)


def available(kind: str) -> tuple[bool, str]:
    binary = os.environ.get(f"MOA_{kind.upper()}_BIN") or ("dsh" if kind == "deepseek" else "muse")
    if not shutil.which(binary):
        return False, f"{binary} is not installed; see docs/native-harnesses.md"
    if kind == "deepseek":
        authenticated = bool(deepseek_key())
        hint = "set DEEPSEEK_API_KEY or save it in ~/.dsh/.env"
    else:
        auth = muse_config_home() / "auth.json"
        authenticated = bool(os.environ.get("META_API_KEY")) or (auth.is_file() and auth.stat().st_size > 2)
        hint = "run muse login or muse auth set --api-key-stdin"
    return authenticated, "Local credential detected; this check does not run inference" if authenticated else f"Not authenticated: {hint}"


def terminal_answer(kind: str, stdout: str) -> tuple[str, Optional[str]]:
    answer = None
    error = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if kind == "muse":
            tag = str(event.get("payload_type", ""))
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if tag == "run.terminal.completed":
                answer = payload.get("text")
            elif tag.startswith("run.terminal."):
                error = str(payload.get("reason") or tag)
        elif event.get("type") == "final":
            answer = event.get("text")
        elif event.get("type") == "error":
            error = str(event.get("message") or "DeepSeek Harness error")
    if error:
        return "", error
    return (answer, None) if isinstance(answer, str) else ("", "Native CLI emitted no completed terminal answer")


def dsh_overlay(default_tree: str, model: str, effort: Optional[str], allow_tools: bool,
                allow_webfetch: bool) -> list[dict]:
    """Disable every shipped tool plugin except the explicit research set.

    The dump is YAML, but only its top-level scalar IDs are needed. Unknown
    tool plugins are disabled rather than inherited as the CLI adds features.
    """
    ids = re.findall(r"^- id: ([a-zA-Z0-9_-]+)\s*$", default_tree, re.MULTILINE)
    if not {"tool-fs", "tool-fs-search", "tool-web", "agent-default-model"}.issubset(ids):
        raise RuntimeError("Unrecognized DeepSeek profile; cannot enforce the read-only tool policy")
    allowed = {"tool-fs", "tool-fs-search"} if allow_tools else set()
    if allow_tools and allow_webfetch:
        allowed.add("tool-web")
    rows = [{"id": name, "disabled": name not in allowed}
            for name in ids if name.startswith("tool-")]
    next(row for row in rows if row["id"] == "tool-fs")["config"] = {
        "read": True, "readImage": False, "write": False, "edit": False,
    }
    rows += [
        {"id": "agent-default-model", "config": {"provider": "deepseek-official", "model": model,
             **({"reasoningEffort": effort} if effort else {})}},
        {"id": "llm-retry", "disabled": True},
        {"id": "session-title", "disabled": True},
        {"id": "session-title-llm", "disabled": True},
        {"id": "hmr", "disabled": True},
        {"id": "skill-filesystem", "disabled": True},
        {"id": "agent-instructions", "disabled": True},
        {"id": "plan-mode", "disabled": True},
        {"id": "llm-deepseek", "config": {"retryPolicy": {"mode": "normal", "maxRetries": 0}}},
    ]
    return rows


@contextmanager
def isolated_workspace(repo_path: Path, scratch: Path, allow_tools: bool, deadline: float):
    if not allow_tools:
        workspace = scratch / "workspace"
        workspace.mkdir()
        yield workspace
        return
    with _isolated_git_workspace(repo_path, scratch, restricted=True, deadline=deadline) as workspace:
        yield workspace


def run(kind: str, *, prompt: str, repo_path: Path, model: str,
        schema_path: Optional[Path] = None, timeout_seconds: int = 1200,
        log_file: Optional[Path] = None, reasoning_effort: Optional[str] = None,
        allow_tools: bool = True, allow_webfetch: bool = True) -> NativeResult:
    started = time.monotonic()
    stdout, stderr, code = "", "", 1
    error = None
    payload = None
    binary = os.environ.get(f"MOA_{kind.upper()}_BIN") or ("dsh" if kind == "deepseek" else "muse")
    try:
        if kind == "muse" and reasoning_effort is not None:
            from muse_models import muse_efforts
            if reasoning_effort not in muse_efforts(model):
                raise ValueError(f"Unsupported Muse reasoning effort {reasoning_effort!r} for {model}")
        with tempfile.TemporaryDirectory(prefix=f"moa-{kind}-") as tmp, ExitStack() as auth_stack:
            scratch = Path(tmp)
            with isolated_workspace(repo_path, scratch, allow_tools, started + timeout_seconds) as workspace:
                env = {k: v for k, v in os.environ.items() if not k.startswith("SLACK_")}
                env.update(PWD=str(workspace), TMPDIR=tmp, XDG_CACHE_HOME=str(scratch / "cache"))
                full_prompt = (READ_ONLY_RULE + "\n\n" if allow_tools else "") + prompt
                if kind == "muse":
                    config = scratch / "config" / "muse"
                    config.mkdir(parents=True)
                    auth_stack.enter_context(muse_auth(config, started + timeout_seconds))
                    env.update(XDG_CONFIG_HOME=str(scratch / "config"), XDG_DATA_HOME=str(scratch / "data"),
                               XDG_STATE_HOME=str(scratch / "state"))
                    task = scratch / "prompt.md"
                    task.write_text(full_prompt, encoding="utf-8")
                    cmd = [binary, "exec", "--json", "--model", model, "--prompt-file", str(task),
                           "--workspace", str(workspace), "--preset", "native-basic" if allow_tools else "empty",
                           "--disable-shell", "--disable-write", "--disable-reminders", "--no-foreign-personal-context",
                           "--disable-request-user-input", "--approval-mode", "never", "--approval-judge", "off",
                           "--no-session-log", "--max-model-steps", "20", "-c", "agents.execution_capacity=1",
                           "-c", 'run.workflow_trigger_mode="off"']
                    if not allow_tools or not allow_webfetch:
                        cmd.append("--disable-web-tools")
                    cmd += ["--contributor-mode", "on" if model.endswith("-contributor") else "off"]
                    if schema_path:
                        cmd += ["--output-schema", str(schema_path.resolve())]
                    if reasoning_effort:
                        cmd += ["--reasoning-effort", reasoning_effort]
                    input_text = None
                else:
                    key = deepseek_key()
                    if not key:
                        raise RuntimeError("DeepSeek native authorization required: DEEPSEEK_API_KEY or ~/.dsh/.env")
                    env.update(DSH_HOME=str(scratch / "dsh"), DSH_AGENTS_HOME=str(scratch / "agents"),
                               DEEPSEEK_API_KEY=key, DSH_PERMISSION_MODE="read-only", DSH_TOOLS_MODE="native")
                    tree = subprocess.run([binary, "--profile", "headless", "--dump-default-config"],
                        capture_output=True, text=True, env=env, cwd=workspace,
                        timeout=min(20, remaining_timeout(started, timeout_seconds)), check=True)
                    overlay = scratch / "moa-policy.yml"
                    # JSON is a strict YAML subset; no optional YAML library is required.
                    overlay.write_text(json.dumps(dsh_overlay(tree.stdout, model, reasoning_effort,
                                                             allow_tools, allow_webfetch)), encoding="utf-8")
                    cmd = [binary, "--profile", "headless", "--patch", str(overlay), "--json"]
                    input_text = full_prompt
                if remaining_timeout(started, timeout_seconds) <= 0:
                    raise RuntimeError(f"{kind} preparation exhausted the invocation deadline")
                proc = subprocess.Popen(cmd, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        start_new_session=True)
                try:
                    stdout, stderr = proc.communicate(input_text, timeout=remaining_timeout(started, timeout_seconds, reserve_cleanup=True))
                    code = proc.returncode
                except subprocess.TimeoutExpired as exc:
                    stdout, stderr = drain_after_timeout(proc, exc, started, timeout_seconds)
                    code = -1
                    error = f"{kind} timed out after {timeout_seconds}s"
                text, terminal_error = terminal_answer(kind, stdout)
                error = error or terminal_error
                if code and not error:
                    error = f"{kind} exited with code {code}"
                if not error:
                    payload = extract_json_from_text(text)
                    if not isinstance(payload, dict):
                        payload = None
                        error = "Native CLI terminal answer is not a JSON object"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = str(exc)
    finally:
        if log_file:
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                log_file.write_text(f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n", encoding="utf-8")
            except OSError as exc:
                print(f"[{kind} adapter] could not write capture: {exc}", file=sys.stderr)
    return NativeResult(not error and code == 0 and payload is not None, payload, stdout, stderr,
                        code, time.monotonic() - started, error)
