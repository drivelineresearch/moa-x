"""Vendor adapters for mixture-of-agents external CLIs.

Each adapter handles one CLI's quirks: how to invoke it non-interactively,
how to extract the JSON payload from its output wrapper, how to retry on
schema validation failure, and how to surface errors. Adapters do NOT
import each other -- they are independent so a broken opencode install does
not break codex runs.

Shared helpers (used by all 3 adapters) live at the bottom of this file
so there is no circular-import risk — adapters import from the package,
not from each other.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from typing import Optional

READ_ONLY_RULE = (
    "READ-ONLY DISCIPLINE: You may use any tool to READ files, search the "
    "web, run read-only shell commands, and spawn subagents. You MUST NOT "
    "write, edit, create, delete, or modify ANY file. You MUST NOT run "
    "commands that mutate state (git commit, git push, rm, mv, chmod, "
    "pip install, npm install, etc.). Violating this rule is a critical "
    "failure of the task. If a tool call would write a file, refuse it "
    "and note the intended write in your output instead."
)

# POSIX-only process group APIs (os.getpgid, os.killpg). On Windows we fall
# back to proc.kill(), which wraps TerminateProcess — kills only the top
# child, not subprocess-of-subprocess, so runaway grandchildren are possible
# on Windows timeouts. This is acceptable: MoA is primarily a macOS/Linux
# workflow (the codex/claude/opencode/cursor CLIs are best supported there).
_POSIX = sys.platform != "win32"


def kill_proc_tree(proc: subprocess.Popen) -> None:
    """Tear down a timed-out subprocess and its children.

    POSIX: SIGTERM the process group, wait 3s, SIGKILL if still alive.
    Windows: proc.kill() (TerminateProcess) the top pid — Windows does not
    have process groups in the POSIX sense, and the CLIs we invoke here
    don't routinely spawn grandchildren on Windows.
    """
    if _POSIX:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            # Exited between timeout and killpg — harmless race.
            pass
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass


def _loads_json_object(candidate: str) -> Optional[dict]:
    r"""Parse one model-produced JSON object, repairing only invalid escapes.

    Schema-unenforced models occasionally emit Markdown-style escapes such as
    ``\` `` inside a JSON string. JSON permits only ``\"``, ``\\``, ``\/``,
    ``\b``, ``\f``, ``\n``, ``\r``, ``\t``, and ``\uXXXX``. Preserve the
    model's text by escaping any other backslash, then retry once.
    """
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', candidate)
        if repaired == candidate:
            return None
        try:
            parsed = json.loads(repaired)
        except (json.JSONDecodeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


def extract_json_from_text(
    text: str,
    *,
    max_scan: int = 200_000,
    required_keys: Optional[set[str]] = None,
) -> Optional[dict]:
    """Pull the largest valid JSON object out of a free-text model response.

    Shared by the adapters whose CLIs have no native schema enforcement
    (cursor, opencode) — their model text may wrap the payload in markdown
    fences or surround it with prose. Strategy, longest-match-first:

      0. Fast path: if the whole (stripped) text is a single JSON object,
         return it. This is the common case (the model output IS the JSON)
         and it works regardless of size — important because the balanced
         scan below is windowed and would otherwise miss a bare object whose
         opening brace falls before the window.
      1. Collect the contents of every ```json ... ``` (or bare ``` ... ```)
         fenced block.
      2. Scan for balanced top-level `{...}` objects, respecting strings and
         escapes so braces inside string literals don't miscount depth.
      3. Try to parse each candidate longest-first, with one conservative
         repair pass for invalid JSON string escapes.
      4. When `required_keys` is provided, reject nested objects that happen
         to parse but are not the requested top-level payload.

    `max_scan` caps the balanced-object scan to the LAST N characters. The
    scan is O(n²) in the worst case and an embedded payload sits near the end
    of the response, so this keeps a multi-hundred-KB tool-use log from
    stalling the parser. Returns None if nothing parses.
    """
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("{"):
        whole = _loads_json_object(stripped)
        if isinstance(whole, dict) and (
            not required_keys or required_keys.issubset(whole)
        ):
            return whole

    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL):
        candidates.append(match.group(1).strip())

    scan_text = text[-max_scan:] if len(text) > max_scan else text
    for start in range(len(scan_text)):
        if scan_text[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for end in range(start, len(scan_text)):
            ch = scan_text[end]
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
                    candidates.append(scan_text[start : end + 1])
                    break

    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        parsed = _loads_json_object(cand)
        if isinstance(parsed, dict) and (
            not required_keys or required_keys.issubset(parsed)
        ):
            return parsed

    return None
