# Diagram & image prompts

The brand images are current and live:

- `docs/moa-x-header.png` — the README hero banner (generated from Prompt 2).
- `docs/moa-x-workflow.png` — the current four-stage workflow illustration
  (generated from Prompt 1), embedded in the README.

The banner reflects the cross-lab ensemble theme. The next workflow image should
show the current Balanced proposers (Gemini Pro, Grok 4.5, and GPT-5.6 Luna),
current broadcast refiners (Qwen 3.8, Kimi K3, and Claude Opus 5), GPT-5.6 Sol
`xhigh` default aggregation, the warning-gated optional Fable aggregator, and the
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
> ② PROPOSERS (top-right quadrant): three full-size isometric desks in a row
> plus one smaller dashed Thorough-only lane,
> each with a monitor showing a globe icon (web research). Desk colors and
> labels identify the default proposer roster: a BLUE desk labeled "Gemini
> Pro", a VIOLET desk labeled "Grok 4.5", a GREEN desk labeled "GPT-5.6
> Luna", and a smaller TEAL Thorough-only desk labeled "GPT-5.6 Terra".
> A bold red ribbon reading "READ-ONLY" bands across all four desks,
> with a padlock icon. JSON pages drop out of each output tray.
>
> ③ BROADCAST REFINERS (bottom-left quadrant): three people at desks reviewing
> cork boards labeled "ALL VALID PROPOSALS" (every refiner receives the complete
> surviving proposal set). A TEAL desk labeled "Qwen 3.8", a VIOLET desk
> labeled "Kimi K3", and an ORANGE desk labeled "Opus 5". Add a "VERIFIED"
> stamp on one desk.
>
> ④ AGGREGATOR (bottom-right quadrant): a pair of hands in orange sleeves
> drawing on a drafting table, assembling a blueprint and three outputs: a
> document labeled "final-plan.md", a small linked-node data card labeled
> "final-plan.json", and a browser card labeled "report.html" containing tiny
> bar-chart, timeline, and decision-lineage shapes. Add a primary "GPT-5.6 Sol
> · xhigh · DEFAULT" label plus two smaller alternate cards: "Claude Opus 5"
> and "Fable 5 1M · xhigh", with a warning triangle, lock, and "HIGH QUOTA"
> badge on Fable.
>
> The connecting arrows are colored ribbon-pipes (blue, black, and purple
> from the proposers; teal, violet, and orange from the refiners). Along the very bottom,
> below the grid, a thin horizontal ruler/measuring line with the caption
> "~12–25 min wall-clock". Overall palette: Google blue, OpenAI green,
> Anthropic orange, Qwen teal, Kimi blue, xAI violet, and OpenAI green on light gray. No photorealism, no
> heavy-3D render — keep the flat illustrated cel-shaded look.

Notes for whoever runs it:
- Image models render obscure logos unreliably — the desk-front **text labels**
  (`Gemini Pro`, `Grok 4.5`, `GPT-5.6 Luna`, `GPT-5.6 Terra`, `Qwen 3.8`, `Kimi K3`, `Opus 5`,
  `GPT-5.6 Sol`, `Fable 5 1M`) carry
  the identification; logo motifs are nice-to-have accents, not load-bearing.
- Keep Qwen and Kimi in Layer 2. Keep Grok in Layer 1. Do not repeat the same
  route or model in multiple stages.
- Keep the image focused on the default recorded GPT-5.6 Sol route.

## Prompt 2 — hero/banner (`docs/moa-x-header.png`)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (green, violet, orange, indigo) converging from the left
> edge into a single white line exiting right — symbolizing four model
> providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of
> Agents for coding plans". Flat, high contrast, no photorealism, no robots.
