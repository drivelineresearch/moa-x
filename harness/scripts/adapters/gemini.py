"""Gemini CLI adapter for enterprise, Cloud, and supported API-key accounts.

The adapter inherits Gemini CLI's existing local authentication state. It does
not request, persist, or proxy API keys. Consumer OAuth accounts moved to AGY;
preflight recognizes Gemini CLI's tier-ineligible response and points users at
that adapter instead.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adapters import READ_ONLY_RULE, extract_json_from_text, kill_proc_tree

_REQUIRED_HELP_FLAGS = (
    "--model", "--approval-mode", "--sandbox", "--output-format"
)
_TIER_RE = re.compile(
    r"IneligibleTierError|not eligible.*Gemini CLI|migrat(?:e|ed|ion).*Antigravity",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class GeminiResult:
    success: bool
    payload: Optional[dict]
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    duration_seconds: float
    error_message: Optional[str] = None
    transient_empty: bool = False


def _gemini_bin() -> str:
    return os.environ.get("MOA_GEMINI_BIN") or "gemini"


def _tier_ineligible(text: str) -> bool:
    return bool(_TIER_RE.search(text))


def _extract_response_text(output: str) -> str:
    """Extract assistant text from Gemini JSON or stream-json output."""
    stripped = output.strip()
    if not stripped:
        return ""
    try:
        envelope = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        for key in ("response", "result", "content", "text"):
            value = envelope.get(key)
            if isinstance(value, str):
                return value
        # A model may have emitted the requested payload directly.
        return stripped

    chunks: list[str] = []
    final = ""
    for line in stripped.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "message" and event.get("role") in (None, "assistant"):
            content = event.get("content")
            if isinstance(content, str):
                chunks.append(content)
        if event_type in {"result", "response"}:
            for key in ("response", "result", "content", "text"):
                value = event.get(key)
                if isinstance(value, str):
                    final = value
                    break
    return final or "".join(chunks)


def _extract_payload(
    output: str, *, required_keys: Optional[set[str]] = None
) -> Optional[dict]:
    text = _extract_response_text(output)
    return extract_json_from_text(text, required_keys=required_keys)


def check_available() -> tuple[bool, str]:
    """Probe capabilities and the existing account without requesting secrets."""
    bin_name = _gemini_bin()
    if not shutil.which(bin_name):
        return False, (
            f"Gemini CLI not found ({bin_name!r} not on PATH; install Gemini CLI "
            "and authenticate it directly)"
        )
    try:
        version_proc = subprocess.run(
            [bin_name, "--version"], capture_output=True, text=True, timeout=10
        )
        help_proc = subprocess.run(
            [bin_name, "--help"], capture_output=True, text=True, timeout=10
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, f"Gemini CLI capability probe failed: {exc}"
    version = ((version_proc.stdout or "") + (version_proc.stderr or "")).strip()
    help_text = (help_proc.stdout or "") + (help_proc.stderr or "")
    missing = [flag for flag in _REQUIRED_HELP_FLAGS if flag not in help_text]
    if version_proc.returncode != 0 or help_proc.returncode != 0 or missing:
        return False, f"Gemini CLI lacks required safe headless capabilities: {missing}"

    # Gemini CLI has no local `whoami`. A tiny plan+sandbox headless request is
    # the only reliable way to distinguish valid enterprise/API/Cloud state
    # from the now-ineligible consumer OAuth state.
    try:
        probe = subprocess.run(
            [
                bin_name,
                "--approval-mode", "plan",
                "--sandbox",
                "--skip-trust",
                "--output-format", "json",
                "--prompt", "Reply with exactly OK. Do not use tools.",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return False, "Gemini CLI account probe timed out"
    except (FileNotFoundError, OSError) as exc:
        return False, f"Gemini CLI account probe failed: {exc}"
    combined = (probe.stderr or "") + "\n" + (probe.stdout or "")
    if _tier_ineligible(combined):
        return False, (
            f"Gemini CLI {version or '(unknown version)'} is installed, but this "
            "consumer account tier moved to Antigravity; use an agy-* provider"
        )
    if probe.returncode != 0:
        detail = combined.strip().splitlines()
        return False, (
            f"Gemini CLI account is not ready: "
            f"{detail[0] if detail else f'exit {probe.returncode}'}"
        )
    return True, f"Gemini CLI {version}; persisted enterprise/API/Cloud account ready"


def _build_cmd(bin_name: str, model: str) -> list[str]:
    """Build a fail-closed plan+sandbox invocation using stream-json."""
    return [
        bin_name,
        "--model", model,
        "--approval-mode", "plan",
        "--sandbox",
        "--skip-trust",
        "--output-format", "stream-json",
    ]


def _write_log(log_file: Optional[Path], stdout: str, stderr: str) -> None:
    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def run(
    *,
    prompt: str,
    repo_path: Path,
    model: str,
    timeout_seconds: int = 1200,
    log_file: Optional[Path] = None,
    schema_path: Optional[Path] = None,
) -> GeminiResult:
    """Run Gemini CLI on stdin with mandatory plan mode and sandboxing."""
    started = time.monotonic()
    stdout = ""
    stderr = ""
    try:
        try:
            proc = subprocess.Popen(
                _build_cmd(_gemini_bin(), model),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(repo_path),
                env=os.environ.copy(),
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(
                    input=READ_ONLY_RULE + "\n\n" + prompt,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                kill_proc_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                stderr = (stderr or "") + f"\n[orchestrator] timeout after {timeout_seconds}s"
                return GeminiResult(
                    False, None, stdout or "", stderr, -1,
                    time.monotonic() - started,
                    f"timeout after {timeout_seconds}s",
                )
        except (FileNotFoundError, OSError) as exc:
            return GeminiResult(
                False, None, "", str(exc), -1, time.monotonic() - started,
                f"could not launch gemini: {exc}",
            )

        stdout = stdout or ""
        stderr = stderr or ""
        combined = stderr + "\n" + stdout
        if _tier_ineligible(combined):
            return GeminiResult(
                False, None, stdout, stderr, proc.returncode,
                time.monotonic() - started,
                "Gemini CLI account tier is ineligible; use an agy-* provider "
                "for consumer Google accounts",
            )
        if proc.returncode != 0:
            detail = (stderr or stdout or "no output").strip()
            return GeminiResult(
                False, None, stdout, stderr, proc.returncode,
                time.monotonic() - started,
                f"gemini exited with code {proc.returncode}: {detail[:300]}",
            )
        required_keys: Optional[set[str]] = None
        if schema_path is not None:
            try:
                required_keys = set(
                    json.loads(schema_path.read_text(encoding="utf-8")).get("required") or []
                )
            except (OSError, json.JSONDecodeError):
                required_keys = None
        payload = _extract_payload(stdout, required_keys=required_keys)
        if payload is None:
            empty = not stdout.strip()
            return GeminiResult(
                False, None, stdout, stderr, proc.returncode,
                time.monotonic() - started,
                "gemini returned empty output" if empty else
                "could not extract JSON object from Gemini CLI response",
                transient_empty=empty,
            )
        return GeminiResult(
            True, payload, stdout, stderr, proc.returncode,
            time.monotonic() - started,
        )
    finally:
        _write_log(log_file, stdout, stderr)
