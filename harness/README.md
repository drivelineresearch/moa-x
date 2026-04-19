# mixture-of-agents

Layered planning ensemble for Claude Code. Three models from three
different labs (OpenAI codex/gpt-5.4, Google gemini/2.5-pro, Anthropic
sonnet/4.6) read the repo, do heavy web research, and write independent
plans. Two of them (codex + gemini) then **broadcast-refine** by reading
all three proposals and producing cross-verifications. The parent Claude
Code session (Opus 4.6) synthesizes the whole thing into one actionable
plan.

Use it for non-trivial architecture work, where a second and third
opinion from models with different training data and different tool
behavior actually changes the answer. Not just different prompts.

## Quick start

```bash
# In any project directory inside Claude Code:
/mixture-of-agents          # then paste a spec, or:
/mixture-of-agents --spec ./docs/cache-layer-spec.md
```

The skill will:

1. Ask 1-3 clarifying questions.
2. Generate a "scout brief" with focus files, in-scope items, out-of-scope items.
3. Show you the brief and ask "ready to run? ~6-12 minutes".
4. On yes, spawn `codex exec`, `gemini -p`, and `claude -p` in parallel as three proposers.
5. Spawn `codex exec` and `gemini -p` in parallel as two broadcast refiners; each sees all three proposals.
6. Synthesize the five outputs (three proposals + two refinements) into `final-plan.md`.
7. Present the plan and ask whether to start executing.

## Architecture

```
Layer 0 — Scout brief                (parent Opus, in-place)
Layer 1 — Proposers (3 parallel)     (codex + gemini + sonnet subprocesses)
Layer 2 — Broadcast refiners (2)     (codex + gemini subprocesses, each sees all 3 proposals)
Layer 3 — Aggregator                 (parent Opus 4.6, in-place)
```

Claude work that happens in the parent REPL: Layer 0 (scout brief) and
Layer 3 (aggregation). The sonnet Layer-1 subprocess is a separate
`claude -p` headless invocation, not the parent session. The Python
orchestrator at `scripts/run_moa.py` handles Layers 1 and 2.

### Why broadcast refinement (not cross-pair)

v0.1 of this skill used cross-pair refinement: codex only saw gemini's
plan, gemini only saw codex's. That wasn't paper-faithful. The 2024
Mixture-of-Agents paper (Wang et al., arXiv:2406.04692) uses full
broadcast: every refiner sees every proposal. v0.2 corrects this.
Broadcast refinement has the same wall-clock cost as cross-pair,
because refiners run in parallel either way, and it gives each refiner
the context to spot cross-proposer convergence and divergence signals
that a one-input view can't reveal.

### Why sonnet is proposer-only

Opus 4.6 is the Layer 3 aggregator. Sonnet 4.6 is a Layer 1 proposer. Layer
2 (the refiner/verification step) is kept to {codex, gemini} so that the
verification is done by two labs independent of both the Anthropic-family
proposer (sonnet) AND the Anthropic-family aggregator (Opus). Using sonnet
as a refiner would concentrate Anthropic models across two load-bearing
layers and reduce verification independence.

## Why this skill exists

The 2024 Mixture-of-Agents paper showed that layered ensembles of LLMs
from different labs produce measurably better outputs than single-model
runs. Heterogeneous (cross-lab) beats homogeneous (the same model
sampled multiple times). The original use case was chat-answer
benchmarks.

For coding work the bigger value shows up at the **planning** moment:
just before you commit to an approach, having three models from three
different labs read the repo independently, do their own web research,
and then audit each other's plans surfaces blind spots that one model
alone would miss.

The four-layer structure (scout → proposers → broadcast refiners →
aggregator) is adapted from the paper but tuned for:

- **Repo-grounded planning, not chat answers.** All CLIs read the
  actual code. Codex runs with a filesystem-enforced read-only
  sandbox; gemini and sonnet run in yolo mode with read-only
  discipline enforced via prompt.
- **Heavy web research.** Every proposer and refiner is told to run
  at least 6-8 web searches and cite 5+ external sources.
- **CLI-first workflow.** Runs entirely from inside Claude Code,
  with no separate web UI or deploy.
- **Subscription billing.** codex, gemini, and sonnet all run on
  subscription plans, so there's no per-call dollar cap to worry
  about.

## Install

Full install instructions live in the repo at
[`docs/install.md`](../docs/install.md). Short version: install the
three vendor CLIs (codex / gemini / claude), authenticate each, then
drop `harness/` into `~/.claude/skills/mixture-of-agents/`:

```bash
cp -r harness ~/.claude/skills/mixture-of-agents
python3 ~/.claude/skills/mixture-of-agents/scripts/install_deps.py
```

The preflight script only checks. It never installs or auths anything
for you.

## Output artifacts

Each invocation creates a session directory under `.moa/` (in your current
working directory by default):

```
.moa/20260408-101530-add-cache-layer/
├── scout-brief.json
├── layer1/
│   ├── codex-proposer.json
│   ├── codex-proposer.log
│   ├── gemini-proposer.json
│   ├── gemini-proposer.log
│   ├── sonnet-proposer.json
│   └── sonnet-proposer.log
├── layer2/
│   ├── codex-refiner-broadcast.json
│   ├── codex-refiner-broadcast.log
│   ├── gemini-refiner-broadcast.json
│   └── gemini-refiner-broadcast.log
├── synthesis-input.md     # what the parent Opus session reads
├── manifest.json          # timing, success/failure per layer
└── final-plan.md          # the synthesized plan (written by parent Opus)
```

`.moa/` should be in your repo's `.gitignore`. Sessions are kept locally
for audit/debug; prune old ones manually if they accumulate.

## Failure modes

The orchestrator keeps going under partial failure:

- **1-2 proposers fail, at least 1 succeeds:** refiners see the
  proposers that worked, the aggregator proceeds, and the manifest
  notes the degraded run.
- **All proposers fail:** orchestrator exits with code 4, no
  synthesis.
- **One refiner fails, one succeeds:** the aggregator proceeds with
  the surviving refiner's output. The aggregator prompt handles the
  single-refiner case explicitly.
- **Schema validation fails for an agent:** that agent is marked
  unsuccessful, the manifest records why, and the run continues with
  what's left.
- **CLI not authenticated in preflight:** that CLI is skipped with
  a warning. If all three fail preflight, the orchestrator exits
  with code 3.

Nothing in this skill writes to your repo during the external phases.
Codex has filesystem-enforced read-only sandboxing. Gemini and sonnet
run in yolo mode so they can use shell and web tools, but their
prompts forbid file writes. Only the parent Claude session can edit
code, and only after you approve the final plan.

## Tuning

Most defaults are right. Things you can override:

```bash
python3 ~/.claude/skills/mixture-of-agents/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json \
  --codex-model gpt-5.4 \
  --codex-effort xhigh \
  --gemini-model gemini-2.5-pro \
  --sonnet-model claude-sonnet-4-6 \
  --codex-timeout 1500 \
  --gemini-timeout 1200 \
  --sonnet-timeout 1200 \
  --skip-layer2          # debug only; skips refiners
```

Defaults:
- `--codex-model gpt-5.4`
- `--codex-effort high`
- `--gemini-model gemini-2.5-pro` (override via `MOA_GEMINI_MODEL` env var)
- `--sonnet-model claude-sonnet-4-6`

> **Note:** `gemini-3.1-pro-preview` is available but flaky in
> practice. Frequent timeouts and empty responses. Stick with
> `gemini-2.5-pro` unless you're testing.

Per-agent timeout defaults (v0.2.3):
- `--codex-timeout` scales with `--codex-effort`: xhigh=1500s, high=1200s, medium/low=900s
- `--gemini-timeout 1200` (seconds)
- `--sonnet-timeout 1200` (seconds; sonnet with full research can spike past 15 min)
- `--timeout` is a master override that sets all three at once. Leave unset
  to use the per-agent defaults tuned to observed tail latency

The manifest's `config.timeout_seconds` is now an object
(`{codex, gemini, sonnet, master_override}`) so post-mortems can see exactly
what each agent had. Older runs have a scalar.

## Limits and caveats

- **One MoA run per machine at a time.** A `flock` on `/tmp/moa.lock`
  stops concurrent invocations from racing on shared CLI auth state.
  Sequential invocations are fine; parallel ones from the same user
  aren't.
- **Wall-clock is typically 6-12 minutes.** xhigh codex passes can
  be 3-5 minutes each. Sonnet with web research is typically
  60-180s; gemini 2.5-pro is similar. Two layers of parallel calls
  add up to roughly the 6-12 minute range. Don't run this for
  trivial tasks.
- **Web research is required, not optional.** All prompts insist on
  it. If a CLI is rate-limited on its web search tool, the proposal
  or refinement will be weaker. Thin `research_sources` arrays in
  the manifest are a signal to retry later.
- **Heterogeneity is the point.** Three labs, three models. If you
  override the defaults so they converge on the same vendor, you've
  defeated the whole purpose of MoA.
- **Claude `--bare` mode is not used for sonnet.** `--bare` requires
  `ANTHROPIC_API_KEY` and skips OAuth/keychain auth. Subscription
  users must use full mode. The ~27K-token startup context tax is
  the cost of compatibility.

## Background

This skill is a from-scratch reimplementation of the planning-time use case
of the 2024 Mixture-of-Agents paper (arXiv:2406.04692, Wang et al., Together
AI), adapted for repo-grounded planning via Claude Code.

Version history:
- **v0.1:** 2 proposers (codex + gemini), cross-pair refinement, Opus aggregator.
- **v0.2:** 3 proposers (codex + gemini + sonnet), broadcast
  refinement (paper-faithful), Opus aggregator.
- **v0.2.2:** Hardening. Research ceilings in proposer/refiner
  prompts, subprocess-tree teardown on timeout, version-aware gemini
  approval flag, strict-mode JSON schema lint in preflight, richer
  manifest fields.
- **v0.2.3:** Per-agent timeouts with effort-aware defaults
  (`--codex-timeout`, `--gemini-timeout`, `--sonnet-timeout`).
  `--timeout` remains as a master override.

## Author

Kyle Boddy
