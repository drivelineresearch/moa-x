"""Vendor adapters for mixture-of-agents external CLIs.

Each adapter handles one CLI's quirks: how to invoke it non-interactively,
how to extract the JSON payload from its output wrapper, how to retry on
schema validation failure, and how to surface errors. Adapters do NOT
import each other -- they are independent so a broken gemini install does
not break codex runs.

Shared helpers (used by all 3 adapters) live at the bottom of this file
so there is no circular-import risk — adapters import from the package,
not from each other.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

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
# workflow (the codex/gemini/claude CLIs are best supported there).
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
