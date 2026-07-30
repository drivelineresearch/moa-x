# Changelog

All notable changes to MoA-X are recorded here. Release tags follow semantic
versioning.

## [Unreleased]

### Added

- Local-first Flask control room with a persistent SQLite run archive,
  browser-local profiles, task-only and managed GitHub launches, reference
  uploads, lifecycle events, provider health, and responsive run review.
- Curated current routes for GPT-5.6 Terra/Sol/Luna, Claude Sonnet/Opus 5,
  OpenCode Go Grok/GLM/DeepSeek, Qwen Token Plan, and AGY Gemini 3.1 Pro High/Low.
  The Web UI offers GPT-5.6 Sol by default, Claude Opus 5 as an alternative
  aggregator, and warning-gated Fable 5 1M at `xhigh`.
- Nine model-lab portrait and pixel-art pairs for OpenAI, Google, Anthropic,
  xAI, Moonshot, Alibaba, DeepSeek, Zhipu, and independent/custom routes.
  Current and archived runs now retain the producing lab's visual identity
  regardless of which CLI transported the request.
- Interactive decision-lineage explorer in `report.html`, backed by a new
  schema-validated `final-plan.json` companion that links every aggregated
  step to exact proposer steps and refiner findings.
- Visible, non-fatal lineage validation warnings and a legacy-session fallback
  when structured lineage is unavailable.
- Optional recorded Layer 3 aggregation through Codex or Claude. The
  `--phase layer3` path reuses retained proposer/refiner output, validates one
  strict Markdown-plus-lineage bundle, records timing and logs, and refreshes
  the HTML report without rerunning Layers 1 or 2.
- Canonical `codex-sol` provider (`gpt-5.6-sol`, `xhigh` aggregation) and dedicated
  `MOA_AGGREGATOR_EFFORT` control. Older reviewer/aggregator names remain
  compatibility aliases only.
- GLM 5.2 and DeepSeek V4 Pro/Flash returned as optional OpenCode routes after
  live full-schema proposer qualification. All remain outside recommended
  presets; Pro passed immediately, while GLM and Flash passed the pipeline's
  single redispatch.

### Changed

- Default proposers use Gemini 3.1 Pro, Grok 4.5, and GPT-5.6 Luna. Default
  broadcast refiners use Qwen `qwen3.8-max-preview`, DeepSeek V4 Pro, and Claude
  Opus 5; the default aggregator remains GPT-5.6 Sol at `xhigh`.
- Quick, Balanced, and Thorough all include Gemini Pro and Grok as
  proposers and GPT-5.6 Sol at `xhigh` as the default aggregator. Quick
  uses DeepSeek V4 Pro as its compact refiner lane. Balanced adds GPT-5.6 Luna plus
  Qwen/DeepSeek/Opus (`high`), while Thorough also adds GPT-5.6 Terra and raises
  Opus to `max`.
- Kimi K3 is disabled for new runs after OpenCode Go repeatedly rejected it
  before inference even with balance fallback enabled. Its route and Moonshot
  visuals remain for archived provenance.
- Cursor execution support and its route catalog were removed. GLM and
  DeepSeek were initially removed from the curated launch roster after
  retained runs showed incomplete output and schema failures; their lab
  identities remain available for accurate archived-run rendering.
- The prompt coach keeps GPT-5.6 Luna as primary and now uses Gemini 3.1 Pro
  as its bounded fallback instead of an unreliable OpenCode route.
- Qwen Token Plan is now part of the default refiner roster and has a bounded
  600-second timeout instead of inheriting the OpenCode harness timeout.
- The report now includes recorded Layer 3 status, timing, logs, and run-health
  visibility throughout its overview, pipeline, and Gantt views.

### Fixed

- Phase-split and redispatched runs now preserve the original session start in
  their manifests. Reports also repair v0.4.1-and-older phase-local timing from
  retained agent timestamps, fixing truncated wall-clock totals and Layer 1
  Gantt offsets.
- Ordered final-plan steps no longer restart at `1` when nested evidence lists
  appear between steps.
- Structured-output extraction is shared, bounded, escape-tolerant, and strict
  about required root fields across adapters.
- Claude receives large synthesis prompts over stdin instead of argv.
  OpenCode incomplete outputs receive exactly one bounded redispatch, while
  parseable schema-invalid output receives one session-confined repair pass
  with repository reads and web access disabled. Readiness is evaluated per
  configured route.
- OpenCode now retains root-shaped responses that are missing required fields
  so bounded repair can receive them, rejects nested-object lookalikes with a
  structural signature, and no longer reports tool messages such as
  `Ripgrep JSON record exceeded` as depleted provider quota. Claude receives
  the same one-shot, tool-free repair for semantic evidence violations.
- AGY headless runs explicitly approve read tools inside plan+sandbox mode.
  Gemini Flash routes were retired after Gemini 3.1 Pro passed long-context
  structured-output validation on the authenticated consumer account.
- Proposer/refiner payloads are isolated as data, model identity is verified,
  and every harness is covered by a Git-visible before/after workspace guard.
- Cross-lab warnings, roster grouping, provider health, live lanes, generated
  reports, and all loading/failure states now use canonical model-lab metadata
  instead of conflating a transport harness with a model provider.
- Report disclosures and lineage tabs now expose consistent keyboard and ARIA
  behavior.

### Validation

- Offline and focused Flask/browser suite counts are refreshed at release
  time after the model-lab migration.
- Live route requalification accepted DeepSeek V4 Pro on its first
  schema-valid response and accepted GLM 5.2 plus DeepSeek V4 Flash on the
  single redispatch allowed after an incomplete first response.
- Five paid ensemble configurations exercised the pre-Pro route matrix. Terra,
  Luna, Sol, Sonnet, Opus, Cursor Grok, GLM, Grok, Kimi, Qwen Token Plan,
  Qwen OpenCode, and the then-surfaced AGY routes returned schema-valid
  artifacts. Gemini 3.1 Pro separately passed a 380,311-character
  schema-shaped proposer probe with exact end-marker retention.
- A 103,507-byte synthesis completed through Opus over stdin. Composer returned
  progress-only output on both its initial attempt and bounded retry, so the
  Web UI leaves it visible but disabled. Across the matrix there were no
  timeouts, identity mismatches, workspace mutations, auth failures, or quota
  failures.

## [0.4.1] — 2026-07-19

### Added

- Optional built-in Qwen Cloud Token Plan proposer
  (`qwen-token-plan/qwen3.7-max`) through OpenCode, with its dedicated endpoint,
  `QWEN_TOKEN_PLAN_API_KEY`, credential preflight, and configuration docs.
- Release archives for both the complete source tree and a ready-to-install
  `mixture-of-agents/` Claude Code skill, plus SHA-256 checksums.

### Fixed

- Claude structured-output calls now remove unsupported `$schema` dialect
  metadata before invoking Claude Code 2.1.x.
- OpenCode output parsing now repairs invalid Markdown escapes without
  accepting a valid nested object in place of the required root payload.
- Broadcast-refiner verification records emitted in `additional_research` are
  restored to `verifications` before strict schema validation.
- Provider selection can include optional built-ins such as Qwen even when
  they are not part of the default layer configuration.
- HTTP provider/model routing failures are classified as non-transient.

### Documentation

- Regenerated the workflow illustration to distinguish the default proposer
  roster from optional Qwen and show both `final-plan.md` and the self-contained
  `report.html` output.
- Updated install, configuration, usage, architecture, harness, auth, and
  read-only guidance to match verified behavior.
- Replaced the stale contribution wishlist with current priorities and updated
  the project status.

### Validation

- Live smoke test: four of four proposers (Codex, GLM, Sonnet, Qwen) and two of
  two broadcast refiners (Codex, Kimi) completed successfully.
- The run produced a self-contained 979 KB HTML report with charts, timing,
  verdicts, logs, and the final plan.
- Offline suite: 79/79 tests pass on Python 3.11 and 3.12.

The default roster is unchanged: `codex,glm,sonnet` proposers and `codex,kimi`
refiners. Qwen remains opt-in.

## [0.4.0] — 2026-07-05

- Added the self-contained HTML run report with a 3D pipeline, Gantt chart,
  verdict matrix, plans, logs, and static reduced-motion/print fallback.
- Switched the default GLM and Kimi routes to the `opencode-go` gateway while
  retaining direct-provider and Fireworks overrides.

[Unreleased]: https://github.com/drivelineresearch/moa-x/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/drivelineresearch/moa-x/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/drivelineresearch/moa-x/releases/tag/v0.4.0
