# Usage

MoA-X has two entry points. Pick the one that matches how you work.

## Inside Claude Code (primary)

Once [installed as a skill](install.md#3-install-as-a-claude-code-skill-primary-path),
invoke from any project directory:

```
/mixture-of-agents
```

or paste a spec file:

```
/mixture-of-agents --spec ./docs/cache-layer-spec.md
```

What happens:

1. **Scout (Layer 0, in the parent session).** Claude reads your spec,
   asks 1–3 clarifying questions, and writes a scout brief
   (focus files, in-scope, out-of-scope) to `.moa/<session>/scout-brief.json`.
2. **Approval gate.** The skill shows you the brief and asks "run it?"
   Estimated wall-clock: roughly 12–25 minutes for research-heavy runs.
   Nothing spawns until you say yes.
3. **Proposers (Layer 1, parallel).** Three headless subprocesses
   fire in parallel by default: `codex` (codex harness), `glm`
   (opencode harness), and `sonnet` (claude harness) — OpenAI,
   Zhipu, and Anthropic. Additional lanes (a `cursor` provider,
   Kimi, or any user provider) are available — see
   [`docs/install.md`](install.md#optional-cursor-cli-extra-provider)
   and [`docs/config.md`](config.md). Each
   reads the repo (codex with a filesystem-enforced read-only
   sandbox; the others with read-only enforced by prompt), does web
   research, and writes an independent plan to
   `.moa/<session>/layer1/`.
4. **Broadcast refiners (Layer 2, parallel).** Two more subprocesses,
   `codex-reviewer` (`gpt-5.6-sol`, high) and `qwen`
   (`qwen3.8-max-preview` — independent of the Anthropic aggregator), each
   receive every valid proposal and
   produce verification output in `.moa/<session>/layer2/`.
   "Broadcast" means every refiner sees every proposal, per the MoA
   paper.
5. **Aggregator (Layer 3).** By default Claude Code's rolling `opus` alias in
   the parent session reads
   `.moa/<session>/synthesis-input.md`, synthesizes, honors refiner
   contradictions, and writes `.moa/<session>/final-plan.md` plus the
   structured `final-plan.json` decision lineage. You can instead run the
   recorded Codex phase shown below.
6. **Plan presented.** Claude shows you the plan and asks if you want
   to start executing.

## Running standalone

The Python orchestrator handles Layers 1 and 2. Layer 0 (scout brief) remains
the caller's responsibility; Layer 3 can be interactive or automated:

```bash
python3 harness/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json
```

You'll need to:

1. Write `scout-brief.json` yourself. The required fields are listed
   at the top of `harness/prompts/scout.md`.
2. After the script exits, read `.moa/<session>/synthesis-input.md` —
   this file has the frozen spec, scout brief, and all proposer and
   refiner outputs concatenated.
3. Aggregate manually following `harness/prompts/aggregator.md`, or run only
   the retained session's Codex-backed Layer 3:

   ```bash
   python3 harness/scripts/run_moa.py \
     --scout-brief .moa/<session>/scout-brief.json \
     --phase layer3 \
     --aggregator-provider codex-aggregator \
     --aggregator-effort high
   ```

   The phase validates the combined Markdown/lineage response, rejects stale
   lineage pointers, writes both final artifacts, records Layer 3 timing and
   logs in the manifest, and regenerates `report.html`. It does not rerun the
   proposers or refiners.

Self-MoA mode (three Sonnet proposers + two Sonnet refiners) is
available via `--self-moa`. See
`python3 harness/scripts/run_moa.py --help` for the full flag surface.

## Output layout

Each invocation creates a directory under `.moa/`:

```
.moa/20260418-143045-add-cache-layer/
├── scout-brief.json
├── layer1-manifest.json  # phase-split checkpoint and redispatch state
├── layer1/
│   ├── codex-proposer.{json,log}
│   ├── glm-proposer.{json,log}
│   └── sonnet-proposer.{json,log}
├── layer2/
│   ├── codex-reviewer-refiner-broadcast.{json,log}
│   └── qwen-refiner-broadcast.{json,log}
├── layer3/
│   ├── aggregation-output.schema.json
│   └── codex-aggregator-aggregator.{json,log}
├── synthesis-input.md    # what the aggregator reads
├── manifest.json         # timing + per-layer success/failure
├── report.html           # self-contained HTML charts, plans, and logs
├── final-plan.md         # human-readable plan; absent until Layer 3
└── final-plan.json       # structured decision lineage for report.html
```

`.moa/` is gitignored. Nothing the orchestrator produces should end
up in a commit. Prune old session directories yourself when you want
the disk space back.

## Failure modes

The orchestrator keeps going under partial failure rather than
aborting outright:

- One or two proposers fail: refiners still run on the survivors,
  the aggregator handles the degraded input and flags it.
- All three proposers fail: `--phase layer1` writes the checkpoint manifest
  and exits 0 so the parent can offer redispatch; legacy `--phase all` exits
  code 4 with no synthesis.
- One refiner fails: the aggregator works with the surviving
  refiner's output. The aggregator prompt explicitly covers the
  single-refiner case.
- Schema validation fails for an agent: that agent is marked
  unsuccessful in the manifest, the run continues.
- A provider mutates the Git-visible workspace: the cross-harness before/after
  guard marks that agent unsuccessful and records the changed paths.
- A CLI is unauthenticated: preflight skips it and warns. If nothing
  can run, exit code 3.

Every failure writes to `.moa/<session>/.../*.log` so post-mortems
have the full CLI output.

## The HTML run report

Each full run also writes a single self-contained
`.moa/<session>/report.html` — a visual post-mortem with a 3D pipeline
view, per-agent Gantt, proposer plans, the refiner verdict matrix and
evidence verification, an interactive final-step decision-lineage explorer,
and the aggregated final plan. Open it directly in a browser (no server, no
network). Re-render any session (e.g. after `final-plan.md` and
`final-plan.json` are written) with
`python3 harness/scripts/report.py --session .moa/<id>` or `--latest`.
Skip it with `--no-report` (or `MOA_NO_REPORT=1`). Full details:
[`docs/report.md`](report.md).

## Limits

- **One MoA run per user at a time.** A `flock` on the per-user system-temp
  lock (`moa-<uid>.lock` on POSIX) stops concurrent runs from racing on shared
  CLI auth state without blocking another OS user.
- **Wall-clock is typically 12–25 minutes for research-heavy runs.** Don't run MoA-X for
  trivial tasks. The tool is shaped for non-trivial architecture
  work.
- **Web research is part of the contract.** Proposers and refiners
  are told to search and cite sources. Thin sources in the manifest
  usually mean a CLI was rate-limited on search. Retry later.
