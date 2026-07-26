# Architecture

MoA-X is a CLI-native take on the 2024 Mixture-of-Agents method
([Wang et al., arXiv:2406.04692](https://arxiv.org/abs/2406.04692)),
pointed at a different job: producing repo-grounded implementation
plans for coding agents instead of chat answers.

<p align="center">
  <img src="moa-x-workflow.png" alt="MoA-X workflow: Scout → GPT-5.6 Terra, GLM-5.2, and Claude Sonnet 5 proposers → GPT-5.6 Sol and Qwen 3.8 Max Preview broadcast refiners → Claude Opus 5 aggregator → final plan, decision lineage, and report" width="700">
</p>

## The four layers

```
Layer 0 — Scout brief           (parent Claude, in-place)
Layer 1 — Proposers (parallel)    default: codex + glm + sonnet subprocesses
Layer 2 — Broadcast refiners      default: codex-sol + qwen, each sees ALL proposals
Layer 3 — Aggregator              default: parent Claude Opus 5; optional recorded Codex phase
```

The roster (which providers run at which layer, and how many) is
config-driven — the defaults shown here are what the harness ships with.

**Layer 0.** Parent Claude Code session reads your spec, asks 1–3
clarifying questions, writes a scout brief (focus files, in-scope,
out-of-scope). The brief bounds how much exploration the downstream
models do.

**Layer 1: proposers across labs.** The default is OpenAI `codex`, Zhipu
`glm` (GLM-5.2 via the `opencode` CLI), and Anthropic `claude` (in Sonnet
mode). Each produces an independent plan. Every proposer reads the repo
(codex with a filesystem-enforced read-only sandbox; opencode with a
permission-deny policy plus the prompt rule; sonnet with a hard read-only
tool allowlist) and does web research. Different labs tend to mean
different training data, different tool-use behavior, and different blind
spots.

**Layer 2: broadcast refiners.** The default refiners are `codex-sol`
(`gpt-5.6-sol` at high reasoning) and `qwen` (Alibaba
`qwen3.8-max-preview` through Qwen Token Plan and `opencode`). Each sees all the
proposals and produces verification output: which claims are verified,
which are contradicted, what's missing, what the proposers disagreed on.
"Broadcast" means every refiner sees every proposal, not cross-pair. This
is paper-faithful to Wang et al.

**Layer 3: aggregation.** By default parent Claude Code on
`claude-opus-5` synthesizes one plan you can act on. The same retained synthesis
can instead run through `--phase layer3 --aggregator-provider
codex-sol`, which invokes `gpt-5.6-sol` at high reasoning, validates
the Markdown and exact decision-lineage pointers, records the subprocess, and
regenerates the report. Both paths honor contradicted findings, pull in
missing steps, and surface disagreements instead of silently picking a side.

Layer 0 lives in the parent agent. Layers 1 and 2 are subprocesses spawned by
`harness/scripts/run_moa.py`; Layer 3 may live in the parent or run as its own
recorded subprocess.

## Why broadcast refinement

Version 0.1 of this harness used *cross-pair* refinement: each refiner
saw only one other proposer's plan. That's not what the
MoA paper does. Broadcast (every refiner sees every proposal) costs
the same wall-clock (refiners run in parallel either way) and
gives each refiner enough context to spot cross-proposer
convergence and divergence signals that a one-input view can't
reveal. v0.2 corrected this.

## Why Sonnet is proposer-only

Claude Code's `claude-opus-5` route is the Layer 3 aggregator and
`claude-sonnet-5` is a Layer 1 proposer. The stable internal provider names
remain `opus` and `sonnet`. The default Layer 2 is
`{codex-sol, qwen}` so verification is done by OpenAI and Alibaba,
independent of both:

- the Anthropic-family proposer (Sonnet), and
- the Anthropic-family aggregator (Opus).

Using Sonnet as a refiner would concentrate Anthropic across two
load-bearing layers and reduce verification independence. This
matters when the Anthropic-family proposer is wrong in a way
characteristic of its training: another Anthropic refiner is less
likely to catch it. The harness no longer enforces this — the roster
is user config — but the shipped default follows it, and CLAUDE.md
recommends keeping it (the orchestrator warns if a refiner shares the
aggregator's harness).

## Why this roster

The default roster spans four labs — OpenAI (`codex`/`codex-sol`), Zhipu
(`glm`), Anthropic (`sonnet`/`opus`), and Alibaba (`qwen`).

- **Cross-lab diversity beats quantity.** The paper's own ablation
  shows diversity (different labs) beats more copies of the same model.
  Four independent labs across two countries cover a lot of the current
  frontier and break the US-only monoculture the earlier lineup had.
- **Adding lanes costs wall-clock and auth complexity.** Each provider
  needs an auth story (subscription OAuth or an API key) and adds to the
  parallel fan-out, though the wall-clock cost is bounded since layers
  run in parallel.
- **It's a default, not a cap.** The roster is pure config (see
  [`config.md`](config.md)); Qwen Token Plan ships in the default refiner set,
  while DeepSeek V4 Pro/Flash are curated OpenCode Go routes.
  Tested recipes for MiniMax, xAI Grok, Mistral, or other frontier models are
  welcome. Most should slot into the existing `opencode`
  or `cursor` adapter. A genuinely new *harness* still needs its own adapter,
  preflight, and prompt-assumption review, so open an issue first. See
  [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Why Google is opt-in

GLM-5.2 via OpenCode holds the default third-lab proposer slot. Google models
are exposed only through the opt-in AGY routes, which reuse the authenticated
local Antigravity account and require plan mode plus sandboxing. Live AGY
probes decide whether the Pro and Flash routes are currently available. See
[`config.md`](config.md#google-models-through-agy).

### Why provider names instead of fixed roles

A provider in moa-x is a `{name, harness, model}` triple. The `harness`
is which CLI gets invoked (`codex`, `claude`, `opencode`, `cursor`, or `agy`); the
`model` is what that harness asks for (e.g. `gpt-5.6-terra`, `opencode-go/glm-5.2`,
`cursor-grok-4.5-high`); the `name` is a stable route identifier that becomes
the `agent_id` in payloads. Compatibility names remain stable even when their
resolved model advances; the Web UI presents the canonical model label. The
curated surface ships `codex`, `codex-sol`, `codex-luna`, `sonnet`, `opus`,
`glm`, `kimi`, `qwen`, `qwen-opencode`, `composer`, `grok`, `cursor-grok`,
`deepseek`, `deepseek-flash`, and `agy-gemini-flash`. The two DeepSeek
routes resolve to OpenCode Go's current V4 Pro and V4 Flash model ids and can
participate as proposers or refiners. Legacy `codex-reviewer`,
`codex-aggregator`, and `agy-gemini-high` names remain resolvable for existing
configs but are hidden from the Web UI. Users add their own under
`providers:` in `harness/config.yaml` or via the
`MOA_PROVIDER_<NAME>=<harness>:<model>` env shorthand.

This split exists because the Cursor CLI breaks the one-CLI-one-lab
assumption — `cursor-agent --model gpt-5.6-sol-high` and
`codex --model gpt-5.6-terra`
both hit OpenAI. Encoding the lab in the harness identifier would
have meant pretending Cursor was three or four different harnesses;
splitting the data model is cleaner.

The lab-independence preference (Layer 2 refiners should not share a
lab with the Opus aggregator) lives in CLAUDE.md as a recommendation,
not as a runtime invariant. The harness stays lab-agnostic; the user
decides whether the soft rule is worth following.

## Why CLI, not SDK

Each vendor CLI already handles auth, retries, tool routing, and
model-specific quirks. An SDK integration would duplicate all of
that inside MoA-X and drift as vendors change their clients. The
CLI surface is also more stable, and it lets the orchestrator stay
agnostic to how the user is billed: whatever auth the CLI is in
when invoked (subscription OAuth, keychain, or `*_API_KEY` env
var) is the auth MoA-X uses. Each CLI call also runs in its own
process group with its own TMPDIR, so auth state stays out of the
orchestrator process's environment.

API-key authentication already works through the underlying CLIs. Normalized
usage/cost accounting in the manifest and report, plus safe pre-dispatch budget
controls, remains an open direction. See the top-level README's contribution
priorities.

## Non-goals

- **Chat-answer benchmarks.** MoA-X is for planning, not Q&A.
- **Eval / benchmark tooling.** Earlier iterations had
  tau-bench/terminal-bench adapters; they're gone.

Previously this list also called "API-key fallback" and "more than
three providers" non-goals. Neither is anymore. The underlying CLIs support
subscription and/or API-key auth, the default roster spans four labs, Qwen is
a built-in default refiner, and the roster is user config. The one constraint we
still recommend (not enforce) is lab-independence at refinement and
aggregation (see "Why sonnet is proposer-only" above); the shipped
default honors it and the orchestrator warns when a roster breaks it.
