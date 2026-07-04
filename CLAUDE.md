# CLAUDE.md: guidance for agents working on this repo

## WHAT

MoA-X is a Mixture-of-Agents reference harness. `harness/scripts/run_moa.py`
orchestrates a config-driven roster of CLI proposers and broadcast
refiners; the shipped default is codex + glm + sonnet proposers and
codex + kimi refiners (glm/kimi run through the `opencode` CLI). Layers 0
(scout) and 3 (aggregation) are handled by the parent Claude Code session.
The orchestrator only runs Layers 1 and 2.

- `harness/`: orchestrator, adapters, prompts, schemas. Designed to be
  droppable into `~/.claude/skills/mixture-of-agents/` as a Claude Code skill.
- `docs/`: topic-by-topic docs. Read the relevant one before structural changes:
  - `docs/install.md`: CLI install + skill install
  - `docs/usage.md`: `/mixture-of-agents` flow + standalone
  - `docs/config.md`: `.env` / `harness/config.yaml` precedence + knob table
  - `docs/architecture.md`: the four layers, why broadcast, why this roster (and why gemini left)
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
# Verify toolchain (config-aware: checks the harnesses your roster needs + auth)
python3 harness/scripts/install_deps.py

# Offline tests (all must pass; no network, no external CLIs)
python3 harness/scripts/test_offline.py

# Run the skill (inside Claude Code, from any project dir)
/mixture-of-agents
```

PR workflow: branch → push → PR → merge. Never push to `main`. New
tests must run offline so CI stays credential-free.

## Hard rules

Rule 2 is non-negotiable. Rule 1 is a strong recommendation.

1. **Recommend lab-independent refiners.** Layer 2 defaults to
   `{codex, kimi}` and the aggregator is Opus, so verification is
   independent of both the Sonnet proposer and the Opus aggregator.
   The harness no longer enforces this (the data model became neutral
   when named providers landed — see `docs/architecture.md`); it's a
   recommendation, and the orchestrator warns when a refiner shares the
   aggregator's harness. If you change the default refiner set in a PR,
   justify it in the PR body.
2. **Don't commit `.moa/` session artifacts.** Already gitignored; just
   don't fight it.

## Soft defaults (open to change via PR)

- **Default auth is subscription CLI (plus provider keys for opencode).**
  codex/claude/cursor lead with subscription login; opencode reads
  provider API keys (`ZHIPU_API_KEY` / `MOONSHOT_API_KEY` /
  `FIREWORKS_API_KEY`) so GLM/Kimi are API-billable today. Making
  codex/claude API-billed runs first-class (`OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY`, cost accounting, a `MOA_MAX_COST` ceiling) is
  still wanted.
- **Default roster is `[codex, glm, sonnet]` proposers, `[codex, kimi]`
  refiners** across harnesses `{codex, claude, opencode, cursor}`. It's
  a default, not a cap — the roster is pure config (built-in names,
  `providers:` in config.yaml, or the `MOA_PROVIDER_<NAME>` env
  shorthand). More providers (DeepSeek / Qwen / MiniMax / xAI / Mistral)
  are welcome; most are an opencode/cursor model string, but a new
  *harness* needs its own adapter — open an issue first.

## Config surface

Precedence, highest first: CLI flags, then shell env, then `.env`,
then `harness/config.yaml`, then built-in defaults. Loader lives at
`harness/scripts/config.py`. Full knob table in `docs/config.md`.

<!-- AGENT-MANAGED SECTION -->
<!-- Agents may append discovered patterns, gotchas, and conventions below. -->

## Discovered patterns

- **opencode has no stdin and argv is ARG_MAX-capped (~128KB on Linux).**
  The opencode adapter writes the prompt to a file and passes it with `-f`
  plus a short positional instruction. Don't try to pass big refiner prompts
  (scout brief + every proposer output) on argv — they overflow.
- **opencode model ids are `provider/model` strings** (`zhipuai/glm-5.2`,
  `moonshotai/kimi-k2.7-code`, `fireworks-ai/accounts/fireworks/models/glm-5p2`).
  Swap billing paths by overriding the model string (`MOA_GLM_MODEL=...`), not
  by adding a harness.
- **The Cursor CLI binary was renamed `cursor-agent` → `agent`** (the bare
  `cursor` is the IDE launcher, not the agent). `cursor._cursor_bin()` probes
  `cursor-agent` then `agent`; honor `MOA_CURSOR_BIN` to pin one.
- **Schema-unenforced adapters (cursor, opencode) share
  `adapters.extract_json_from_text`.** If you touch JSON extraction, change it
  there once, not per-adapter.
- **`gemini` is gone.** `config.resolve_provider('gemini')` raises a targeted
  migration hint pointing at the cursor harness. Don't reintroduce a gemini
  built-in without addressing the flakes documented in `docs/architecture.md`.
