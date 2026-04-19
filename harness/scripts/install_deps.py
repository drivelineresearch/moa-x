#!/usr/bin/env python3
"""install_deps.py — bootstrap the mixture-of-agents skill.

Verifies that the external CLIs (codex, gemini, claude) are installed and
reachable, and prints clear instructions for the user to authenticate them.
The orchestrator itself uses only the Python standard library, so there is
no skill-local venv to provision.

Run with any system Python:
    python3 harness/scripts/install_deps.py   # from the moa-x repo root
    # or
    python3 ~/.claude/skills/mixture-of-agents/scripts/install_deps.py   # from the installed skill location
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def _check(label: str, cmd: list[str]) -> tuple[bool, str]:
    """Run a quick command and return (ok, version_or_error_string)."""
    if not shutil.which(cmd[0]):
        return False, f"{cmd[0]} not on PATH"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, f"{label} version check timed out"
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, (output[0] if output else "(no output)")


def main() -> int:
    print("Mixture-of-Agents skill — dependency check")
    print("=" * 60)

    failures = 0

    # ---- Python ----
    py_version = sys.version_info
    if py_version < (3, 9):
        print(f"  python: {py_version.major}.{py_version.minor} — FAIL (need 3.9+)")
        failures += 1
    else:
        print(f"  python: {py_version.major}.{py_version.minor}.{py_version.micro} — OK")

    # ---- codex CLI ----
    ok, info = _check("codex", ["codex", "--version"])
    if ok:
        print(f"  codex CLI: {info} — OK")
        auth_file = Path.home() / ".codex" / "auth.json"
        if auth_file.exists():
            print("    auth: ~/.codex/auth.json present — OK")
        else:
            print("    auth: ~/.codex/auth.json MISSING")
            print("    fix:  codex login")
            failures += 1
    else:
        print(f"  codex CLI: {info} — FAIL")
        print("    fix:  npm i -g @openai/codex && codex login")
        failures += 1

    # ---- gemini CLI ----
    ok, info = _check("gemini", ["gemini", "--version"])
    if ok:
        print(f"  gemini CLI: {info} — OK")
        gemini_dir = Path.home() / ".gemini"
        if gemini_dir.exists():
            print("    auth: ~/.gemini/ present — OK (login state inside)")
        else:
            print("    auth: ~/.gemini/ MISSING")
            print("    fix:  gemini  (run interactively once to log in)")
            failures += 1
    else:
        print(f"  gemini CLI: {info} — FAIL")
        print("    fix:  npm i -g @google/gemini-cli")
        failures += 1

    # ---- claude CLI (for sonnet proposer) ----
    ok, info = _check("claude", ["claude", "--version"])
    if ok:
        print(f"  claude CLI: {info} — OK")
        claude_dir = Path.home() / ".claude"
        has_oauth = claude_dir.exists() and (claude_dir / "settings.json").exists()
        if has_oauth:
            print("    auth: ~/.claude/settings.json present (subscription OAuth) — OK")
        else:
            print("    auth: ~/.claude/settings.json MISSING")
            print("    fix:  claude  (run interactively once to complete subscription login)")
            print("    note: API keys (ANTHROPIC_API_KEY) are not supported by MoA-X — subscription OAuth only")
            failures += 1
    else:
        print(f"  claude CLI: {info} — FAIL")
        print("    fix:  see https://docs.claude.com/en/docs/claude-code/quickstart")
        failures += 1

    # ---- skill assets ----
    skill_dir = Path(__file__).resolve().parent.parent
    required = [
        skill_dir / "SKILL.md",
        skill_dir / "scripts" / "run_moa.py",
        skill_dir / "scripts" / "adapters" / "codex.py",
        skill_dir / "scripts" / "adapters" / "gemini.py",
        skill_dir / "scripts" / "adapters" / "claude.py",
        skill_dir / "scripts" / "schemas" / "proposer.schema.json",
        skill_dir / "scripts" / "schemas" / "refiner.schema.json",
        skill_dir / "prompts" / "scout.md",
        skill_dir / "prompts" / "proposer.md",
        skill_dir / "prompts" / "refiner.md",
        skill_dir / "prompts" / "aggregator.md",
    ]
    for path in required:
        if path.exists():
            print(f"  asset: {path.relative_to(skill_dir)} — OK")
        else:
            print(f"  asset: {path.relative_to(skill_dir)} — MISSING")
            failures += 1

    # ---- schema strict-mode lint ----
    import sys as _sys
    _sys.path.insert(0, str(skill_dir / "scripts"))
    try:
        import run_moa  # noqa: E402
        for schema_label, schema_path in (
            ("proposer", skill_dir / "scripts" / "schemas" / "proposer.schema.json"),
            ("refiner", skill_dir / "scripts" / "schemas" / "refiner.schema.json"),
        ):
            if not schema_path.exists():
                continue
            try:
                schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"  schema lint: {schema_label} — FAIL (load error: {e})")
                failures += 1
                continue
            violations = run_moa.lint_schema_openai_strict(schema_doc)
            if violations:
                print(f"  schema lint: {schema_label} — FAIL ({len(violations)} strict-mode violations)")
                for v in violations[:3]:
                    print(f"    - {v[:180]}")
                failures += 1
            else:
                print(f"  schema lint: {schema_label} — OK (strict-mode clean)")
    except ImportError as e:
        print(f"  schema lint: SKIPPED (could not import run_moa: {e})")
        failures += 1

    print("=" * 60)
    if failures == 0:
        print("All checks passed. /mixture-of-agents is ready to run.")
        return 0
    print(f"{failures} issue(s) — fix the items above before invoking /mixture-of-agents.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
