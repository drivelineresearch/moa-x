"""Bounded, inference-free Muse model discovery through its native protocol."""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from adapters import kill_proc_tree
from adapters.native import muse_auth

_LAST_MODELS = {}


def list_models(timeout_seconds=15):
    """Return native model IDs and supported tiers, without exporting credentials."""
    global _LAST_MODELS
    deadline = time.monotonic() + timeout_seconds
    binary = os.environ.get("MOA_MUSE_BIN") or "muse"
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="moa-muse-catalog-") as tmp:
            root = Path(tmp)
            config = root / "config/muse"
            config.mkdir(parents=True)
            env = {key: value for key, value in os.environ.items() if not key.startswith("SLACK_")}
            env.update(XDG_CONFIG_HOME=str(root / "config"), XDG_CACHE_HOME=str(root / "cache"),
                       XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"))
            with muse_auth(config, min(deadline, time.monotonic() + .25)), tempfile.TemporaryFile() as stderr:
                proc = subprocess.Popen([binary, "serve", "--no-session-log", "--disable-shell", "--disable-write"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                        cwd=root, env=env, start_new_session=True)
                pending = b""
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)

                    def call(ident, method, params):
                        nonlocal pending
                        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}) + "\n").encode())
                        proc.stdin.flush()
                        while time.monotonic() < deadline:
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                event = json.loads(line)
                                if isinstance(event, dict) and event.get("id") == ident:
                                    if "error" in event:
                                        raise ValueError("Muse rejected the model catalog query")
                                    return event["result"]
                            if not selector.select(max(0, deadline - time.monotonic())):
                                break
                            data = os.read(proc.stdout.fileno(), 65536)
                            if not data:
                                raise ValueError("Muse closed the model catalog stream")
                            pending += data
                            if len(pending) > 2_000_000:
                                raise ValueError("Muse model catalog exceeded the size limit")
                        raise TimeoutError("Muse model catalog check timed out")

                    try:
                        call(1, "initialize", {"clientInfo": {"name": "moax_catalog", "version": "1"}})
                        proc.stdin.write(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
                        proc.stdin.flush()
                        result = call(2, "model/list", {})
                        if not isinstance(result, dict):
                            raise ValueError("Invalid Muse model catalog result")
                        rows = result["models"]
                        if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("modelId"), str) for row in rows):
                            raise ValueError("Invalid Muse model catalog")
                        models = {row["modelId"]: row for row in rows}
                        _LAST_MODELS = models
                        return True, models, f"Native catalog checked; {len(models)} text-model variants listed. Inference is checked when a run succeeds."
                    finally:
                        kill_proc_tree(proc, grace_seconds=0)
                        proc.wait(timeout=2)
                        proc.stdin.close()
                        proc.stdout.close()
    except RuntimeError as exc:
        if "credential lock" in str(exc) and _LAST_MODELS:
            return True, dict(_LAST_MODELS), "Cached native catalog; a Muse run is using the credential. Recheck after it finishes. Inference is checked by a run."
        return False, {}, "Muse credential is in use; recheck after the active native call finishes"
    except (OSError, ValueError, KeyError, TimeoutError, subprocess.SubprocessError):
        return False, {}, "Muse model catalog unavailable; recheck the local CLI account and connection"
    finally:
        if proc is not None and proc.poll() is None:
            kill_proc_tree(proc, grace_seconds=0)
