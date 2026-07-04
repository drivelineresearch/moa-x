# AGENTS.md

MoA-X keeps a single source of agent guidance in **[CLAUDE.md](CLAUDE.md)** —
it applies to every tool that reads an agent instructions file (Codex,
OpenCode, Cursor, Zed, Claude Code). Read it before making changes.

It covers:

- **WHAT** — the project map: `harness/` (orchestrator, adapters, prompts,
  schemas) and `docs/` (topic-by-topic).
- **WHY** — the cross-lab design: repo-grounded plans from a four-lab ensemble,
  and why refiner/aggregator lab-independence is load-bearing.
- **HOW** — the workflow: `install_deps.py` preflight, offline `test_offline.py`
  (must pass, no network), and the branch → PR → merge rule (never push `main`).

Two hard rules worth stating up front (details in CLAUDE.md): recommend
lab-independent refiners, and never commit `.moa/` session artifacts.
