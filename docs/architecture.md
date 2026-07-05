# Architecture

MoA-X is a CLI-native take on the 2024 Mixture-of-Agents method
([Wang et al., arXiv:2406.04692](https://arxiv.org/abs/2406.04692)),
pointed at a different job: producing repo-grounded implementation
plans for coding agents instead of chat answers.

<p align="center">
  <img src="moa-x-workflow.png" alt="MoA-X workflow: Scout → Proposers (codex + glm + sonnet, read-only) → Broadcast refiners (codex + kimi) → Opus aggregator" width="700">
</p>

## The four layers

```
Layer 0 — Scout brief           (parent Claude, in-place)
Layer 1 — Proposers (parallel)    default: codex + glm + sonnet subprocesses
Layer 2 — Broadcast refiners      default: codex + kimi, each sees ALL proposals
Layer 3 — Aggregator              (parent Claude Opus, in-place)
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
permission-deny policy plus the prompt rule; sonnet with read-only
enforced by prompt) and does web research. Different labs tend to mean
different training data, different tool-use behavior, and different blind
spots.

**Layer 2: broadcast refiners.** The default refiners are `codex` and
`kimi` (Moonshot Kimi K2.7 Code via `opencode`). Each sees all the
proposals and produces verification output: which claims are verified,
which are contradicted, what's missing, what the proposers disagreed on.
"Broadcast" means every refiner sees every proposal, not cross-pair. This
is paper-faithful to Wang et al.

**Layer 3: aggregation.** Parent Claude Opus synthesizes into one
plan you can act on. It honors every `contradicted` flag from the
refiners, pulls in every `missing_steps` entry, and surfaces
disagreements instead of silently picking a side.

Layers 0 and 3 live in your Claude Code REPL. Layers 1 and 2 are
subprocesses spawned by `harness/scripts/run_moa.py`.

## Why broadcast refinement

Version 0.1 of this harness used *cross-pair* refinement: each refiner
saw only one other proposer's plan. That's not what the
MoA paper does. Broadcast (every refiner sees every proposal) costs
the same wall-clock (refiners run in parallel either way) and
gives each refiner enough context to spot cross-proposer
convergence and divergence signals that a one-input view can't
reveal. v0.2 corrected this.

## Why sonnet is proposer-only

Opus 4.x is the Layer 3 aggregator. Sonnet 4.x is a Layer 1 proposer.
The default Layer 2 is `{codex, kimi}` so the verification step is
done by two labs (OpenAI + Moonshot) independent of both:

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

The default roster spans four labs — OpenAI (`codex`), Zhipu (`glm`),
Anthropic (`sonnet`), Moonshot (`kimi`) — not more, not fewer.

- **Cross-lab diversity beats quantity.** The paper's own ablation
  shows diversity (different labs) beats more copies of the same model.
  Four independent labs across two countries cover a lot of the current
  frontier and break the US-only monoculture the earlier lineup had.
- **Adding lanes costs wall-clock and auth complexity.** Each provider
  needs an auth story (subscription OAuth or an API key) and adds to the
  parallel fan-out, though the wall-clock cost is bounded since layers
  run in parallel.
- **It's a default, not a cap.** The roster is pure config (see
  [`config.md`](config.md)). More providers — DeepSeek, Qwen, MiniMax,
  xAI Grok, Mistral — are welcome; most slot in as an `opencode` or
  `cursor` model string. A genuinely new *harness* still needs its own
  adapter, preflight, and prompt-assumption review, so open an issue
  first. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Why gemini was removed

Through v0.2 the third lab was Google `gemini` (via the gemini CLI). It
was removed in v0.3.0 because it was the harness's dominant flake source:
the CLI routinely returned a success-shaped envelope with an empty
response (utility-model quota exhaustion silently dropping the JSON),
it has no CLI-level read-only mode (only a prompt rule), and it forced
the prompt onto argv where large refiner prompts hit ARG_MAX. GLM-5.2
(via opencode) took the third-lab slot: comparable frontier coding
quality, real permission-level read-only, and file-based prompt delivery
that sidesteps the argv limit. Anyone who still wants a Gemini model can
route it through the `cursor` harness as a user-named provider — see
[`config.md`](config.md#migrating-from-gemini).

### Why provider names instead of fixed roles

A provider in moa-x is a `{name, harness, model}` triple. The `harness`
is which CLI gets invoked (`codex`, `claude`, `opencode`, `cursor`); the
`model` is what that harness asks for (e.g. `gpt-5.4`, `opencode-go/glm-5.2`,
`grok-4-20`); the `name` is a user-facing label that becomes the
`agent_id` in payloads. The codebase ships built-in names `codex`,
`sonnet`, `glm`, `kimi`, `composer`; users add their own under
`providers:` in `harness/config.yaml` or via the
`MOA_PROVIDER_<NAME>=<harness>:<model>` env shorthand.

This split exists because the Cursor CLI breaks the one-CLI-one-lab
assumption — `cursor-agent --model gpt-5.5-medium` and `codex --model gpt-5.4`
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

First-class API-billing support (cost accounting in the manifest,
a spend ceiling, per-layer breakdowns) is an open direction we'd
happily take PRs on. See the top-level README's PR wishlist.

## Non-goals

- **Chat-answer benchmarks.** MoA-X is for planning, not Q&A.
- **Eval / benchmark tooling.** Earlier iterations had
  tau-bench/terminal-bench adapters; they're gone.

Previously this list also called "API-key fallback" and "more than
three providers" non-goals. Neither is anymore. opencode already routes
provider API keys (GLM/Kimi are API-billable today), the default roster
is four providers, and the roster is user config. The one constraint we
still recommend (not enforce) is lab-independence at refinement and
aggregation (see "Why sonnet is proposer-only" above); the shipped
default honors it and the orchestrator warns when a roster breaks it.
