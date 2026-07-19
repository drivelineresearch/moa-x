# Changelog

All notable changes to MoA-X are recorded here. Release tags follow semantic
versioning.

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

[0.4.1]: https://github.com/drivelineresearch/moa-x/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/drivelineresearch/moa-x/releases/tag/v0.4.0
