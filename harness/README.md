# mixture-of-agents

Layered planning ensemble for Claude Code. The configured proposers — by
default three models from three different labs (OpenAI codex/gpt-5.4, Zhipu
glm/5.2 via the opencode CLI, Anthropic sonnet/4.6) — read the repo, do heavy
web research, and write independent plans. The refiners (default codex + kimi,
Moonshot's kimi-k2.7-code via opencode) then **broadcast-refine** by reading
all the proposals and producing cross-verifications. The parent Claude Code
session (Opus 4.6) synthesizes the whole thing into one actionable plan.

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
4. On yes, spawn the proposers in parallel (default `codex exec`, `opencode run` for GLM, and `claude -p`).
5. Spawn the broadcast refiners in parallel (default `codex exec` and `opencode run` for Kimi); each sees all proposals.
6. Synthesize the proposals + refinements into `final-plan.md`.
7. Present the plan and ask whether to start executing.

## Architecture

```
Layer 0 — Scout brief                (parent Opus, in-place)
Layer 1 — Proposers (parallel)       (default codex + glm + sonnet subprocesses)
Layer 2 — Broadcast refiners         (default codex + kimi subprocesses, each sees all proposals)
Layer 3 — Aggregator                 (parent Opus 4.6, in-place)
```

Claude work that happens in the parent REPL: Layer 0 (scout brief) and
Layer 3 (aggregation). The sonnet Layer-1 subprocess is a separate
`claude -p` headless invocation, not the parent session. The Python
orchestrator at `scripts/run_moa.py` handles Layers 1 and 2.

### Why broadcast refinement (not cross-pair)

v0.1 of this skill used cross-pair refinement: each refiner saw only one
other proposer's plan. That wasn't paper-faithful. The 2024
Mixture-of-Agents paper (Wang et al., arXiv:2406.04692) uses full
broadcast: every refiner sees every proposal. v0.2 corrects this.
Broadcast refinement has the same wall-clock cost as cross-pair,
because refiners run in parallel either way, and it gives each refiner
the context to spot cross-proposer convergence and divergence signals
that a one-input view can't reveal.

### Why sonnet is proposer-only

Opus 4.6 is the Layer 3 aggregator. Sonnet 4.6 is a Layer 1 proposer. Layer
2 (the refiner/verification step) is kept to {codex, kimi} so that the
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
  sandbox; Claude gets a hard read-only tool allowlist; OpenCode denies edit
  and shell tools through config; Cursor runs in plan mode. Prompts repeat the
  read-only contract for every harness.
- **Heavy web research.** Every proposer and refiner is told to run
  at least 6-8 web searches and cite 5+ external sources.
- **CLI-first workflow.** Runs entirely from inside Claude Code,
  with no separate web UI or deploy.

## Install

Full install instructions live in the repo at
[`docs/install.md`](../docs/install.md). Short version: install the
vendor CLIs your roster needs and authenticate each, then drop `harness/`
into `~/.claude/skills/mixture-of-agents/`. The default roster needs:

- **codex** — `npm i -g @openai/codex && codex login`
- **opencode** (runs GLM + Kimi) — `curl -fsSL https://opencode.ai/install | bash`
  (or `npm i -g opencode-ai`), then `opencode auth login`, or export provider
  API keys (`ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `FIREWORKS_API_KEY` /
  `QWEN_TOKEN_PLAN_API_KEY`)
- **claude** — the Claude Code CLI (runs the sonnet proposer)
- **cursor** (only if you configure a cursor-routed provider like `composer`)
  — `curl https://cursor.com/install -fsS | bash`, then `cursor-agent login`
  (the binary is `cursor-agent`, or just `agent` on newer installs)

```bash
cp -r harness ~/.claude/skills/mixture-of-agents
python3 ~/.claude/skills/mixture-of-agents/scripts/install_deps.py
```

The preflight script only checks (config-aware: it probes just the harnesses
your resolved roster uses). It never installs or auths anything for you.

## Output artifacts

Each invocation creates a session directory under `.moa/` (in your current
working directory by default):

```
.moa/20260408-101530-add-cache-layer/
├── scout-brief.json
├── layer1-manifest.json  # phase-split checkpoint / redispatch state
├── layer1/
│   ├── codex-proposer.json
│   ├── codex-proposer.log
│   ├── glm-proposer.json
│   ├── glm-proposer.log
│   ├── sonnet-proposer.json
│   └── sonnet-proposer.log
├── layer2/
│   ├── codex-refiner-broadcast.json
│   ├── codex-refiner-broadcast.log
│   ├── kimi-refiner-broadcast.json
│   └── kimi-refiner-broadcast.log
├── synthesis-input.md     # what the parent Opus session reads
├── manifest.json          # timing, success/failure per layer
├── report.html            # self-contained charts, plans, verdicts, and logs
└── final-plan.md          # written by parent Opus; absent before aggregation
```

`.moa/` should be in your repo's `.gitignore`. Sessions are kept locally
for audit/debug; prune old ones manually if they accumulate.

## Failure modes

The orchestrator keeps going under partial failure:

- **1-2 proposers fail, at least 1 succeeds:** refiners see the
  proposers that worked, the aggregator proceeds, and the manifest
  notes the degraded run.
- **All proposers fail:** `--phase layer1` writes `layer1-manifest.json` and
  exits 0 so the parent can offer redispatch; legacy `--phase all` exits 4.
- **One refiner fails, one succeeds:** the aggregator proceeds with
  the surviving refiner's output. The aggregator prompt handles the
  single-refiner case explicitly.
- **Schema validation fails for an agent:** that agent is marked
  unsuccessful, the manifest records why, and the run continues with
  what's left.
- **CLI not authenticated in preflight:** that CLI is skipped with
  a warning. If every needed harness fails preflight, the orchestrator
  exits with code 3.

External agents do not mutate the project working tree. Codex has filesystem
sandboxing, Claude has a read-only tool allowlist, OpenCode denies edit and
shell tools, and Cursor uses plan mode. The orchestrator writes only its
gitignored `.moa/` session artifacts; the parent session edits project files
only after you approve the final plan.

## Tuning

Most defaults are right. Things you can override:

```bash
python3 ~/.claude/skills/mixture-of-agents/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json \
  --codex-model gpt-5.4 \
  --codex-effort xhigh \
  --sonnet-model claude-sonnet-4-6 \
  --codex-timeout 1500 \
  --sonnet-timeout 1200 \
  --proposers codex,glm,sonnet \
  --refiners codex,kimi \
  --skip-layer2          # debug only; skips refiners
```

Defaults:
- `--codex-model gpt-5.4`
- `--codex-effort high`
- `--sonnet-model claude-sonnet-4-6`
- `--proposers codex,glm,sonnet` and `--refiners codex,kimi`

Optional built-in: `qwen` routes `qwen-token-plan/qwen3.7-max` through
OpenCode. Set `QWEN_TOKEN_PLAN_API_KEY=sk-sp-...` in `.env`, then include
`qwen` in `--proposers` or `MOA_PROPOSERS`.

The codex and sonnet harnesses have dedicated flags. Every other harness
(opencode for GLM + Kimi, cursor) takes its model/timeout from the
`providers:` block in `harness/config.yaml` or from `MOA_<NAME>_MODEL` /
`MOA_<NAME>_TIMEOUT` env vars. You can also define a provider entirely from
the environment with `MOA_PROVIDER_<NAME>=<harness>:<model>`, e.g.
`MOA_PROVIDER_GLM=opencode:opencode-go/glm-5.2`. Opencode model ids are
`provider/model` strings (`opencode-go/glm-5.2` and
`opencode-go/kimi-k2.7-code` are the defaults; `zhipuai/glm-5.2`,
`moonshotai/kimi-k2.7-code`, and Fireworks-hosted
`fireworks-ai/accounts/fireworks/models/glm-5p2` also work).

Per-agent timeout defaults:
- `--codex-timeout` scales with `--codex-effort`: xhigh=1500s, high=1200s, medium/low=900s
- `--sonnet-timeout 1200` (seconds; sonnet with full research can spike past 15 min)
- Other harnesses: `MOA_<NAME>_TIMEOUT` or `providers.<name>.timeout`
- `--timeout` is a master override that sets all at once. Leave unset
  to use the per-agent defaults tuned to observed tail latency

### Want Gemini in the mix?

Gemini's dedicated adapter was removed in v0.3.0, but you can still route a
Gemini model through the **cursor** harness as a user-named provider. In
`harness/config.yaml`:

```yaml
providers:
  cursor-gemini: {harness: cursor, model: gemini-3.1-pro}
```

Then add `cursor-gemini` to `layers.proposers` (or `layers.refiners`), or
pass it on the CLI: `--proposers codex,cursor-gemini,sonnet`.

## Limits and caveats

- **One MoA run per machine at a time.** A `flock` on `/tmp/moa.lock`
  stops concurrent invocations from racing on shared CLI auth state.
  Sequential invocations are fine; parallel ones from the same user
  aren't.
- **Wall-clock is typically 6-12 minutes.** xhigh codex passes can
  be 3-5 minutes each. Sonnet with web research is typically
  60-180s; the opencode providers (GLM, Kimi) are similar. Two layers
  of parallel calls add up to roughly the 6-12 minute range. Don't run
  this for trivial tasks.
- **Web research is required, not optional.** All prompts insist on
  it. If a CLI is rate-limited on its web search tool, the proposal
  or refinement will be weaker. Thin `research_sources` arrays in
  the manifest are a signal to retry later.
- **Heterogeneity is the point.** The default roster spans four labs
  (OpenAI, Zhipu, Anthropic, Moonshot). If you override the defaults so
  they converge on the same vendor, you've defeated the whole purpose of MoA.
- **Claude `--bare` mode is not used for sonnet.** `--bare` requires
  `ANTHROPIC_API_KEY` and skips OAuth/keychain auth, which means
  subscription-only users would be locked out. The adapter accepts
  either auth path, so the default stays on full mode. The ~27K-token
  startup context tax is the cost of that compatibility. A PR that
  detects an API key in the environment and opts into `--bare` for
  the faster path is welcome.

## Background

This skill is a from-scratch reimplementation of the planning-time use case
of the 2024 Mixture-of-Agents paper (arXiv:2406.04692, Wang et al., Together
AI), adapted for repo-grounded planning via Claude Code.

Version history:
- **v0.1:** 2 proposers, cross-pair refinement, Opus aggregator.
- **v0.2:** 3 proposers (added the sonnet proposer), broadcast
  refinement (paper-faithful), Opus aggregator.
- **v0.2.2:** Hardening. Research ceilings in proposer/refiner
  prompts, subprocess-tree teardown on timeout, version-aware CLI
  approval flags, strict-mode JSON schema lint in preflight, richer
  manifest fields.
- **v0.2.3:** Per-agent timeouts with effort-aware defaults
  (`--codex-timeout`, `--sonnet-timeout`). `--timeout` remains as a
  master override.
- **v0.3.0:** Named-provider roster refactor. Harnesses are now codex,
  claude, opencode, and cursor; the standalone Google adapter was dropped.
  Default roster spans four labs — proposers codex + glm + sonnet, refiners
  codex + kimi. Providers are declarable via `MOA_PROVIDER_<NAME>` env
  shorthand or the config.yaml `providers:` block; route a Gemini model
  through the cursor harness (`cursor-gemini`) if you still want one.
- **v0.4.0:** Self-contained HTML session report with pipeline, timing,
  verdict, plan, and log views; GLM and Kimi defaults moved to the
  `opencode-go` gateway.
- **v0.4.1:** Qwen Token Plan became an optional built-in provider; Claude and
  OpenCode structured-output handling, refiner normalization, optional-provider
  selection, routing diagnostics, documentation, and workflow art were
  hardened and refreshed.

## Author

Kyle Boddy
