# The HTML run report

Every full MoA-X run ends by rendering a single, self-contained HTML
report of the session to `.moa/<session>/report.html`. Open it in any
browser — it needs no server and makes zero network requests. Everything
(page, charts, illustrations, session data, evidence-weighted decision map,
exact lineage, and the rendered final recommendation) is inlined into the one
file.

When the report is opened through the Web UI's private artifact URL, its
header also includes **Share report**. It creates and copies the same
revocable bearer link available from the run page. That control is deliberately
absent from downloaded reports and public shared-report links, which remain
standalone and make no network requests.

If Codex Desktop shows `report.html` as markup, open the same file in your
system web browser from the file manager or the browser's **File > Open**
command. The report remains self-contained; this does not require a local
server, upload, or network connection.

The report is a reader-first record of the decision: the recommendation is
easy to reach, a plain-language overview explains what happened, and the
supporting process and raw output remain available for deeper inspection.

## What it shows

- **Header + executive overview** — a prominent jump to the final
  recommendation, a plain-language summary from the recorded lineage,
  completion counts, session details, elapsed time, parallel speed-up, and
  visible run-health warnings for timeouts, identity mismatches, transient
  empty responses, or workspace mutations.
- **Static contributor network** — an embedded illustration and a four-stage
  roster showing how the task brief moved through independent
  recommendations, review, and final synthesis. Names, counts, completion
  status, and configured aggregator details come from the session data. The
  view does not require WebGL, motion, or a network connection.
- **Compact stage timeline** — one readable track per contributor, grouped
  into independent recommendations, review, and final-decision stages. Each
  stage uses its own scale, so a long retry or handoff cannot compress the
  useful bars into unreadable slivers. The report still preserves that time:
  delays are labeled between stages, and each stage shows when it began
  relative to the start of the run.
- **Task brief** — frozen spec, in/out of scope, focus files,
  clarifications.
- **Independent recommendations** — per-contributor summary, plan steps
  (step/why/files/risks), evidence chips (`code` = file:line, `external`
  = links), and research sources.
- **Review and challenge** — a verdict matrix (reviewers × recommendations), a
  transparent at-a-glance consensus grade derived only from those structured
  verdicts, and the number of final decisions citing each contributor. The
  detailed verdicts remain visible, so the grade is never a black box. An
  evidence-verification dot matrix (verified / unverified / contradicted,
  click a dot for the finding), agreements, disagreements, missing and
  incorrect steps, and each reviewer's `synthesis_recommendation` as a
  pull-quote.
- **Evidence-weighted living decision map** — model-lab lanes connect retained
  evidence receipts to atomic claims and final decisions. Select a node to
  inspect its source, review status, and downstream use. A quality strip,
  explicit gates, evidence-debt warnings, and an accessible ledger expose
  coverage and independence without turning a model's confidence into a score.
- **Decision trail** — select any final-recommendation step to see the exact
  source steps and review findings that shaped it. Solid paths mean used as
  written, dashed paths mean revised, and dotted paths show reviewer influence;
  click any node for its original reasoning. Rejected proposer steps remain
  available below the graph. The explorer uses `final-plan.json`, validates
  every pointer against the retained agent payloads, and shows non-fatal
  warnings for stale references.
- **Final recommendation** — `final-plan.md` rendered inline (or a
  "not yet aggregated" note when neither the parent nor a Layer 3 subprocess
  has written it). A one-click **Copy final plan as Markdown** control copies
  the original Markdown—not reconstructed HTML—using the Clipboard API with a
  browser-compatible fallback and an accessible status message. If a browser
  blocks automatic clipboard access on a non-secure connection, the report
  reveals and selects the original Markdown for an immediate Ctrl/Cmd+C
  instead of invoking the deprecated synchronous copy command.
  Nested Markdown lists retain one continuous ordered plan, so indented
  evidence bullets no longer restart every top-level step at `1`. GitHub-style
  pipe tables, column alignment, inline code, emphasis, bold text, links, and
  escaped pipes are rendered as accessible HTML; wide tables scroll inside
  the recommendation instead of widening the page.
- **Technical logs** — collapsible per-contributor STDOUT/STDERR with a line
  filter.

Wide evidence tables, verdict matrices, decision maps, lineage graphs, and step
tabs scroll
inside their own containers on narrow screens. They do not widen the page, so
the report remains usable on phones and small browser windows.

## Generating it manually

The orchestrator writes it automatically, but you can (re)render any
session — including after the parent or `--phase layer3` writes
`final-plan.md` and its `final-plan.json` lineage companion:

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
The renderer reads the current `decision-map.json` or derives an up-to-date map
from retained artifacts, so older checkpoints can use the same presentation.
For v0.4.1 and older phase-split sessions, the renderer also repairs a
phase-local manifest start time from the earliest retained agent timestamp so
the wall-clock and Gantt offsets cover the whole run.

## Evidence-weighted decision-map data

`decision-map.json` is an orchestrator-owned derivative. The report validates
it against `harness/scripts/schemas/decision-map.schema.json` before rendering.
It joins retained proposer evidence, canonical refiner verification pointers,
and final-plan lineage with stable content IDs. Its stage advances through `setup`,
`proposals`, `review`, and `complete`; the same visual therefore gains
evidence, review findings, and decisions without changing its vocabulary.

Repository receipts contain commit/tree identity plus status and diff hashes.
Code receipts add file and cited-line hashes; they do not copy repository file
contents into the map. External receipts retain the canonical URL and declared
snippet hash but do not claim that MoA-X archived the source page. The map also
records source concentration, independent reviewer-lab coverage,
contradictions, lineage coverage, and unresolved-pointer warnings.

The report presents the aggregator's stated confidence separately from the
deterministic evidence ceiling. Effective confidence is the lower of the two,
so an unsupported or incompletely reviewed plan cannot display a stronger
result merely because the aggregator selected `high`. Legacy sessions still
render; missing live receipts are labeled unavailable rather than reconstructed
from current repository contents.

## Decision-lineage data

Layer 3 writes `final-plan.json` alongside `final-plan.md`. Its schema is
`harness/scripts/schemas/final-plan.schema.json`. Each final step records a
decision (`accepted`, `revised`, or `new`), an adjudication, exact zero-based
references to source proposer steps, and exact references to refiner findings.
The Markdown remains the human-readable plan; the JSON is deliberately a
small provenance companion rather than a second copy of every plan field.

Older sessions without `final-plan.json` remain fully readable and show a
lineage-unavailable notice. A structurally invalid file is ignored with a
visible warning. Valid files with stale pointers still render, with each bad
pointer listed so the source can be corrected.

The lineage graph separates a review category (for example, `KIMI · MISSING
STEP`) from its content: the card summarizes the actual missing work and
expands to the full finding on hover or selection. Repeated reviewer labels
are therefore distinct findings cited by the final decision, not placeholders.
The same readable-card treatment applies to verifications, disagreements,
incorrect steps, and synthesis recommendations; internal provenance paths
remain available in the detailed review instead of becoming graph-card titles.

## Turning it off

Pass `--no-report` to `run_moa.py` (or set `MOA_NO_REPORT=1`) to skip
report generation. Report rendering is best-effort: if it fails, the run
still succeeds — the manifest and `synthesis-input.md` are already on
disk — and a warning is printed.

## Design

The report follows the Driveline Baseball white-surface design language:
pure white canvas, the signature 8px Mine Shaft top bar, goldenrod
`#FFA300` as the sole accent, no shadows, and hand-crafted SVG charts
(no chart library). Fonts use a system stack so the report stays
license-clean and portable as a single file.

Collapsible sections use native buttons with keyboard/ARIA state. The decision
map includes native node controls and a table ledger, while the decision-lineage
tab rail supports arrow, Home, and End keys.

The template and embedded illustration assets live in `harness/report/`; the
generator is `harness/scripts/report.py`.
