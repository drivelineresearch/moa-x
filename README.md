# MoA-X

**Cross-Lab Mixture of Agents for coding plans.**

<p align="center">
  <img src="docs/moa-architecture.png" alt="MoA-X architecture: Scout → 3 proposers (codex + gemini + sonnet, read-only) → 2 broadcast refiners → Opus aggregator, 6-12 min wall-clock" width="720">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/runner-Claude%20Code-8b5cf6.svg" alt="Claude Code">
  <img src="https://img.shields.io/badge/providers-codex%20%7C%20claude--code%20%7C%20gemini-informational" alt="supported CLIs">
  <img src="https://img.shields.io/badge/auth-subscription%20only-brightgreen.svg" alt="subscription-only">
</p>

A small, CLI-native take on the 2024
[Mixture-of-Agents paper](https://arxiv.org/abs/2406.04692), pointed at
a different job: producing **repo-grounded implementation plans** for
coding agents instead of chat answers. Three proposers from three
different labs (OpenAI `codex`, Google `gemini`, Anthropic `claude`
Sonnet) read the repo in parallel, do their own web research, and each
write an independent plan. Two of them then refine in broadcast mode
(every refiner sees every plan). Finally a parent Claude Opus session
aggregates the whole thing into one plan you can act on.

Built to run **inside Claude Code** as a skill. Standalone Python works
too. PRs that improve the non-Claude-Code path (OpenCode, other agent
harnesses, other providers) are very welcome.

## TL;DR

```bash
# 1. Install the three CLIs (see docs/install.md for details)
npm i -g @openai/codex          && codex login
npm i -g @google/gemini-cli     && gemini
# claude CLI: https://docs.claude.com/en/docs/claude-code/quickstart

# 2. Install as a Claude Code skill
cp -r harness ~/.claude/skills/mixture-of-agents

# 3. Inside Claude Code, in any project
/mixture-of-agents
```

## Architecture at a glance

```
Layer 0 — Scout brief           (parent Claude, in-place)
Layer 1 — Proposers (3 parallel)  codex + gemini + sonnet subprocesses
Layer 2 — Broadcast refiners (2)  codex + gemini, each sees ALL 3 proposals
Layer 3 — Aggregator              (parent Claude Opus, in-place)
```

Typical wall-clock is 6–12 minutes. Use it for non-trivial
architecture work, not one-line fixes. Background in
[`docs/architecture.md`](docs/architecture.md).

## Docs

- [`docs/install.md`](docs/install.md): install the three CLIs, verify, install as a Claude Code skill
- [`docs/usage.md`](docs/usage.md): running via `/mixture-of-agents` (primary) or standalone
- [`docs/config.md`](docs/config.md): `.env` + `harness/config.yaml`, MOA_\* knob table, precedence
- [`docs/architecture.md`](docs/architecture.md): the four layers, why broadcast, why these three providers
- [`CONTRIBUTING.md`](CONTRIBUTING.md): dev setup, PR protocol, where help is welcome
- [`SECURITY.md`](SECURITY.md): private vulnerability reports
- [`CLAUDE.md`](CLAUDE.md): guidance for coding agents working on this repo

## Repo layout

```
README.md              this file
CLAUDE.md              agent guidance for this repo
CONTRIBUTING.md        contributor guide
SECURITY.md            vulnerability reporting
LICENSE                MIT
.env.example           copy to .env to override harness defaults
docs/                  longer-form docs by topic
harness/               orchestrator, adapters, prompts, schemas
  SKILL.md             Claude Code skill manifest
  README.md            skill-internal notes (lives with harness/ when copied into ~/.claude/skills/)
  config.example.yaml  copy to harness/config.yaml to override defaults
  prompts/             scout / proposer / refiner / aggregator
  scripts/             orchestrator + adapters + config + tests
requirements-cli.txt   install/auth notes for the three CLIs
```

## Status

Early open-source release. Contributions welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through
[SECURITY.md](SECURITY.md).

## License

MIT; see [LICENSE](LICENSE). Copyright (c) 2026 Kyle Boddy.

## Author

Kyle Boddy.
