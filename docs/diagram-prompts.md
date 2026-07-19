# Diagram & image prompts

The brand images are current and live:

- `docs/moa-x-header.png` — the README hero banner (generated from Prompt 2).
- `docs/moa-x-workflow.png` — the four-stage workflow illustration, embedded in
  the README and `docs/architecture.md` (generated from Prompt 1).

The banner reflects the four-lab default roster. The workflow image also shows
the optional Qwen Token Plan proposer and the generated HTML report. The prompts
below are kept as the source of truth for regenerating or tweaking them — keep
the same style so the pair stays visually consistent.
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
> sticky notes; a small "scout-brief.json" note and an "APPROVED · 6–12 min"
> rubber stamp on the desk.
>
> ② PROPOSERS (top-right quadrant): three full-size isometric desks in a row,
> each with a monitor showing a globe icon (web research). Desk colors and
> labels identify the default proposer roster: a GREEN desk labeled "codex", a
> VIOLET desk labeled "GLM", and an ORANGE desk labeled "sonnet". Beside them,
> place one smaller CYAN desk inside a dashed outline labeled "optional Qwen".
> The Qwen desk is visibly optional, not part of the default three. A bold red
> ribbon reading "READ-ONLY" bands across every proposer desk, with a padlock
> icon. JSON pages drop out of an output tray.
>
> ③ BROADCAST REFINERS (bottom-left quadrant): two people at desks reviewing
> cork boards labeled "ALL VALID PROPOSALS" (both refiners receive the complete
> surviving proposal set, whether the optional Qwen lane is enabled or not). A
> GREEN desk labeled "codex", and a DARK-INDIGO desk labeled "kimi". Add a
> "VERIFIED" stamp on one desk.
>
> ④ AGGREGATOR (bottom-right quadrant): a pair of hands in orange sleeves
> drawing on a drafting table, assembling a blueprint and two outputs: a
> document labeled "final-plan.md" and a browser card labeled "report.html"
> containing tiny bar-chart and timeline shapes. Small "Opus" label in the
> corner.
>
> The connecting arrows are colored ribbon-pipes (green, violet, orange, plus a
> dashed cyan optional pipe from the proposers; green and indigo from the
> refiners). Along the very bottom,
> below the grid, a thin horizontal ruler/measuring line with the caption
> "~6–12 min wall-clock". Overall palette: OpenAI green, Zhipu violet,
> Anthropic orange, Moonshot indigo, on light gray. No photorealism, no
> heavy-3D render — keep the flat illustrated cel-shaded look.

Notes for whoever runs it:
- The two former Google-blue desks (one proposer, one refiner) become **Zhipu
  GLM** (violet) and **Moonshot Kimi** (indigo). Keep OpenAI green and
  Anthropic orange as-is.
- Image models render obscure logos unreliably — the desk-front **text labels**
  (`codex`, `GLM`, `sonnet`, `optional Qwen`, `kimi`) carry the identification;
  logo motifs are nice-to-have accents, not load-bearing. The dashed outline
  and the word **optional** are required so Qwen is not mistaken for a default.

## Prompt 2 — hero/banner (`docs/moa-x-header.png`)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (green, violet, orange, indigo) converging from the left
> edge into a single white line exiting right — symbolizing four model
> providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of
> Agents for coding plans". Flat, high contrast, no photorealism, no robots.
