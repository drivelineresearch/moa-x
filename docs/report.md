# The HTML run report

Every full MoA-X run ends by rendering a single, self-contained HTML
report of the session to `.moa/<session>/report.html`. Open it in any
browser — it needs no server and makes zero network requests. Everything
(page, charts, the Three.js build, the session data, the rendered final
plan) is inlined into the one file.

The report is a visual post-mortem of the engine: what was asked, which
models ran, how long each took, what they proposed, how the refiners
cross-examined those proposals, and the aggregated final plan.

## What it shows

- **Header + overview tiles** — session id, wall-clock, proposer/refiner
  success counts, and the parallel speed-up (summed agent time ÷
  wall-clock).
- **3D pipeline (Three.js)** — the four layers as a live scene: scout →
  proposers → *broadcast* refiners → Opus aggregator. Every proposer
  fans into every refiner, which is the visual argument for broadcast
  refinement. Node size scales with runtime, color with status. Drag to
  orbit; click a node to jump to that agent's section. Falls back to a
  static SVG under `prefers-reduced-motion` and in print.
- **Timeline (SVG Gantt)** — one bar per agent, grouped by layer. Uses
  each agent's recorded start offset when present, else layer-derived
  offsets (Layer 1 from t0, Layer 2 after the slowest proposer).
- **Scout brief** — frozen spec, in/out of scope, focus files,
  clarifications.
- **Layer 1 proposers** — per-proposer summary, plan steps
  (step/why/files/risks), evidence chips (`code` = file:line, `external`
  = links), and research sources.
- **Layer 2 refiners** — a verdict matrix (refiners × proposers), an
  evidence-verification dot matrix (verified / unverified / contradicted,
  click a dot for the finding), agreements, disagreements, missing and
  incorrect steps, and each refiner's `synthesis_recommendation` as a
  pull-quote.
- **Aggregated final plan** — `final-plan.md` rendered inline (or a
  "not yet aggregated" note when the parent session hasn't written it).
- **Raw logs** — collapsible per-agent STDOUT/STDERR with a line filter.

## Generating it manually

The orchestrator writes it automatically, but you can (re)render any
session — including after the parent session writes `final-plan.md`:

```bash
# a specific session
python3 harness/scripts/report.py --session .moa/<session-id>

# the newest session under .moa/
python3 harness/scripts/report.py --latest

# custom output path
python3 harness/scripts/report.py --session .moa/<session-id> -o /tmp/run.html
```

It reads `manifest.json` (or `layer1-manifest.json` for a phase-split
Layer-1-only run, rendered as *partial*) and exits 2 if neither exists.

## Turning it off

Pass `--no-report` to `run_moa.py` (or set `MOA_NO_REPORT=1`) to skip
report generation. Report rendering is best-effort: if it fails, the run
still succeeds — the manifest and `synthesis-input.md` are already on
disk — and a warning is printed.

## Design

The report follows the Driveline Baseball white-surface design language:
pure white canvas, the signature 8px Mine Shaft top bar, goldenrod
`#FFA300` as the sole accent, no shadows, and hand-crafted SVG charts
(no chart library). Fonts fall back to a system stack because the
proprietary Gotham/Lato faces can't be inlined into a shareable
single file.

Template + vendored assets live in `harness/report/`
(`template.html`, `three.min.js`); the generator is
`harness/scripts/report.py`.
