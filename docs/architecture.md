# Architecture

MoA-X is a CLI-native take on the 2024 Mixture-of-Agents method
([Wang et al., arXiv:2406.04692](https://arxiv.org/abs/2406.04692)),
pointed at a different job: producing repo-grounded implementation
plans for coding agents instead of chat answers.

<p align="center">
  <img src="moa-architecture.png" alt="MoA-X architecture" width="620">
</p>

## The four layers

```
Layer 0 — Scout brief           (parent Claude, in-place)
Layer 1 — Proposers (3 parallel)  codex + gemini + sonnet subprocesses
Layer 2 — Broadcast refiners (2)  codex + gemini, each sees ALL 3 proposals
Layer 3 — Aggregator              (parent Claude Opus, in-place)
```

**Layer 0.** Parent Claude Code session reads your spec, asks 1–3
clarifying questions, writes a scout brief (focus files, in-scope,
out-of-scope). The brief bounds how much exploration the downstream
models do.

**Layer 1: three proposers, three labs.** OpenAI `codex`, Google
`gemini`, Anthropic `claude` (in Sonnet mode). Each produces an
independent plan. Every proposer reads the repo (codex with a
filesystem-enforced read-only sandbox; gemini and sonnet with
read-only enforced by prompt) and does web research. Different labs
tend to mean different training data, different tool-use behavior,
and different blind spots.

**Layer 2: two broadcast refiners.** `codex` and `gemini` each see
all three proposals and produce verification output: which claims
are verified, which are contradicted, what's missing, what the
proposers disagreed on. "Broadcast" means every refiner sees every
proposal, not cross-pair. This is paper-faithful to Wang et al.

**Layer 3: aggregation.** Parent Claude Opus synthesizes into one
plan you can act on. It honors every `contradicted` flag from the
refiners, pulls in every `missing_steps` entry, and surfaces
disagreements instead of silently picking a side.

Layers 0 and 3 live in your Claude Code REPL. Layers 1 and 2 are
subprocesses spawned by `harness/scripts/run_moa.py`.

## Why broadcast refinement

Version 0.1 of this harness used *cross-pair* refinement: codex only
saw gemini's proposal, gemini only saw codex's. That's not what the
MoA paper does. Broadcast (every refiner sees every proposal) costs
the same wall-clock (refiners run in parallel either way) and
gives each refiner enough context to spot cross-proposer
convergence and divergence signals that a one-input view can't
reveal. v0.2 corrected this.

## Why sonnet is proposer-only

Opus 4.x is the Layer 3 aggregator. Sonnet 4.x is a Layer 1 proposer.
Layer 2 is kept to `{codex, gemini}` so the verification step is
done by two labs independent of both:

- the Anthropic-family proposer (Sonnet), and
- the Anthropic-family aggregator (Opus).

Using Sonnet as a refiner would concentrate Anthropic across two
load-bearing layers and reduce verification independence. This
matters when the Anthropic-family proposer is wrong in a way
characteristic of its training: another Anthropic refiner is less
likely to catch it.

## Why these three

Three labs (OpenAI + Google + Anthropic), not more, not fewer.

- **Three is enough for cross-lab diversity.** The paper's own
  ablation shows diversity (different labs) beats quantity (more
  copies of the same model). Three independent labs cover the
  current frontier.
- **Adding more labs costs wall-clock and auth complexity.** Each
  provider needs its own adapter, preflight, and auth story (whether
  that's subscription OAuth or an API key).
- **`{codex, claude-code, gemini}` is the default set.** It isn't a
  hard cap. The orchestrator, preflight, and prompt assumptions are
  shaped around this trio, so PRs that add providers (OpenCode, a
  fourth frontier lab, a Chinese-lab model, xAI, Mistral) should open
  an issue first so we can talk through the adapter shape. A
  Chinese-lab proposer in particular would sharpen the cross-lab
  diversity argument, since today's three are all US-based. See
  [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Why provider names instead of fixed roles

A provider in moa-x is a `{name, harness, model}` triple. The `harness`
is which CLI gets invoked (`codex`, `gemini`, `claude`, `cursor`); the
`model` is what that harness asks for (e.g. `gpt-5.4`, `grok-4-20`);
the `name` is a user-facing label that becomes the `agent_id` in
payloads. The codebase ships built-in names `codex`, `gemini`, `sonnet`
for back-compat; users add their own under `providers:` in
`harness/config.yaml`.

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
three providers" non-goals. Neither is anymore. API billing is a
path we want to support better, and a fourth provider (especially a
Chinese-lab model) is on the PR wishlist. The one hard constraint
that remains is the lab-independence invariant at refinement and
aggregation (see "Why sonnet is proposer-only" above); any new
provider has to slot in without collapsing that.
