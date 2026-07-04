# CLAUDE.md: guidance for agents working on this repo

## WHAT

MoA-X is a Mixture-of-Agents reference harness. `harness/scripts/run_moa.py`
orchestrates three CLI proposers (codex + gemini + claude/sonnet) and
two broadcast refiners (codex + gemini). Layers 0 (scout) and 3
(aggregation) are handled by the parent Claude Code session. The
orchestrator only runs Layers 1 and 2.

- `harness/`: orchestrator, adapters, prompts, schemas. Designed to be
  droppable into `~/.claude/skills/mixture-of-agents/` as a Claude Code skill.
- `docs/`: topic-by-topic docs. Read the relevant one before structural changes:
  - `docs/install.md`: CLI install + skill install
  - `docs/usage.md`: `/mixture-of-agents` flow + standalone
  - `docs/config.md`: `.env` / `harness/config.yaml` precedence + knob table
  - `docs/architecture.md`: the four layers, why broadcast, why these providers
- `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`: community files.

## WHY

The harness produces **repo-grounded implementation plans** via a
cross-lab ensemble, not chat answers. The primary runner is Claude
Code; the orchestrator runs fine from a shell, but that's the less
well-trodden path. Cross-lab diversity at the refiner and aggregator
layers is load-bearing: the whole design argument is that one lab's
blind spots tend to get caught by another lab's model.

## HOW

```bash
# Verify toolchain (checks codex/gemini/claude install + auth)
python3 harness/scripts/install_deps.py

# Offline tests (23/23 must pass; no network, no external CLIs)
python3 harness/scripts/test_offline.py

# Run the skill (inside Claude Code, from any project dir)
/mixture-of-agents
```

PR workflow: branch → push → PR → merge. Never push to `main`. New
tests must run offline so CI stays credential-free.

## Hard rules

Rule 2 is non-negotiable. Rule 1 is a strong recommendation.

1. **Recommend lab-independent refiners.** Layer 2 defaults to
   `{codex, gemini}` and the aggregator is Opus, so verification is
   independent of both the Sonnet proposer and the Opus aggregator.
   The harness no longer enforces this (the data model became neutral
   when named providers landed — see `docs/architecture.md`); it's a
   recommendation. If you change the default refiner set in a PR,
   justify it in the PR body.
2. **Don't commit `.moa/` session artifacts.** Already gitignored; just
   don't fight it.

## Soft defaults (open to change via PR)

- **Default auth is subscription CLI.** That's what I run and what
  the docs lead with. API-billed auth (`ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `GEMINI_API_KEY`) is a welcome contribution path.
  The claude adapter already accepts either. PRs that make API-billed
  runs first-class (cost accounting in the manifest, a `MOA_MAX_COST`
  ceiling, docs updates) are wanted.
- **Default provider set is `{codex, claude-code, gemini}`.** It's
  the set the orchestrator, preflight, and prompts are shaped around.
  It's a default, not a cap. Other providers (OpenCode-driven models,
  Chinese-lab frontier models like DeepSeek / Qwen / Kimi / GLM /
  MiniMax, xAI, Mistral) are on the PR wishlist. Open an issue first
  so we can talk through adapter shape.

## Config surface

Precedence, highest first: CLI flags, then shell env, then `.env`,
then `harness/config.yaml`, then built-in defaults. Loader lives at
`harness/scripts/config.py`. Full knob table in `docs/config.md`.

<!-- AGENT-MANAGED SECTION -->
<!-- Agents may append discovered patterns, gotchas, and conventions below. -->

## Discovered patterns

_None yet. Add entries here as you find non-obvious things._
