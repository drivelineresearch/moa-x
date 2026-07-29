# mixture-of-agents

Layered planning ensemble for Claude Code. The configured proposers — by
default three models from three different labs (Google Gemini 3.1 Pro via
AGY, xAI Grok 4.5 via OpenCode, and OpenAI GPT-5.6 Luna via Codex) — read
the repo, do heavy web research, and write independent plans. The refiners
(Qwen qwen3.8-max-preview + Kimi K3 + Claude Opus 5 high) then
**broadcast-refine** by
reading all the proposals and producing cross-verifications. Layer 3
synthesizes one plan in a recorded Codex subprocess on `gpt-5.6-sol` at
`xhigh` reasoning.

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
3. Show you the brief and ask "ready to run? ~12-25 minutes".
4. On yes, spawn Gemini Pro, Grok 4.5, and GPT-5.6 Luna in parallel.
5. Spawn the broadcast refiners in parallel (Qwen, Kimi K3, and Claude Opus);
   each sees all proposals.
6. Synthesize the proposals + refinements into `final-plan.md` and the
   structured `final-plan.json` decision lineage.
7. Re-render the interactive report, present the plan, and ask whether to
   start executing.

## Architecture

```
Layer 0 — Scout brief                (parent Claude Code, in-place)
Layer 1 — Proposers (parallel)       (default Gemini Pro + Grok + GPT-5.6 Luna)
Layer 2 — Broadcast refiners         (default Qwen + Kimi + Opus, each sees all proposals)
Layer 3 — Aggregator                 (recorded GPT-5.6 Sol at xhigh)
```

Layer 0 happens in the parent REPL. Layer 3 runs through Codex with `--phase
layer3` by default. The Opus refiner is a separate `claude -p` headless
invocation, not the parent session.

### Why broadcast refinement (not cross-pair)

v0.1 of this skill used cross-pair refinement: each refiner saw only one
other proposer's plan. That wasn't paper-faithful. The 2024
Mixture-of-Agents paper (Wang et al., arXiv:2406.04692) uses full
broadcast: every refiner sees every proposal. v0.2 corrects this.
Broadcast refinement has the same wall-clock cost as cross-pair,
because refiners run in parallel either way, and it gives each refiner
the context to spot cross-proposer convergence and divergence signals
that a one-input view can't reveal.

### Why model families do not repeat across the default stages

The Balanced lane assigns Google, xAI, and OpenAI to proposal; Alibaba,
Moonshot, and Anthropic to broadcast refinement; and OpenAI to aggregation.
The refiners remain independent of the aggregator, preserving the
cross-check that matters most. Quick trims the lane to Gemini + Grok → Kimi →
Sol. Thorough adds GPT-5.6 Terra upstream and raises Opus to `max`.

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
  and shell tools through config; AGY requires plan
  mode plus sandboxing. Prompts repeat the read-only contract for
  every harness.
- **Heavy web research.** Every proposer and refiner is told to run
  at least 6-8 web searches and cite 5+ external sources.
- **CLI-first core.** The Claude Code skill remains the primary workflow; the
  repository also ships a local Flask control room over the same orchestrator.

## Install

Full install instructions live in the repo at
[`docs/install.md`](../docs/install.md). Short version: install the
vendor CLIs your roster needs and authenticate each, then drop `harness/`
into `~/.claude/skills/mixture-of-agents/`. The default roster needs:

- **codex** — `npm i -g @openai/codex && codex login`
- **agy / Antigravity** — install or update Antigravity, run `agy install`,
  sign in, and verify `agy models`
- **opencode** (runs Grok and Qwen/Kimi routes) — `curl -fsSL https://opencode.ai/install | bash`
  (or `npm i -g opencode-ai`), then `opencode auth login`, or export provider
  API keys (`MOONSHOT_API_KEY` /
  `QWEN_TOKEN_PLAN_API_KEY`)
- **claude** — the Claude Code CLI (runs the Balanced/Thorough Opus refiner)

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
│   ├── codex-luna-proposer.json
│   ├── codex-luna-proposer.log
│   ├── sonnet-proposer.json
│   └── sonnet-proposer.log
├── layer2/
│   ├── codex-sol-refiner-broadcast.json
│   ├── codex-sol-refiner-broadcast.log
│   ├── qwen-refiner-broadcast.json
│   └── qwen-refiner-broadcast.log
├── layer3/                # present after a subprocess aggregation
│   ├── aggregation-output.schema.json
│   └── codex-sol-aggregator.{json,log}
├── synthesis-input.md     # what the parent aggregator reads
├── manifest.json          # timing, success/failure per layer
├── report.html            # self-contained charts, plans, verdicts, and logs
├── final-plan.md          # written by parent or subprocess aggregator
└── final-plan.json        # exact proposer/refiner lineage for every final step
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
- **Workspace mutation is detected:** the agent is marked unsuccessful and
  the changed Git-visible paths are recorded in the manifest/report.
- **CLI not authenticated in preflight:** that CLI is skipped with
  a warning. If every needed harness fails preflight, the orchestrator
  exits with code 3.

External agents do not mutate the project working tree. Codex has filesystem
sandboxing, Claude has a read-only tool allowlist, OpenCode denies edit and
shell tools, and AGY requires plan mode plus sandboxing.
The orchestrator writes only its
gitignored `.moa/` session artifacts; the parent session edits project files
only after you approve the final plan.

## Tuning

Most defaults are right. Things you can override:

```bash
python3 ~/.claude/skills/mixture-of-agents/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json \
  --codex-model gpt-5.6-terra \
  --codex-effort high \
  --sonnet-model claude-sonnet-5 \
  --codex-timeout 1500 \
  --sonnet-timeout 1200 \
  --proposers agy-gemini-pro,grok,codex-luna \
  --refiners qwen,kimi,opus \
  --skip-layer2          # debug only; skips refiners
```

To aggregate an existing session through Codex without rerunning Layers 1–2:

```bash
python3 ~/.claude/skills/mixture-of-agents/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json \
  --phase layer3 \
  --aggregator-provider codex-sol \
  --aggregator-effort xhigh
```

This validates and writes `final-plan.md` plus `final-plan.json`, records the
Layer 3 log/timing, and regenerates the report.

Defaults:
- `--codex-model gpt-5.6-terra`
- `--codex-effort high`
- `--sonnet-model claude-sonnet-5`; stable Anthropic provider names remain
  `sonnet` and `opus`
- `--aggregator-provider codex-sol --aggregator-effort xhigh`
- `--proposers agy-gemini-pro,grok,codex-luna` and `--refiners qwen,kimi,opus`

The default Qwen refiner routes `qwen-token-plan/qwen3.8-max-preview` through
OpenCode with a 600-second cap. Set `QWEN_TOKEN_PLAN_API_KEY=sk-sp-...` in
`.env`; Qwen can also be included in the proposer set.

The Codex and Claude harnesses have dedicated flags. Every other harness
(OpenCode for Grok + Qwen/Kimi, and AGY) takes its
model/timeout/effort from the
`providers:` block in `harness/config.yaml` or from `MOA_<NAME>_MODEL` /
`MOA_<NAME>_TIMEOUT` / `MOA_<NAME>_EFFORT` env vars. You can also define a provider entirely from
the environment with `MOA_PROVIDER_<NAME>=<harness>:<model>`. OpenCode model
ids are `provider/model` strings. Current built-ins use
`opencode-go/grok-4.5`, `opencode-go/kimi-k3`, Qwen Token Plan
`qwen-token-plan/qwen3.8-max-preview`, and OpenCode Go
`opencode-go/qwen3.7-max`.

Per-agent timeout defaults:
- `--codex-timeout` scales with `--codex-effort`: xhigh=1500s, high=1200s, medium/low=900s
- `--sonnet-timeout 1200` (seconds; sonnet with full research can spike past 15 min)
- Other harnesses: `MOA_<NAME>_TIMEOUT` or `providers.<name>.timeout`
- `--timeout` is a master override that sets all at once. Leave unset
  to use the per-agent defaults tuned to observed tail latency

### Gemini research lane

The shipped roster uses the AGY provider with the Google account already
authenticated in the local CLI:

```yaml
layers:
  proposers: [agy-gemini-pro, grok, codex-luna]
  refiners: [qwen, kimi, opus]
  aggregator: codex-sol
```

`agy-gemini-pro` is the preferred AGY route for deep planning and review;
it is the sole curated Gemini route. It supports
proposer/refiner roles, and is guarded by
plan mode, sandboxing, schema validation, and the workspace snapshot check.
The live account catalog must expose the selected stable model slug.

## Limits and caveats

- **One MoA run per user at a time.** A per-UID `flock` under `/tmp`
  stops concurrent invocations from racing on shared CLI auth state.
  Sequential invocations are fine; parallel ones from the same user
  aren't.
- **Wall-clock is typically 12-25 minutes for research-heavy work.** Provider
  latency can extend the tail; Qwen's default refiner cap is 600 seconds. Don't run
  this for trivial tasks.
- **Web research is required, not optional.** All prompts insist on
  it. If a CLI is rate-limited on its web search tool, the proposal
  or refinement will be weaker. Thin `research_sources` arrays in
  the manifest are a signal to retry later.
- **Heterogeneity is the point.** The default roster spans seven labs
  (Google, xAI, Zhipu, Alibaba, Moonshot, Anthropic, OpenAI). If you override the defaults so
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
- **v0.3.0:** Named-provider roster refactor. At that release, harnesses were
  codex, claude, opencode, and cursor; the standalone Google adapter was dropped.
  The default roster was codex + glm + sonnet proposers with codex-sol + qwen
  refiners. Providers became declarable via `MOA_PROVIDER_<NAME>` env
  shorthand or the config.yaml `providers:` block.
- **v0.4.0:** Self-contained HTML session report with pipeline, timing,
  verdict, plan, and log views; GLM and Kimi defaults moved to the
  `opencode-go` gateway.
- **v0.4.1:** Qwen Token Plan became a built-in provider; Claude and
  OpenCode structured-output handling, refiner normalization, optional-provider
  selection, routing diagnostics, documentation, and workflow art were
  hardened and refreshed.
- **Current development:** AGY provides Gemini Pro in every recommended
  proposer roster; Qwen + Opus refine the Balanced default; and GPT-5.6 Sol
  at `xhigh` aggregates every recommended mode. The local Flask control room
  adds queued runs, provider
  probes, SQLite history, sandbox-independent PDF/text context extraction,
  durable uploads, browser profiles, and exact-owner
  GitHub workspaces configured with `MOA_WEBUI_GITHUB_OWNER`.

## Author

Kyle Boddy
