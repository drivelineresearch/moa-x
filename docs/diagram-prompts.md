# Diagram & image prompts

The brand images are current and live:

- `docs/moa-x-header.png` — the README hero banner (generated from Prompt 2).
- `docs/moa-x-workflow.png` — the four-stage workflow illustration, embedded in
  the README and `docs/architecture.md` (generated from Prompt 1).

The banner reflects the four-lab default roster. The workflow image shows the
current default proposers (Codex Terra, GLM 5.2, rolling Sonnet), current
broadcast refiners (Codex Sol and Qwen 3.8), default Opus aggregation, and the
generated plan/report artifacts. The prompts below are the source of truth for
regenerating or tweaking them — keep the same style so the pair stays visually
consistent.
When regenerating, overwrite the file in place (keep the filename so the embeds
keep working).

## Prompt 1 — workflow illustration (`docs/moa-x-workflow.png`)

Layout note: image models scramble the stage order unless the quadrants and
the arrow path are stated up front and explicitly. Keep the 2×2 grid + numbered
Z-path exactly as written below.

> Isometric technical illustration, hand-drawn vector style: bold black
> outlines, flat color fills with subtle cel-shading, on a light warm-gray
> background. Clean sans-serif labels.
>
> COMPOSITION — a 2×2 grid of four equal quadrants, one numbered stage per
> quadrant, connected by thick numbered arrows in strict order 1 → 2 → 3 → 4
> (a Z-shaped reading path). Exact placement, do not rearrange:
> - TOP-LEFT quadrant = stage ① SCOUT
> - TOP-RIGHT quadrant = stage ② PROPOSERS
> - BOTTOM-LEFT quadrant = stage ③ BROADCAST REFINERS
> - BOTTOM-RIGHT quadrant = stage ④ AGGREGATOR
> Draw the flow as three big labeled arrows: arrow ①→② runs straight across the
> TOP edge, left to right (Scout to Proposers); arrow ②→③ sweeps diagonally
> down the middle from top-right to bottom-left (Proposers to Refiners); arrow
> ③→④ runs straight across the BOTTOM edge, left to right (Refiners to
> Aggregator). Each stage header is prefixed with its circled number. Keep every
> quoted label exactly spelled and legible; do not invent model names.
>
> ① SCOUT (top-left quadrant): a person in an orange sweater at a desk,
> reviewing a paper labeled "spec" and a green planning board with pinned
> sticky notes; a small "scout-brief.json" note and an "APPROVED · 12–25 min"
> rubber stamp on the desk.
>
> ② PROPOSERS (top-right quadrant): three full-size isometric desks in a row,
> each with a monitor showing a globe icon (web research). Desk colors and
> labels identify the default proposer roster: a GREEN desk labeled "Codex
> Terra", a VIOLET desk labeled "GLM 5.2", and an ORANGE desk labeled "Sonnet
> latest". A bold red ribbon reading "READ-ONLY" bands across all three desks,
> with a padlock icon. JSON pages drop out of each output tray.
>
> ③ BROADCAST REFINERS (bottom-left quadrant): two people at desks reviewing
> cork boards labeled "ALL VALID PROPOSALS" (both refiners receive the complete
> surviving proposal set). A GREEN desk labeled "Codex Sol" and a TEAL desk
> labeled "Qwen 3.8". Add a "VERIFIED" stamp on one desk.
>
> ④ AGGREGATOR (bottom-right quadrant): a pair of hands in orange sleeves
> drawing on a drafting table, assembling a blueprint and three outputs: a
> document labeled "final-plan.md", a small linked-node data card labeled
> "final-plan.json", and a browser card labeled "report.html" containing tiny
> bar-chart, timeline, and decision-lineage shapes. Add a small "Opus latest"
> label in the corner; Opus is the default, while the docs separately describe
> the optional recorded Codex Layer 3.
>
> The connecting arrows are colored ribbon-pipes (green, violet, and orange
> from the proposers; green and teal from the refiners). Along the very bottom,
> below the grid, a thin horizontal ruler/measuring line with the caption
> "~12–25 min wall-clock". Overall palette: OpenAI green, Zhipu violet,
> Anthropic orange, and Qwen teal on light gray. No photorealism, no
> heavy-3D render — keep the flat illustrated cel-shaded look.

Notes for whoever runs it:
- Image models render obscure logos unreliably — the desk-front **text labels**
  (`Codex Terra`, `GLM 5.2`, `Sonnet latest`, `Codex Sol`, `Qwen 3.8`) carry
  the identification; logo motifs are nice-to-have accents, not load-bearing.
- Keep Qwen in Layer 2. It is a default broadcast refiner, not the optional
  proposer shown by the v0.4.1 illustration.
- Keep the image focused on the default Opus route. The optional recorded
  Codex aggregation path belongs in the surrounding text and report UI rather
  than a second competing arrow in this overview.

## Prompt 2 — hero/banner (`docs/moa-x-header.png`)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (green, violet, orange, indigo) converging from the left
> edge into a single white line exiting right — symbolizing four model
> providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of
> Agents for coding plans". Flat, high contrast, no photorealism, no robots.
