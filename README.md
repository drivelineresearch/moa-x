<p align="center">
  <img src="docs/moa-x-header.png" alt="MoA-X — Cross-Lab Mixture of Agents for coding plans" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/runner-Claude%20Code-8b5cf6.svg" alt="Claude Code">
  <img src="https://img.shields.io/badge/providers-codex%20%7C%20claude--code%20%7C%20opencode%20%7C%20cursor-informational" alt="supported CLIs">
</p>

<p align="center">
  <img src="docs/moa-x-workflow.png" alt="MoA-X workflow: (1) Scout writes a brief → (2) Proposers codex + glm + sonnet draft plans read-only → (3) Broadcast refiners codex + kimi verify all plans → (4) Opus aggregator writes final-plan.md, ~6–12 min wall-clock" width="820">
</p>

A small, CLI-native take on the 2024
[Mixture-of-Agents paper](https://arxiv.org/abs/2406.04692), pointed at
a different job: producing **repo-grounded implementation plans** for
coding agents instead of chat answers. The default roster puts proposers
from four different labs to work — OpenAI `codex`, Zhipu `glm` (GLM-5.2 via
the `opencode` CLI), Anthropic `claude` Sonnet — reading the repo in
parallel, doing their own web research, and each writing an independent
plan. Two refiners (`codex` + Moonshot `kimi`) then refine in broadcast
mode (every refiner sees every plan). Finally a parent Claude Opus session
aggregates the whole thing into one plan you can act on.

Built to run **inside Claude Code** as a skill. Standalone Python works
too. The harness ships built-in providers across four harnesses (`codex`,
`claude`, `opencode`, `cursor`) and the roster — which providers run at
which layer, and how many — is pure config. API-based auth and more
providers are all fair game. See "PRs we'd love to see" below.

## TL;DR

```bash
# 1. Install the CLIs (see docs/install.md for details)
npm i -g @openai/codex               && codex login
curl -fsSL https://opencode.ai/install | bash   # then: opencode auth login,
                                                 # or export ZHIPU_API_KEY / MOONSHOT_API_KEY
# claude CLI: https://docs.claude.com/en/docs/claude-code/quickstart

# 2. Install as a Claude Code skill
cp -r harness ~/.claude/skills/mixture-of-agents

# 3. Inside Claude Code, in any project
/mixture-of-agents
```

## Architecture at a glance

```
Layer 0 — Scout brief           (parent Claude, in-place)
Layer 1 — Proposers (parallel)    default: codex + glm + sonnet subprocesses
Layer 2 — Broadcast refiners      default: codex + kimi, each sees ALL proposals
Layer 3 — Aggregator              (parent Claude Opus, in-place)
```

The roster is config-driven; the defaults above span four labs (OpenAI,
Zhipu, Anthropic, Moonshot) and keep the refiners independent of the Opus
aggregator's lab.

Typical wall-clock is 6–12 minutes. Use it for non-trivial
architecture work, not one-line fixes. Background in
[`docs/architecture.md`](docs/architecture.md).

## Docs

- [`docs/install.md`](docs/install.md): install the CLIs, verify, install as a Claude Code skill
- [`docs/usage.md`](docs/usage.md): running via `/mixture-of-agents` (primary) or standalone
- [`docs/config.md`](docs/config.md): `.env` + `harness/config.yaml`, MOA_\* knob table, precedence, roster swaps
- [`docs/architecture.md`](docs/architecture.md): the four layers, why broadcast, why this roster
- [`CONTRIBUTING.md`](CONTRIBUTING.md): dev setup, PR protocol, where help is welcome
- [`SECURITY.md`](SECURITY.md): private vulnerability reports
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md): guidance for coding agents working on this repo (AGENTS.md points at CLAUDE.md)

## Repo layout

```
README.md              this file
CLAUDE.md              agent guidance for this repo
AGENTS.md              pointer to CLAUDE.md for Codex / OpenCode / Cursor / Zed
CONTRIBUTING.md        contributor guide
SECURITY.md            vulnerability reporting
LICENSE                MIT
.env.example           copy to .env to override harness defaults
docs/                  longer-form docs by topic (+ brand images)
harness/               orchestrator, adapters, prompts, schemas
  SKILL.md             Claude Code skill manifest
  README.md            skill-internal notes (lives with harness/ when copied into ~/.claude/skills/)
  config.example.yaml  copy to harness/config.yaml to override defaults
  prompts/             scout / proposer / refiner / aggregator
  scripts/             orchestrator + adapters + config + tests
requirements-cli.txt   install/auth notes for the provider CLIs
```

## PRs we'd love to see

The current harness is shaped around what I use day-to-day. These are
the directions that would expand who MoA-X is useful for, and I'd
prioritize reviewing PRs that land any of them:

- **API-billing support** for codex and claude. Right now those
  adapters assume the CLI is logged in against a subscription plan.
  Shops that run through `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` should
  be a first-class path. (opencode already routes provider API keys —
  `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `FIREWORKS_API_KEY` — so GLM
  and Kimi are API-billable today.)
- **More agent harnesses.** The orchestrator runs fine from a plain
  shell, but the scout + aggregation steps are tailored to Claude Code.
  `opencode` and `cursor` are supported alongside `codex`/`claude`;
  PRs closing the gap for aider, roo, continue, cline, etc. are welcome.
- **More Chinese-lab and frontier models.** GLM (Zhipu) and Kimi
  (Moonshot) ship in the default roster via opencode. DeepSeek, Qwen,
  MiniMax, xAI Grok, Mistral — anything with a credible coding-bench
  story — extend the cross-lab diversity argument further. Most slot in
  as an `opencode` or `cursor` model string with no new adapter; a
  genuinely new harness needs its own adapter, preflight, and
  prompt-assumption review, so open an issue first.
- **Cost observability** for API-billed runs: token accounting in the
  manifest, a `MOA_MAX_COST` ceiling, per-layer spend breakdowns.
- **Stronger, uniform read-only guarantees.** `codex` runs in a filesystem
  sandbox and `cursor` uses `--mode plan`, but `opencode` leans on a
  permission-deny config plus the prompt rule. A PR that hardens or verifies
  read-only across every harness — and fails a run that writes — would tighten
  the safety story.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the PR protocol.

## Status

Early open-source release. Contributions welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through
[SECURITY.md](SECURITY.md).

## License

MIT; see [LICENSE](LICENSE). Copyright (c) 2026 Kyle Boddy.

## Author

Kyle Boddy.
