# CLAUDE.md: guidance for agents working on this repo

## WHAT

MoA-X is a Mixture-of-Agents reference harness. `harness/scripts/run_moa.py`
orchestrates a config-driven roster of CLI proposers and broadcast
refiners; the shipped default is codex + glm + sonnet proposers and
codex-reviewer + qwen refiners (GLM and Qwen run on the `opencode` CLI;
Qwen uses the Token Plan API). Layer 0 (scout) is handled by the parent agent.
Layer 3 defaults to the parent Claude Code session using its rolling `opus`
alias, but the orchestrator can also run it as a recorded Codex/Claude
subprocess with `--phase layer3`. Layer 3 adds `final-plan.md` plus a
schema-validated `final-plan.json` provenance companion and refreshes the
self-contained report's decision-lineage explorer.

- `harness/`: orchestrator, adapters, prompts, schemas, and `report/`
  (HTML report template + vendored three.min.js). Designed to be droppable
  into `~/.claude/skills/mixture-of-agents/` as a Claude Code skill.
- `docs/`: topic-by-topic docs. Read the relevant one before structural changes:
  - `docs/install.md`: CLI install + skill install
  - `docs/usage.md`: `/mixture-of-agents` flow + standalone
  - `docs/config.md`: `.env` / `harness/config.yaml` precedence + knob table
  - `docs/architecture.md`: the four layers, why broadcast, why this roster (and why gemini left)
  - `docs/report.md`: the self-contained HTML run report
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
   `{codex-reviewer, qwen}` and the default aggregator uses Claude Code's
   `opus` alias, so verification is independent of both the Sonnet proposer
   and the Anthropic aggregator. If selecting the optional Codex aggregator,
   reconsider the reviewer roster because `codex-reviewer` then shares its
   harness/lab.
   The harness no longer enforces this (the data model became neutral
   when named providers landed — see `docs/architecture.md`); it's a
   recommendation, and the orchestrator warns when a refiner shares the
   aggregator's harness. If you change the default refiner set in a PR,
   justify it in the PR body.
2. **Don't commit `.moa/` session artifacts.** Already gitignored; just
   don't fight it.

## Soft defaults (open to change via PR)

- **Auth follows the underlying CLI.** Codex supports persisted API-key login,
  Claude accepts `ANTHROPIC_API_KEY`, Cursor accepts `CURSOR_API_KEY`, and
  OpenCode reads provider keys (`ZHIPU_API_KEY`, `MOONSHOT_API_KEY`,
  `FIREWORKS_API_KEY`, `QWEN_TOKEN_PLAN_API_KEY`, and others). The open gap is
  normalized usage/cost telemetry and safe pre-dispatch budget controls, not
  basic API-key authentication.
- **Default roster is `[codex, glm, sonnet]` proposers,
  `[codex-reviewer, qwen]` refiners, and `opus` aggregator** across harnesses
  `{codex, claude, opencode, cursor}`. The model defaults are
  `gpt-5.6-terra`, GLM-5.2, Claude Code's rolling `sonnet` alias,
  `gpt-5.6-sol` at high reasoning, Qwen `qwen3.8-max-preview`, and Claude
  Code's rolling `opus` alias. It's
  a default, not a cap — the roster is pure config (built-in names,
  `providers:` in config.yaml, the built-in `grok` (xAI Grok via opencode's
  native `xai` provider — needs `XAI_API_KEY`) and Qwen Token Plan providers, or
  the `MOA_PROVIDER_<NAME>` env shorthand). Tested recipes for DeepSeek,
  MiniMax, and Mistral are welcome; most are an opencode/cursor model
  string, but a new *harness* needs its own adapter — open an issue first.

## Config surface

Precedence, highest first: CLI flags, then shell env, then `.env`,
then `harness/config.yaml`, then built-in defaults. Loader lives at
`harness/scripts/config.py`. Full knob table in `docs/config.md`.

<!-- AGENT-MANAGED SECTION -->
<!-- Lifecycle for entries below: (1) write the full why/detail into the relevant
     doc or skill FIRST, (2) add a one-line RULE + one-clause tripwire + pointer
     here, (3) once the entry is stable, graduate it into the human section above
     and delete it here. Keep this section short — it's an inbox, not an archive. -->

## Discovered patterns

- **Don't guess opencode `run` flags — they contradict the published docs.** No
  `-q`/`--auto`; auto-approve is `--dangerously-skip-permissions`; no stdin
  (prompt via `-f`, big prompts overflow argv); `-f` is a greedy array so the
  message goes before it. → `opencode-headless-run-invocation` skill + `adapters/opencode.py`.
- **opencode model ids are `provider/model` strings.** Swap billing paths by
  overriding the model string (`MOA_GLM_MODEL=fireworks-ai/...`), not by adding a
  harness. → `docs/config.md`.
- **Cursor CLI binary was renamed `cursor-agent` → `agent`** (bare `cursor` is the
  IDE launcher). The adapter probes both; honor `MOA_CURSOR_BIN`. → `adapters/cursor.py`.
- **Schema-unenforced adapters (cursor, opencode) share one JSON extractor.**
  Change `adapters.extract_json_from_text` once, not per-adapter.
- **`gemini` is gone** and `resolve_provider('gemini')` raises a migration hint.
  Don't reintroduce it without fixing the flakes. → `docs/architecture.md`.
- **The HTML run report is a single self-contained file.** `report.py` inlines
  `harness/report/template.html` + vendored `three.min.js` + the session data —
  zero network requests (tests assert no external `src=`/`href=`). Two gotchas:
  Three.js is pinned to **r128 UMD** (later releases are ESM-only, which can't be
  inlined into a bare `<script>`); and the session JSON is embedded in a
  `<script type="application/json">` with `</` → `<\/` so a `</script>` inside a
  captured log can't terminate the tag early. → `docs/report.md`.
