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

1. **Subscription-CLI auth only.** No `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, or `GEMINI_API_KEY` in the MoA pipeline. If a
   proposed change routes through an API key, it's the wrong change.
2. **Supported providers are `codex`, `claude-code`, and `gemini`.**
   Not a TODO. Adding a provider requires a design discussion first;
   see `CONTRIBUTING.md`.
3. **Preserve lab independence at refinement and aggregation.** Layer
   2 uses `{codex, gemini}` and the aggregator is Opus so verification
   is independent of both the Sonnet proposer and the Opus aggregator.
   Moving Sonnet into Layer 2 defeats the design.
4. **Don't commit `.moa/` session artifacts.** Already gitignored; just
   don't fight it.

## Config surface

Precedence, highest first: CLI flags, then shell env, then `.env`,
then `harness/config.yaml`, then built-in defaults. Loader lives at
`harness/scripts/config.py`. Full knob table in `docs/config.md`.

<!-- AGENT-MANAGED SECTION -->
<!-- Agents may append discovered patterns, gotchas, and conventions below. -->

## Discovered patterns

_None yet. Add entries here as you find non-obvious things._
