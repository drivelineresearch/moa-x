# CLAUDE.md: guidance for agents working on this repo

## WHAT

MoA-X is a Mixture-of-Agents reference harness. `harness/scripts/run_moa.py`
orchestrates a config-driven roster of CLI proposers and broadcast
refiners; the shipped default is Gemini Pro + Grok 4.5 + GPT-5.6 Luna proposers
and Qwen + Kimi K3 + Claude Opus refiners (Qwen uses the Token Plan API).
Layer 0 (scout) is handled by the parent agent. Layer 3 defaults to a recorded
Codex subprocess on `gpt-5.6-sol` at `xhigh` reasoning (stable provider name
`codex-sol`) through `--phase layer3`. Layer 3 adds `final-plan.md` plus a
schema-validated `final-plan.json` provenance companion and refreshes the
self-contained report's decision-lineage explorer.

- `harness/`: orchestrator, adapters, prompts, schemas, `report/`
  (HTML report template + embedded illustration assets), and `webui/` (Flask
  control plane, SQLite store, worker, and frontend). The CLI/skill assets are designed to be droppable
  into `~/.claude/skills/mixture-of-agents/` as a Claude Code skill.
- `docs/`: topic-by-topic docs. Read the relevant one before structural changes:
  - `docs/install.md`: CLI install + skill install
  - `docs/usage.md`: `/mixture-of-agents` flow + standalone
  - `docs/config.md`: `.env` / `harness/config.yaml` precedence + knob table
  - `docs/architecture.md`: the four layers, why broadcast, and why this roster
  - `docs/report.md`: the self-contained HTML run report
  - `docs/webui.md`: local control room, XDG storage, uploads, profiles,
    allowlisted GitHub workspaces, and trusted-network boundary
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
   `{qwen, kimi, opus}` and the default aggregator uses GPT-5.6 Sol at `xhigh`
   under the stable `codex-sol` provider name. All three reviewers are
   independent of the OpenAI aggregator and of the default proposer labs.
   If changing the aggregator, reconsider
   the reviewer roster so no refiner shares its harness/lab.
   The harness no longer enforces this (the data model became neutral
   when named providers landed — see `docs/architecture.md`); it's a
   recommendation, and the orchestrator warns when a refiner shares the
   aggregator's harness. If you change the default refiner set in a PR,
   justify it in the PR body.
2. **Don't commit `.moa/` session artifacts.** Already gitignored; just
   don't fight it.

## Soft defaults (open to change via PR)

- **Auth follows the underlying CLI.** Codex supports persisted API-key login,
  Claude accepts `ANTHROPIC_API_KEY`, and OpenCode reads provider keys
  (`MOONSHOT_API_KEY`,
  `FIREWORKS_API_KEY`, `QWEN_TOKEN_PLAN_API_KEY`, and others). The open gap is
  normalized usage/cost telemetry and safe pre-dispatch budget controls, not
  basic API-key authentication.
- **Default CLI roster is `[agy-gemini-pro, grok, codex-luna]` proposers,
  `[qwen, kimi, opus]` refiners, and `codex-sol` aggregator at `xhigh`** using the
  `{agy, codex, claude, opencode}` harnesses. The Web UI Thorough preset replaces
  Luna with GLM 5.2 beside Terra so all four proposer lanes come from independent
  labs. Cursor is unsupported. DeepSeek V4 Pro/Flash remain optional curated
  routes outside recommended presets. The model defaults are
  Gemini 3.1 Pro High, Grok 4.5, GPT-5.6 Luna, Qwen
  `qwen3.8-max-preview`, Kimi K3, Claude Opus 5 (`claude-opus-5`),
  and `gpt-5.6-sol` at `xhigh` for synthesis. Fable 5 is an
  aggregator-only, warning-gated alternative and is never a proposer or
  refiner. It is a default, not a cap—the roster is pure config (built-in names,
  `providers:` in config.yaml, current Claude/OpenCode/Google routes
  documented in `docs/config.md`, or the
  `MOA_PROVIDER_<NAME>` env shorthand). OpenCode-backed routes use a
  `provider/model` string, but a new *harness* needs its own adapter—open an
  issue first. Stable provider names
  such as `sonnet`, `opus`, `qwen`, and `kimi` are compatibility/configuration
  identifiers; manifests and the Web UI record their resolved current model.

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

- **Evidence validation fails closed.** Reject a proposer before persistence
  when its evidence cross-fields are invalid, and redact operator email/home
  details from browser-visible worker logs. → `harness/scripts/run_moa.py`,
  `harness/webui/store.py`.
- **Scanned-PDF OCR is page-parallel but output-ordered.** Keep the
  CPU-derived `MOA_ATTACHMENT_OCR_WORKERS` × Tesseract thread budget bounded,
  serialize progress updates, and always reassemble text by PDF page number.
  → `docs/webui.md`.
- **Effort copy and controls are one UI contract.** If a roster row says
  “Adjust,” selecting it must reveal an enabled control; fixed routes must say
  “Fixed _level_ effort” or “Provider-managed effort.” Drive both from
  `effortPresentation()` and never reuse the route's `data-effort-mode` marker
  for the `fieldset[data-effort-control]` container; keep the Playwright
  contract test green. → `docs/webui.md`.
- **Model lab owns visual identity; execution harness never does.** Every
  roster group, review node, live lane, health-card portrait, archived run,
  and responsive state must resolve `lab_id` through
  `harness/scripts/model_labs.py`. Unknown custom routes use the independent
  lab fallback. Never add `provider-*` or harness-keyed `pixel-*` art.
  → `docs/assets.md`.
- **Don't guess opencode `run` flags — they contradict the published docs.** No
  `-q`/`--auto`; auto-approve is `--dangerously-skip-permissions`; no stdin
  (prompt via `-f`, big prompts overflow argv); `-f` is a greedy array so the
  message goes before it. → `opencode-headless-run-invocation` skill + `adapters/opencode.py`.
- **opencode model ids are `provider/model` strings.** Swap billing paths by
  overriding the model string (`MOA_CUSTOM_MODEL=provider/model`), not by adding a
  harness. → `docs/config.md`.
- **Schema-invalid roots get one bounded repair pass, not a research retry.**
  OpenCode retains root-shaped objects missing required fields; OpenCode and
  Claude repair only the supplied object inside the session directory with
  research disabled. Incomplete output still uses the existing one-time
  redispatch path. → `harness/scripts/run_moa.py`.
- **Google runs through AGY.** Live probes control route availability;
  execution fails closed on plan mode + sandbox and reuses local CLI auth.
  → `docs/architecture.md`.
- **The HTML run report is a single self-contained file.** `report.py` inlines
  `harness/report/template.html`, six editorial WebP illustrations, and the
  session data — zero network requests (tests assert no external
  `src=`/`href=`). The session JSON is embedded in a
  `<script type="application/json">` with `</` → `<\/` so a `</script>` inside
  a captured log can't terminate the tag early. → `docs/report.md`.
