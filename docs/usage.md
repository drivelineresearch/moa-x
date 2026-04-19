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
   Estimated wall-clock: 6–12 minutes. Nothing spawns until you say yes.
3. **Proposers (Layer 1, parallel).** Three headless subprocesses
   fire in parallel: `codex exec`, `gemini -p`, `claude -p`. Each
   reads the repo (codex with a filesystem-enforced read-only
   sandbox; the others with read-only enforced by prompt), does web
   research, and writes an independent plan to
   `.moa/<session>/layer1/`.
4. **Broadcast refiners (Layer 2, parallel).** Two more subprocesses,
   `codex` and `gemini`, each receive all three proposals and
   produce verification output in `.moa/<session>/layer2/`.
   "Broadcast" means every refiner sees every proposal, per the MoA
   paper.
5. **Aggregator (Layer 3, in the parent session).** Claude Opus reads
   `.moa/<session>/synthesis-input.md`, synthesizes, honors refiner
   contradictions, and writes `.moa/<session>/final-plan.md`.
6. **Plan presented.** Claude shows you the plan and asks if you want
   to start executing.

## Running standalone

The Python orchestrator handles Layers 1 and 2. Layer 0 (scout brief)
and Layer 3 (aggregation) are your responsibility:

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
3. Aggregate manually following `harness/prompts/aggregator.md`. If
   you're driving a different agent harness, this is the prompt you'd
   feed it.

Self-MoA mode (three Sonnet proposers + two Sonnet refiners) is
available via `--self-moa`. See
`python3 harness/scripts/run_moa.py --help` for the full flag surface.

## Output layout

Each invocation creates a directory under `.moa/`:

```
.moa/20260418-143045-add-cache-layer/
├── scout-brief.json
├── layer1/
│   ├── codex-proposer.{json,log}
│   ├── gemini-proposer.{json,log}
│   └── sonnet-proposer.{json,log}
├── layer2/
│   ├── codex-refiner-broadcast.{json,log}
│   └── gemini-refiner-broadcast.{json,log}
├── synthesis-input.md    # what the aggregator reads
├── manifest.json         # timing + per-layer success/failure
└── final-plan.md         # written by the aggregator
```

`.moa/` is gitignored. Nothing the orchestrator produces should end
up in a commit. Prune old session directories yourself when you want
the disk space back.

## Failure modes

The orchestrator keeps going under partial failure rather than
aborting outright:

- One or two proposers fail: refiners still run on the survivors,
  the aggregator handles the degraded input and flags it.
- All three proposers fail: exit code 4. No synthesis.
- One refiner fails: the aggregator works with the surviving
  refiner's output. The aggregator prompt explicitly covers the
  single-refiner case.
- Schema validation fails for an agent: that agent is marked
  unsuccessful in the manifest, the run continues.
- A CLI is unauthenticated: preflight skips it and warns. If nothing
  can run, exit code 3.

Every failure writes to `.moa/<session>/.../*.log` so post-mortems
have the full CLI output.

## Limits

- **One MoA run per machine at a time.** A `flock` on `/tmp/moa.lock`
  stops concurrent runs from racing on shared CLI auth state.
- **Wall-clock is typically 6–12 minutes.** Don't run MoA-X for
  trivial tasks. The tool is shaped for non-trivial architecture
  work.
- **Web research is part of the contract.** Proposers and refiners
  are told to search and cite sources. Thin sources in the manifest
  usually mean a CLI was rate-limited on search. Retry later.
