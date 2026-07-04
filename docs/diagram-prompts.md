# Diagram & image prompts

`docs/moa-architecture.png` (embedded in the README and `docs/architecture.md`)
still shows the pre-v0.3.0 roster (codex + gemini + sonnet). **It needs
regenerating** for the four-lab roster. Until then, the alt text is accurate
and the image is the only stale artifact.

Generate a replacement with an image model using Prompt 1 below, export at
~1440px wide, and overwrite `docs/moa-architecture.png` (keep the filename so
existing embeds keep working).

## Prompt 1 — architecture diagram (replaces `docs/moa-architecture.png`)

> Clean horizontal technical architecture diagram on a white background, flat
> design, thin dark-gray connector arrows, rounded rectangles, sans-serif
> labels. Four columns left to right. Column 1: single box "Layer 0 — Scout
> (parent Claude Code session)" with a small magnifying-glass icon. Column 2
> titled "Layer 1 — Proposers (parallel, read-only)": three stacked boxes
> labeled "codex · GPT-5.4 (OpenAI)", "glm · GLM-5.2 (Zhipu, via opencode)",
> "sonnet · Claude Sonnet (Anthropic)", each with a tiny terminal icon. Column
> 3 titled "Layer 2 — Broadcast refiners": two boxes labeled "codex · GPT-5.4"
> and "kimi · Kimi K2.7 (Moonshot, via opencode)", with thin arrows from ALL
> three proposer boxes fanning into EACH refiner box. Column 4: single box
> "Layer 3 — Aggregator (Claude Opus, in-session)" with an arrow out to a
> document icon labeled "final-plan.md". Footer caption: "4 labs · broadcast
> refinement · 6–12 min wall-clock". Accent color: one restrained blue for
> layer headers; no gradients, no shadows, no 3D.

## Prompt 2 — hero/banner (optional, top of README)

> Minimal wide banner (3:1), dark charcoal background. Center: the text
> "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four
> thin colored lines (blue, red, teal, amber) converging from the left edge
> into a single white line exiting right — symbolizing four model providers
> merging into one plan. Small subtitle text: "Cross-Lab Mixture of Agents for
> coding plans". Flat, high contrast, no photorealism, no robots, no brains.

## Prompt 3 — "how a run flows" strip (optional, for docs/usage.md)

> Four-panel horizontal storyboard, flat pastel illustration style, consistent
> stroke weight. Panel 1: a terminal window with a prompt line and the caption
> "You write a spec". Panel 2: three small robot terminals reading the same
> stack of documents in parallel, caption "Proposers read your repo". Panel 3:
> two magnifier-wielding robot terminals inspecting all three proposals laid on
> a table, caption "Refiners cross-check every plan". Panel 4: one larger
> terminal assembling pages into a single bound document, caption "Opus writes
> the final plan". No text other than captions; generous whitespace.
