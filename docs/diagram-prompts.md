# Diagram & image prompts

`docs/moa-architecture.png` (embedded in the README and `docs/architecture.md`)
still shows the pre-v0.3.0 roster (codex + gemini + sonnet). **It needs
regenerating** for the four-lab roster (codex + glm + sonnet proposers,
codex + kimi refiners). Until then the alt text is accurate and the image is
the only stale artifact.

The existing image is a **detailed isometric illustration** — hand-drawn
vector look, bold black outlines, flat color fills with light cel-shading, on
a light-gray ground — NOT a flat boxes-and-arrows diagram. Prompt 1 recreates
that exact style with the new roster. Generate at ~1440px square and overwrite
`docs/moa-architecture.png` (keep the filename so the embeds keep working).

## Prompt 1 — architecture illustration (replaces `docs/moa-architecture.png`)

> Isometric technical illustration, hand-drawn vector style: bold black
> outlines, flat color fills with subtle cel-shading, light warm-gray
> background. Clean sans-serif labels. It depicts a four-stage "mixture of
> agents" pipeline, laid out as four labeled vignettes connected by thick
> colored ribbon-pipes that flow between them.
>
> **(1) SCOUT** (top-left): a person in an orange sweater sitting at a desk,
> reviewing a paper labeled "spec" and a green planning board with pinned
> sticky notes; a small "scout-brief.json" note and an "APPROVED · 6–12 min"
> rubber stamp on the desk.
>
> **(2) PROPOSERS** (lower-left): three isometric desks in a row, each with a
> monitor showing a globe icon (web research). Desk colors and logos identify
> three labs: a GREEN desk with the OpenAI swirl labeled "codex", a VIOLET
> desk labeled "GLM" with a small stylized knowledge-graph / "Z" motif (Zhipu),
> and an ORANGE desk with the Anthropic sunburst labeled "sonnet". A bold red
> ribbon reading "READ-ONLY" bands across the desks, with a padlock icon. JSON
> pages drop out of an output tray.
>
> **(3) BROADCAST REFINERS** (top-right): two people at desks reviewing cork
> boards pinned with three "JSON" sheets each (they each see all proposals). A
> GREEN desk with the OpenAI swirl labeled "codex", and a DARK-INDIGO desk
> labeled "kimi" with a small crescent-moon logo (Moonshot). A "VERIFIED"
> stamp on a desk.
>
> **(4) AGGREGATOR** (bottom-right): a pair of hands in orange sleeves drawing
> on a drafting table, assembling a blueprint and a document labeled
> "final-plan.md". Small "Opus" + Anthropic wordmark in the corner.
>
> Colored ribbon-pipes (green, violet, orange from the proposers; green and
> indigo from the refiners) flow from stage to stage and converge at the
> aggregator. Along the very bottom, a horizontal ruler/measuring line with the
> caption "~6–12 min wall-clock". Overall palette: OpenAI green, Zhipu violet,
> Anthropic orange, Moonshot indigo, on light gray. No photorealism, no
> gradients-heavy 3D render — keep the flat illustrated cel-shaded look.

Notes for whoever runs it:
- The two former Google-blue desks (one proposer, one refiner) become **Zhipu
  GLM** (violet) and **Moonshot Kimi** (indigo). Keep OpenAI green and
  Anthropic orange as-is.
- Image models render obscure logos unreliably — the desk-front **text labels**
  (`codex`, `GLM`, `sonnet`, `kimi`) carry the identification; the crescent
  moon (Kimi) and knowledge-graph/Z (Zhipu) are nice-to-have accents, not
  load-bearing.

## Prompt 2 — hero/banner (optional, top of README)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (green, violet, orange, indigo) converging from the left
> edge into a single white line exiting right — symbolizing four model
> providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of
> Agents for coding plans". Flat, high contrast, no photorealism, no robots.
