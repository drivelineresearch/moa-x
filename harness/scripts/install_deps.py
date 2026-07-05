#!/usr/bin/env python3
"""install_deps.py — config-aware preflight for the mixture-of-agents skill.

Loads the resolved config (harness/config.yaml + .env + built-in defaults via
the same path run_moa.py uses) and verifies coherence:

  - Which harnesses are actually needed (proposers + refiners union)
  - Each needed harness's check_available() (CLI present + auth probe)
  - Schema-pattern coherence: every resolved provider name matches the
    regex pattern in proposer/refiner schemas. Catches the kind of
    runtime mismatch that surfaced when user-named providers ran
    against schemas hardcoded to a fixed provider set.
  - Cursor-only model-availability: cross-checks each cursor provider's
    `model:` against `cursor-agent --list-models`. Cursor uses machine
    ids (gpt-5.5-medium, grok-4-20) that differ from friendly names —
    this catches typos before a real run wastes wall-clock.
  - Skill assets and schema strict-mode lint.

Harnesses NOT referenced by any provider in the resolved layers are
skipped. Reported as 'unused' at the end so users aren't confused.

Run with any system Python:
    python3 harness/scripts/install_deps.py   # from the moa-x repo root
    # or
    python3 ~/.claude/skills/mixture-of-agents/scripts/install_deps.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Add scripts/ to sys.path so adapters and config import. install_deps lives in
# scripts/, so its parent IS scripts/parent — but we add scripts/ explicitly
# so 'from adapters import ...' works the same as in run_moa.py.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import config as harness_config  # noqa: E402

if TYPE_CHECKING:
    from config import LoadedConfig, ResolvedProvider


ALL_HARNESSES = ("codex", "claude", "cursor", "opencode")


def _check(label: str, cmd: list[str]) -> tuple[bool, str]:
    """Run a quick command and return (ok, version_or_error_string)."""
    import shutil as _shutil
    if not _shutil.which(cmd[0]):
        return False, f"{cmd[0]} not on PATH"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, f"{label} version check timed out"
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, (output[0] if output else "(no output)")


def _check_python(failures: list[str]) -> None:
    py_version = sys.version_info
    if py_version < (3, 9):
        print(f"  python: {py_version.major}.{py_version.minor} — FAIL (need 3.9+)")
        failures.append("python<3.9")
    else:
        print(f"  python: {py_version.major}.{py_version.minor}.{py_version.micro} — OK")


def _print_provider_summary(loaded_cfg: "LoadedConfig") -> None:
    print("")
    print("  resolved providers (from harness/config.yaml + builtins):")
    print("    proposers: " + (
        ", ".join(f"{p.name} ({p.harness} → {p.model})" for p in loaded_cfg.proposers)
        or "(none)"
    ))
    print("    refiners:  " + (
        ", ".join(f"{p.name} ({p.harness} → {p.model})" for p in loaded_cfg.refiners)
        or "(none — refinement skipped)"
    ))


def _check_needed_harnesses(loaded_cfg: "LoadedConfig", failures: list[str]) -> set[str]:
    """Run check_available() per needed harness; return the set we checked."""
    # Mirror run_moa's preflight: when refinement is skipped, the refiner
    # harnesses are never used, so don't gate the run on installing them.
    providers = list(loaded_cfg.proposers)
    if not loaded_cfg.skip_refinement:
        providers += loaded_cfg.refiners
    needed = {p.harness for p in providers}
    print("")
    print(f"  required harnesses (from layer assignments): {sorted(needed)}")

    # Lazy-import adapters so missing optional deps don't crash the whole script
    from adapters import codex as codex_adapter
    from adapters import claude as claude_adapter
    from adapters import cursor as cursor_adapter
    from adapters import opencode as opencode_adapter

    adapter_for = {
        "codex": codex_adapter,
        "claude": claude_adapter,
        "cursor": cursor_adapter,
        "opencode": opencode_adapter,
    }
    install_hint = {
        "codex":  "npm i -g @openai/codex && codex login",
        "claude": "see https://docs.claude.com/en/docs/claude-code/quickstart",
        "cursor": "curl https://cursor.com/install -fsS | bash  (then: cursor-agent login)",
        "opencode": "curl -fsSL https://opencode.ai/install | bash  (then: opencode auth login, "
                    "or export ZHIPU_API_KEY / MOONSHOT_API_KEY / FIREWORKS_API_KEY)",
    }

    for harness in sorted(needed):
        adapter = adapter_for.get(harness)
        if adapter is None:
            print(f"  harness {harness}: FAIL — unknown harness (no adapter)")
            failures.append(f"unknown harness {harness}")
            continue
        ok, msg = adapter.check_available()
        if ok:
            print(f"  harness {harness}: OK — {msg}")
        else:
            print(f"  harness {harness}: FAIL — {msg}")
            print(f"    fix: {install_hint.get(harness, '(no hint available)')}")
            failures.append(f"{harness} preflight")

    return needed


def _check_schema_coherence(loaded_cfg: "LoadedConfig", failures: list[str]) -> None:
    """Verify every resolved provider name matches the agent_id pattern in
    proposer.schema.json and the proposer-id pattern in refiner.schema.json.

    Catches runtime mismatches like the c-gpt/c-gemini/c-opus case where
    user-named providers were rejected by hardcoded enums."""
    print("")
    print("  schema coherence (provider names vs schema patterns):")

    proposer_schema_path = SCRIPT_DIR / "schemas" / "proposer.schema.json"
    refiner_schema_path = SCRIPT_DIR / "schemas" / "refiner.schema.json"

    try:
        proposer_schema = json.loads(proposer_schema_path.read_text(encoding="utf-8"))
        refiner_schema = json.loads(refiner_schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"    FAIL — could not load schemas: {e}")
        failures.append("schema load")
        return

    proposer_pattern = (
        proposer_schema.get("properties", {}).get("agent_id", {}).get("pattern")
    )
    if not proposer_pattern:
        print("    FAIL — proposer schema has no agent_id pattern (was the regex relaxation reverted?)")
        failures.append("missing proposer.agent_id pattern")
        return

    proposer_re = re.compile(proposer_pattern)
    bad_proposer_names = [p.name for p in loaded_cfg.proposers if not proposer_re.fullmatch(p.name)]
    if bad_proposer_names:
        print(f"    proposer.agent_id pattern {proposer_pattern!r}: FAIL")
        print(f"      names that violate pattern: {bad_proposer_names}")
        print("      fix: rename providers in harness/config.yaml to match the pattern (lowercase, dash-separated, ≤32 chars)")
        failures.append("proposer name pattern")
    else:
        print(f"    proposer.agent_id pattern {proposer_pattern!r}: OK ({len(loaded_cfg.proposers)} names)")

    # All five proposer-id reference sites in the refiner schema use the same pattern.
    # We sample one (refiner.agent_id) to derive it, then assume the five sites match —
    # if they don't, the strict-mode lint or a future test will catch it.
    refiner_pattern = (
        refiner_schema.get("properties", {}).get("agent_id", {}).get("pattern")
    )
    if not refiner_pattern:
        print("    FAIL — refiner schema has no agent_id pattern")
        failures.append("missing refiner.agent_id pattern")
        return

    refiner_re = re.compile(refiner_pattern)
    # Refiners reference proposers in reviewing[], per_proposer_verdicts[].proposer, etc.
    # so check both refiner names AND that the proposer names match the refiner's regex
    # (in practice the patterns are identical, but compute defensively).
    bad_refiner_names = [p.name for p in loaded_cfg.refiners if not refiner_re.fullmatch(p.name)]
    bad_proposer_refs = [p.name for p in loaded_cfg.proposers if not refiner_re.fullmatch(p.name)]
    if bad_refiner_names or bad_proposer_refs:
        print(f"    refiner agent/proposer pattern {refiner_pattern!r}: FAIL")
        if bad_refiner_names:
            print(f"      refiner names violating pattern: {bad_refiner_names}")
        if bad_proposer_refs:
            print(f"      proposer names that refiners would echo: {bad_proposer_refs}")
        failures.append("refiner pattern")
    else:
        n = len(loaded_cfg.refiners) + len(loaded_cfg.proposers)
        print(f"    refiner agent/proposer pattern {refiner_pattern!r}: OK ({n} names)")


def _check_cursor_models(loaded_cfg: "LoadedConfig", needed: set[str], failures: list[str]) -> None:
    """For each cursor-routed provider, verify its model is in `cursor-agent --list-models`.

    Cursor uses machine ids that diverge from the friendly names on
    cursor.com/docs/models — this catches the most common typo class
    (gpt-5.5 vs gpt-5.5-medium, grok-4.20 vs grok-4-20)."""
    if "cursor" not in needed:
        return
    cursor_providers = [p for p in loaded_cfg.proposers + loaded_cfg.refiners if p.harness == "cursor"]
    if not cursor_providers:
        return

    print("")
    print("  cursor model availability (probe via --list-models):")

    # Resolve the binary exactly as the adapter does (honors MOA_CURSOR_BIN,
    # else probes cursor-agent → agent).
    from adapters import cursor as cursor_adapter
    bin_name = cursor_adapter._cursor_bin()

    try:
        proc = subprocess.run(
            [bin_name, "--list-models"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"    FAIL — could not probe cursor models: {e}")
        failures.append("cursor --list-models")
        return

    if proc.returncode != 0:
        print(f"    FAIL — cursor-agent --list-models exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:200]}")
        failures.append("cursor --list-models exit")
        return

    # Output format: one model per line as "<machine-id> - <Friendly Name>"
    # Some lines are headers (e.g. "Available models") or blank.
    available_ids: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        machine_id = line.split(" - ", 1)[0].strip()
        if machine_id:
            available_ids.add(machine_id)

    if not available_ids:
        print("    WARN — cursor-agent --list-models returned no parseable rows; skipping model check")
        return

    seen_models: set[str] = set()
    for p in cursor_providers:
        if p.model in seen_models:
            continue
        seen_models.add(p.model)
        if p.model in available_ids:
            print(f"    {p.name} → {p.model}: OK")
        else:
            print(f"    {p.name} → {p.model}: FAIL — not in --list-models output")
            print(f"      fix: run 'cursor-agent --list-models' to see the {len(available_ids)} ids your account can use")
            failures.append(f"cursor model {p.model}")


def _check_assets(failures: list[str]) -> None:
    skill_dir = SCRIPT_DIR.parent
    required = [
        skill_dir / "SKILL.md",
        skill_dir / "scripts" / "run_moa.py",
        skill_dir / "scripts" / "adapters" / "codex.py",
        skill_dir / "scripts" / "adapters" / "opencode.py",
        skill_dir / "scripts" / "adapters" / "claude.py",
        skill_dir / "scripts" / "adapters" / "cursor.py",
        skill_dir / "scripts" / "schemas" / "proposer.schema.json",
        skill_dir / "scripts" / "schemas" / "refiner.schema.json",
        skill_dir / "prompts" / "scout.md",
        skill_dir / "prompts" / "proposer.md",
        skill_dir / "prompts" / "refiner.md",
        skill_dir / "prompts" / "aggregator.md",
    ]
    print("")
    print("  skill assets:")
    for path in required:
        rel = path.relative_to(skill_dir)
        if path.exists():
            print(f"    {rel}: OK")
        else:
            print(f"    {rel}: MISSING")
            failures.append(f"asset {rel}")


def _check_strict_lint(failures: list[str]) -> None:
    print("")
    print("  schema strict-mode lint:")
    try:
        import run_moa  # noqa: E402
    except ImportError as e:
        print(f"    SKIPPED — could not import run_moa: {e}")
        failures.append("run_moa import")
        return

    for label, schema_name in (("proposer", "proposer.schema.json"), ("refiner", "refiner.schema.json")):
        schema_path = SCRIPT_DIR / "schemas" / schema_name
        if not schema_path.exists():
            continue
        try:
            schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"    {label}: FAIL (load error: {e})")
            failures.append(f"schema lint {label} load")
            continue
        violations = run_moa.lint_schema_openai_strict(schema_doc)
        if violations:
            print(f"    {label}: FAIL ({len(violations)} strict-mode violations)")
            for v in violations[:3]:
                print(f"      - {v[:180]}")
            failures.append(f"schema lint {label}")
        else:
            print(f"    {label}: OK (strict-mode clean)")


def main() -> int:
    print("Mixture-of-Agents skill — config-aware preflight")
    print("=" * 60)

    failures: list[str] = []

    _check_python(failures)

    # Load resolved config. Falls back to built-in defaults if no config.yaml.
    try:
        harness_config.apply_config_to_env()
        loaded_cfg = harness_config.load_resolved_config()
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"  config: FAIL — {e}")
        print("  fix: check harness/config.yaml syntax (see harness/config.example.yaml)")
        return 1

    _print_provider_summary(loaded_cfg)
    needed = _check_needed_harnesses(loaded_cfg, failures)
    _check_schema_coherence(loaded_cfg, failures)
    _check_cursor_models(loaded_cfg, needed, failures)
    _check_assets(failures)
    _check_strict_lint(failures)

    unused = set(ALL_HARNESSES) - needed
    if unused:
        print("")
        print(f"  unused harnesses (not checked): {sorted(unused)}")

    print("")
    print("=" * 60)
    if not failures:
        print("All checks passed. /mixture-of-agents is ready to run.")
        return 0
    print(f"{len(failures)} issue(s) — fix the items above before invoking /mixture-of-agents:")
    for f in failures:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
