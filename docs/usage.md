# Usage

MoA-X has three entry points. Pick the one that matches how you work.

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
   fire in parallel by default: `agy-gemini-pro` (AGY harness), `grok`
   (OpenCode), and `codex-luna` (Codex) — Google, xAI, and OpenAI.
   Additional supported custom routes are documented in
   [`docs/config.md`](config.md). Each
   reads the repo under adapter-specific read-only controls (filesystem
   sandboxing for Codex, a hard tool allowlist for Claude, and a
   permission-deny policy for OpenCode), does web research, and writes an independent plan to
   `.moa/<session>/layer1/`.
4. **Broadcast refiners (Layer 2, parallel).** Three more subprocesses,
   `qwen` (`qwen3.8-max-preview`), `deepseek` (`deepseek-v4-pro`), and `opus`
   (`claude-opus-5`, high), each
   receive every valid proposal and
   produce verification output in `.moa/<session>/layer2/`.
   "Broadcast" means every refiner sees every proposal, per the MoA
   paper.
5. **Aggregator (Layer 3).** By default GPT-5.6 Sol at `xhigh` reads the
   retained `.moa/<session>/synthesis-input.md` through the recorded Codex
   phase, synthesizes, honors refiner contradictions, and writes
   `.moa/<session>/final-plan.md` plus the structured `final-plan.json`
   decision lineage.
   The Web UI also exposes Claude Opus 5 and Fable 5 1M at `xhigh` as
   alternative Claude aggregators. Fable selection is guarded by a
   warning/password modal because it can consume very large quotas; Fable is
   not available in either upstream layer.
6. **Plan presented.** Claude shows you the plan and asks if you want
   to start executing.

## Local Web UI

Install the optional web dependencies and start the control room:

```bash
.venv/bin/pip install -r requirements-web.txt
.venv/bin/python -m harness.webui
```

The UI accepts a raw goal, resolves the selected named-provider roster, queues
the phase subprocesses, streams lifecycle events, and keeps run history in
SQLite. It uses the CLI accounts already authenticated for the server's OS
user.

Repository context is optional. **Task only** launches create an isolated
managed workspace for the task and any attachments. GitHub launches are restricted to
the single owner configured by `MOA_WEBUI_GITHUB_OWNER` and shallow-clone
through the authenticated `gh` CLI into managed XDG storage. Up to 10
reference files (25 MB each) can be attached, copied into the run's
`.moa/<session>/inputs/` snapshot, and converted into shared Markdown context
for every model layer. See [`webui.md`](webui.md).

## Running standalone

The Python orchestrator handles Layers 1 and 2. Layer 0 (scout brief) remains
the caller's responsibility; Layer 3 runs as a recorded Codex phase:

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
3. Run the retained session's Codex-backed Layer 3:

   ```bash
   python3 harness/scripts/run_moa.py \
     --scout-brief .moa/<session>/scout-brief.json \
     --phase layer3 \
     --aggregator-provider codex-sol \
     --aggregator-effort xhigh
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
│   ├── codex-luna-proposer.{json,log}
│   └── sonnet-proposer.{json,log}
├── layer2/
│   ├── codex-sol-refiner-broadcast.{json,log}
│   └── qwen-refiner-broadcast.{json,log}
├── layer3/
│   ├── aggregation-output.schema.json
│   └── codex-sol-aggregator.{json,log}
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
- All proposers fail: `--phase layer1` writes the checkpoint manifest
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
