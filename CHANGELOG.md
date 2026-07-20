# Changelog

All notable changes to MoA-X are recorded here. Release tags follow semantic
versioning.

## [Unreleased]

### Added

- Interactive decision-lineage explorer in `report.html`, backed by a new
  schema-validated `final-plan.json` companion that links every aggregated
  step to exact proposer steps and refiner findings.
- Visible, non-fatal lineage validation warnings and a legacy-session fallback
  when structured lineage is unavailable.
- Optional recorded Layer 3 aggregation through Codex or Claude. The
  `--phase layer3` path reuses retained proposer/refiner output, validates one
  strict Markdown-plus-lineage bundle, records timing and logs, and refreshes
  the HTML report without rerunning Layers 1 or 2.
- Built-in `codex-aggregator` provider (`gpt-5.6-sol`, high reasoning) and
  dedicated `MOA_AGGREGATOR_EFFORT` control.

### Changed

- Default proposers now use Codex `gpt-5.6-terra`, GLM 5.2, and Claude Code's
  rolling `sonnet` alias. Default broadcast refiners now use Codex
  `gpt-5.6-sol` at high reasoning plus Qwen Token Plan
  `qwen3.8-max-preview`; the default interactive aggregator remains Claude
  Code's rolling `opus` alias.
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
- Proposer/refiner payloads are isolated as data, model identity is verified,
  and every harness is covered by a Git-visible before/after workspace guard.
- Report disclosures and lineage tabs now expose consistent keyboard and ARIA
  behavior.

### Validation

- Offline suite: 92/92 tests pass.
- Live cross-lab smoke: all three proposers and both broadcast refiners passed
  with no timeout, identity, schema, transient-empty, or workspace-mutation
  failures.
- Live Codex-only Layer 3 smoke completed in 150.4 seconds, produced two
  lineage-valid final steps with no stale references, and regenerated the
  self-contained report.

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
