# Diagram & image prompts

The brand images are current and live:

- `docs/moa-x-header.png` — the README hero banner (generated from Prompt 2).
- `docs/moa-x-workflow.png` — the four-stage workflow illustration, embedded in
  the README and `docs/architecture.md` (generated from Prompt 1).

Both reflect the four-lab roster (codex + glm + sonnet proposers, codex + kimi
refiners). The prompts below are kept as the source of truth for regenerating
or tweaking them — keep the same style so the pair stays visually consistent.
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
> Aggregator). Each stage header is prefixed with its circled number.
>
> ① SCOUT (top-left quadrant): a person in an orange sweater at a desk,
> reviewing a paper labeled "spec" and a green planning board with pinned
> sticky notes; a small "scout-brief.json" note and an "APPROVED · 6–12 min"
> rubber stamp on the desk.
>
> ② PROPOSERS (top-right quadrant): three isometric desks in a row, each with a
> monitor showing a globe icon (web research). Desk colors and logos identify
> three labs: a GREEN desk with the OpenAI swirl labeled "codex", a VIOLET desk
> labeled "GLM" with a small stylized knowledge-graph / "Z" motif (Zhipu), and
> an ORANGE desk with the Anthropic sunburst labeled "sonnet". A bold red ribbon
> reading "READ-ONLY" bands across the desks, with a padlock icon. JSON pages
> drop out of an output tray.
>
> ③ BROADCAST REFINERS (bottom-left quadrant): two people at desks reviewing
> cork boards pinned with three "JSON" sheets each (they each see all
> proposals). A GREEN desk with the OpenAI swirl labeled "codex", and a
> DARK-INDIGO desk labeled "kimi" with a small crescent-moon logo (Moonshot). A
> "VERIFIED" stamp on a desk.
>
> ④ AGGREGATOR (bottom-right quadrant): a pair of hands in orange sleeves
> drawing on a drafting table, assembling a blueprint and a document labeled
> "final-plan.md". Small "Opus" + Anthropic wordmark in the corner.
>
> The connecting arrows are colored ribbon-pipes (green, violet, orange from
> the proposers; green and indigo from the refiners). Along the very bottom,
> below the grid, a thin horizontal ruler/measuring line with the caption
> "~6–12 min wall-clock". Overall palette: OpenAI green, Zhipu violet,
> Anthropic orange, Moonshot indigo, on light gray. No photorealism, no
> heavy-3D render — keep the flat illustrated cel-shaded look.

Notes for whoever runs it:
- The two former Google-blue desks (one proposer, one refiner) become **Zhipu
  GLM** (violet) and **Moonshot Kimi** (indigo). Keep OpenAI green and
  Anthropic orange as-is.
- Image models render obscure logos unreliably — the desk-front **text labels**
  (`codex`, `GLM`, `sonnet`, `kimi`) carry the identification; the crescent
  moon (Kimi) and knowledge-graph/Z (Zhipu) are nice-to-have accents, not
  load-bearing.

## Prompt 2 — hero/banner (`docs/moa-x-header.png`)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (green, violet, orange, indigo) converging from the left
> edge into a single white line exiting right — symbolizing four model
> providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of
> Agents for coding plans". Flat, high contrast, no photorealism, no robots.
