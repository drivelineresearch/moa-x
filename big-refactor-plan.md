# Big Refactor Plan: gemini out, opencode in, roster-first config

**Date:** 2026-07-04
**Branch context:** local `cursor-named-providers` is a strict prefix of PR #2 (mjfork stacked 9 commits on our exact commits). Offline suite: 33/33 green on this branch.
**Target version:** v0.3.0 (breaking: removes the `gemini` built-in provider and adapter).

---

## 0. Goals

1. Incorporate the three open community PRs (#2, #3, #4) without wasting mjfork's work.
2. **Remove gemini entirely** — adapter, built-in provider, docs, prompts, badges, diagram. It is the dominant flake source (empty-envelope responses, utility-model quota exhaustion, ARG_MAX exposure, no CLI-level read-only mode).
3. Add an **opencode adapter** as the harness for Chinese-lab models: **GLM-5.2** (Zhipu) and **Kimi K2.7 Code** (Moonshot), with Fireworks serverless as the API-key alternative behind the same adapter.
4. Make **Cursor + Composer 2.5** a first-class documented lane (it already works mechanically via PR #2's adapter).
5. Push the config surface so proposer/refiner **count and identity are pure config** (.env or YAML), no code edits.
6. New README/architecture **image prompts** reflecting the post-gemini roster.

The cross-lab story actually gets *stronger*: today's roster is 3 US labs. Post-refactor a default run touches OpenAI (codex), Anthropic (sonnet + Opus aggregator), Zhipu (GLM-5.2), and Moonshot (Kimi K2.7) — four labs, two countries, and the refiner layer stays fully independent of the Anthropic aggregator.

---

## 1. Research findings that drive the design

### opencode CLI (the chosen harness for GLM/Kimi)

- Headless: `opencode run -m <provider/model> --dir <repo> -q [--format json] "<message>"`. Project moved to `anomalyco/opencode`; install `curl -fsSL https://opencode.ai/install | bash` or `npm i -g opencode-ai` (binary is `opencode`).
- **No stdin support** (feature request closed as not-planned). Positional args hit Linux's per-arg limit (`MAX_ARG_STRLEN` = 128KB) — refiner prompts can exceed that. **The adapter must write the prompt to a file in the session dir and attach it with `-f`**, with a short positional message ("Follow the instructions in the attached file"). The session dir lives under the target repo's `.moa/`, so no `external_directory` permission issue.
- Model IDs (all resolvable via `opencode models <provider>` as a preflight):
  - Zhipu direct: `zhipuai/glm-5.2` (env `ZHIPU_API_KEY`)
  - Z.ai Coding Plan subscription ($18–160/mo): `zhipuai-coding-plan/glm-5.2`
  - Moonshot direct: `moonshotai/kimi-k2.7-code` (env `MOONSHOT_API_KEY`)
  - Fireworks serverless: `fireworks-ai/accounts/fireworks/models/glm-5p2` and `.../kimi-k2p7-code` (env `FIREWORKS_API_KEY`)
  - OpenCode Go subscription ($10/mo): `opencode-go/glm-5.2`, `opencode-go/kimi-k2.7-code`
- Read-only enforcement is real: `permission: {edit: "deny", bash: {"*": "deny"}, webfetch: ...}` in opencode config; explicit `deny` survives `--auto`. Headless runs need `--auto` so `ask` permissions don't hang.
- `--format json` emits **a stream of JSON events, not one envelope** — schema is under-documented. Safest v1: default text format + Python-side JSON extraction (we already have battle-tested extraction logic in `gemini.py::_extract_inner_json` — hoist it before deleting the file).
- Auth preflight: `opencode auth list` + `opencode models <provider> | grep <model>`; env-var keys need no login step.

### Fireworks (API-direct option)

- OpenAI-compatible: `https://api.fireworks.ai/inference/v1/chat/completions`, `FIREWORKS_API_KEY`.
- GLM-5.2 = `accounts/fireworks/models/glm-5p2`: $1.40/$4.40 per M in/out ($0.14 cached), 1.04M context. `glm-5p2-fast` router variant ~2-3x faster at $2.10/$6.60.
- Kimi K2.7 Code = `accounts/fireworks/models/kimi-k2p7-code`: $0.95/$4.00 per M, 256K context.
- **Decision: do not write a raw-API adapter.** Fireworks is reachable *through* opencode's built-in `fireworks-ai` provider, which preserves the repo's CLI-not-SDK principle (docs/architecture.md "Why CLI, not SDK") and gets agentic repo reading for free. A bare chat-completions call can't grep the repo — it would produce ungrounded plans. Fireworks becomes a **provider config choice inside the opencode harness**, not a separate harness. (If a raw-API lane is ever wanted, that's its own design discussion — the paper-faithful "proposers read the repo" property is what's at stake.)
- Both Z.ai and Moonshot also ship **Anthropic-compatible endpoints** (`https://api.z.ai/api/anthropic`, `https://api.moonshot.ai/anthropic`). That means the existing **claude adapter** can drive GLM/Kimi via `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` with zero new code — worth documenting as a fallback lane, but opencode is the primary (cleaner env isolation, no risk of contaminating the parent Claude Code session's auth).

### Cursor CLI / Composer 2.5

- Binary renamed `cursor-agent` → **`agent`** (the `cursor` binary is the IDE launcher). PR #2's adapter and docs use `cursor-agent`; `MOA_CURSOR_BIN` already lets users point at either, but the default and docs need verification against a current install.
- Composer 2.5 (May 2026, in-house, reportedly a Kimi K2.5 fine-tune — unconfirmed by Cursor): slug `composer-2.5`. Catalog also carries `gpt-5.5`, `claude-4.7-opus`, `grok-4.3`, and **Cursor-routed** `Kimi K2.7 Code` / `GLM 5.2` (exact slugs unverified — check `agent models`).
- No BYO-key path in the CLI: GLM/Kimi via Cursor run on Cursor's routing and Cursor's billing. Fine as a convenience lane, not a substitute for opencode.
- Hard read-only exists: sandbox `workspace_readonly` + `permissions.deny: ["Write(**)"]`; note the open bug where headless `-p` ignored `sandbox.json` (forum thread 157095) — keep PR #2's `--mode plan` as the primary guarantee and layer permissions on top.

---

## 2. Phase A — Land the open PRs (do this first, before any refactor)

Order matters; all three predate the gemini decision.

| PR | Verdict | Action |
|----|---------|--------|
| **#2 Named providers + Cursor adapter** | Merge. Our local branch is commit-for-commit its prefix; mjfork's 9 extra commits are all keepers: `--mode plan` CLI-level read-only, `whoami` auth probe, stdin prompt (ARG_MAX fix), per-provider `timeout` on `ResolvedProvider` + `MOA_<NAME>_TIMEOUT`, refiner-schema pattern fix for 5 leftover proposer-id enums, config-aware `install_deps.py`, `docs/cursor.md`. Status is MERGEABLE, no longer draft. | Review-approve, merge, delete local `cursor-named-providers` (fully contained). |
| **#3 Gemini stdin fix** | Merge even though gemini is being removed. It's 9 lines off `main`, correct, and keeps gemini healthy for every user between now and v0.3.0. Costs nothing — the file gets deleted later anyway, and it credits the contributor. | Merge after #2. |
| **#4 Redispatch on transient-empty** | Keep the machinery, coordinate the rebase. `--phase layer1/layer2` + `--redispatch` + `transient_empty` detection is exactly the right answer to the cursor flake mode, and it stays valuable after gemini is gone (cursor exhibits the same empty-envelope pattern). The gemini half of the diff becomes dead on removal. | Comment on the PR: gemini removal is coming in v0.3.0; ask mjfork to rebase onto main post-#2 and we'll merge, gemini parts and all — the removal PR then deletes the gemini bits mechanically. Don't make the contributor pre-clean for a refactor that hasn't landed. |

Also: fix the stale "23/23" test count in CLAUDE.md while touching it (currently 33/33; will change again in every phase below — make the doc say "run `test_offline.py`; all tests must pass" instead of hardcoding a number).

---

## 3. Phase B — opencode adapter (additive, no removals yet)

New file `harness/scripts/adapters/opencode.py`, modeled on `cursor.py` (no native schema enforcement → Python-side validation):

- **Invocation:**
  ```
  opencode run -m <model> --dir <repo> -q --auto -f <session_dir>/opencode-prompt-<agent_id>.md \
      "Read the attached file and follow its instructions exactly."
  ```
  Prompt written to the session dir (inside the target repo's `.moa/`), so no ARG_MAX and no external-directory permission friction. Default text output; payload extracted with the shared JSON-extraction helper (see below).
- **Read-only:** two layers. (1) Prompt-level `READ_ONLY_RULE` (existing shared constant). (2) A `permission` block denying `edit` and `bash` writes. Open implementation question: cleanest injection is a temp global config via `OPENCODE_CONFIG` env var if supported, else a documented `~/.config/opencode/opencode.json` snippet + preflight warning. Verify against a real install during implementation; don't guess.
- **Preflight `check_available()`:** `shutil.which("opencode")` → `opencode auth list` (non-empty) → optionally `opencode models <provider-prefix>` grep for the configured model. Honors `MOA_OPENCODE_BIN`.
- **Hoist before delete:** move `gemini.py::_extract_inner_json` + `_diagnose_empty_response` (genericized) into `adapters/__init__.py`; have `cursor.py` and `opencode.py` share them. This consolidates the three copies of fence-stripping/brace-matching logic into one.
- **Orchestrator wiring:** add `"opencode"` to `_dispatch_provider`, preflight loop in `main()`, `timeout_for_harness` (default 1200s), and `_DEFAULT_BINS`. All mechanical — the harness-keyed dispatch from PR #2 was built for exactly this.
- **New built-in providers** in `config.py::BUILTIN_PROVIDERS`:
  ```python
  "glm":  ResolvedProvider(name="glm",  harness="opencode", model="zhipuai/glm-5.2"),
  "kimi": ResolvedProvider(name="kimi", harness="opencode", model="moonshotai/kimi-k2.7-code"),
  ```
  Users on Z.ai coding plan / Fireworks / OpenCode Go just override the model string:
  `MOA_GLM_MODEL=fireworks-ai/accounts/fireworks/models/glm-5p2` etc.
- **`install_deps.py`:** opencode section in the config-aware preflight (PR #2's rewrite makes this a table entry, not new plumbing).
- **Tests (offline):** envelope-extraction fixtures for opencode text output, provider resolution for `glm`/`kimi`, dispatch routing, preflight-missing-binary path. Mirror the cursor test block from PR #2.

Deliverable: a run with `layers: {proposers: [codex, glm, sonnet], refiners: [codex, kimi]}` works end-to-end while gemini still exists. Additive PR, easy review.

---

## 4. Phase C — rip out gemini (the breaking PR)

Footprint measured: 27 files, ~325 references.

**Delete:**
- `harness/scripts/adapters/gemini.py` (460 lines) — after the extraction-helper hoist in Phase B.

**Code surgery:**
- `run_moa.py` (39 refs): drop the gemini import, `_run_gemini`, the `h == "gemini"` dispatch branch, `--gemini-model` / `--gemini-timeout` flags, gemini preflight branch, `timeout_for_harness["gemini"]`, gemini mentions in module docstring and log strings. Bump `architecture_version` → `"v3-named-roster"`.
- `config.py` (10): remove `gemini` from `BUILTIN_PROVIDERS` and `_DEFAULT_BINS`; **new defaults** `_DEFAULT_PROPOSERS = ["codex", "glm", "sonnet"]`, `_DEFAULT_REFINERS = ["codex", "kimi"]`. Unknown-provider `ValueError` already prints valid names — add a migration hint when the unknown name is exactly `"gemini"` ("gemini was removed in v0.3.0; define it as a user provider with harness cursor, or see docs/config.md#migrating-from-gemini").
- `install_deps.py` (12): drop the gemini preflight entry.
- `test_offline.py` (42): delete gemini adapter tests; update config-resolution and dispatch tests for the new defaults; add a test asserting `gemini` now raises with the migration hint.
- Schemas: proposer/refiner descriptions mentioning gemini (enums are already gone via PR #2's pattern relaxation).
- Prompts (`proposer.md` 1, `refiner.md` 4, `scout.md` 3, `aggregator.md` 8): reword the roster examples to be roster-neutral ("the configured proposers") rather than hardcoding names — this is the last place fixed names hide.
- `adapters/__init__.py`, `claude.py`, `cursor.py`: comment mentions.

**Config/docs surgery:**
- `.env.example` (8): drop `MOA_GEMINI_*`; add `MOA_OPENCODE_BIN`, `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `FIREWORKS_API_KEY` notes, and the new roster examples.
- `harness/config.example.yaml` (10): new built-ins table (codex/sonnet/glm/kimi), new example mixes (see §6).
- `README.md` (9), `CLAUDE.md` (6), `CONTRIBUTING.md` (2), `SECURITY.md` (2), `requirements-cli.txt` (3), `docs/install.md` (3), `docs/usage.md` (4), `docs/config.md` (16), `docs/architecture.md` (10), `harness/SKILL.md` (25), `harness/README.md` (28): replace the codex+gemini+sonnet trio narrative with the named-roster narrative. `docs/architecture.md` "Why these three" becomes "Why this roster" and the cross-lab argument gets the four-lab upgrade. Skill description in `SKILL.md` frontmatter must change (it currently names gemini/3-flash-preview).
- One honest paragraph in `docs/architecture.md` on **why gemini left**: empty-envelope flake mode, utility-model quota exhaustion breaking JSON output, no CLI-level read-only mode, ARG_MAX-hostile prompt passing. Future contributors deserve the reasoning, and it preempts "add gemini back" PRs that don't address the flakes.

**Explicitly not kept:** no `gemini` back-compat alias, no tombstone provider. Kyle's call: rip it out entirely. Anyone who wants Gemini models can still route them through cursor (`gemini-3.1-pro` is in Cursor's catalog) as a user-named provider — document that one-liner in the migration note.

---

## 5. Phase D — Cursor / Composer 2.5 as a documented first-class lane

Mechanically PR #2 already did the work. Remaining:

- **Verify the binary rename** (`agent` vs `cursor-agent`) on a current install; update `_DEFAULT_BINS`/`_cursor_bin()` default and `docs/cursor.md` accordingly (probe both names in `check_available()` — try `cursor-agent`, fall back to `agent`).
- Add `composer` as a **built-in provider**: `ResolvedProvider(name="composer", harness="cursor", model="composer-2.5")`. Cheap ($0.50/$2.50 per M standard), fast, and a genuinely different point in model space.
- Document the caveat that Composer 2.5 is reportedly a Kimi-K2.5 derivative — if true, `composer` as refiner + `kimi` as proposer is *not* two independent labs. Note it in the lab-independence section and move on; it's a recommendation, not an invariant.
- Verify Cursor-routed `GLM 5.2` / `Kimi K2.7 Code` slugs via `agent models` and list them in `docs/cursor.md` as an alternative to opencode for people who already pay for Cursor.
- Sandbox: document `workspace_readonly` + `permissions.deny: ["Write(**)"]` as belt-and-suspenders on top of `--mode plan` (and the known headless sandbox bug).

---

## 6. Phase E — roster-first config (.env parity)

Named providers (PR #2) already give N-of-anything rosters via YAML + `MOA_PROPOSERS`/`MOA_REFINERS`. Two gaps to close:

1. **Provider definitions in `.env`.** Today user-named providers require `harness/config.yaml`. Add:
   ```
   MOA_PROVIDER_<NAME>=<harness>:<model>
   # e.g.
   MOA_PROVIDER_GLM_FW=opencode:fireworks-ai/accounts/fireworks/models/glm-5p2
   MOA_PROVIDER_COMPOSER=cursor:composer-2.5
   ```
   Parse in `config.py` (split on first `:`; name lowercased, `_`→`-`), merged into `user_providers` with YAML winning on conflict (consistent with existing precedence). Then a complete roster swap is a pure `.env` edit:
   ```
   MOA_PROPOSERS=codex,glm,sonnet,composer
   MOA_REFINERS=codex,kimi
   ```
2. **Scaling knobs already work** — count is implicit in the list length; `ThreadPoolExecutor(max_workers=len(runnable))` scales; `write_synthesis_input` and the refiner schema are name-neutral post-PR #2. Verify the refiner schema's `reviewing.maxItems: 3` — bump to something generous (8) so a 4-5 proposer roster doesn't fail validation. (Check proposer count assumptions anywhere else: grep for `maxItems` and "three" in schemas/prompts.)

Documented example rosters for `docs/config.md`:

```yaml
# Default (4 labs): OpenAI + Zhipu + Anthropic propose; OpenAI + Moonshot refine
layers:
  proposers: [codex, glm, sonnet]
  refiners:  [codex, kimi]

# Budget: everything through opencode subscriptions
providers:
  oc-glm:  {harness: opencode, model: opencode-go/glm-5.2}
  oc-kimi: {harness: opencode, model: opencode-go/kimi-k2.7-code}
layers:
  proposers: [oc-glm, oc-kimi, sonnet]
  refiners:  [oc-glm, oc-kimi]

# Five-wide with Cursor lanes
providers:
  composer: {harness: cursor, model: composer-2.5}
  c-grok:   {harness: cursor, model: grok-4.3}
layers:
  proposers: [codex, glm, sonnet, composer, c-grok]
  refiners:  [codex, kimi, composer]
```

---

## 7. Phase F — README refresh + new image prompts

`README.md` badges, TL;DR install block (gemini CLI line → opencode line), architecture snippet, and `docs/moa-architecture.png` all encode the old trio. New diagram + optional social/hero image, generated from these prompts (Driveline-neutral, works with any decent image model):

**Prompt 1 — architecture diagram (replaces `docs/moa-architecture.png`):**
> Clean horizontal technical architecture diagram on a white background, flat design, thin dark-gray connector arrows, rounded rectangles, sans-serif labels. Four columns left to right. Column 1: single box "Layer 0 — Scout (parent Claude Code session)" with a small magnifying-glass icon. Column 2 titled "Layer 1 — Proposers (parallel, read-only)": three stacked boxes labeled "codex · GPT-5.4 (OpenAI)", "opencode · GLM-5.2 (Zhipu)", "sonnet · Claude Sonnet (Anthropic)", each with a tiny terminal icon. Column 3 titled "Layer 2 — Broadcast refiners": two boxes labeled "codex · GPT-5.4" and "opencode · Kimi K2.7 (Moonshot)", with thin arrows from ALL three proposer boxes fanning into EACH refiner box. Column 4: single box "Layer 3 — Aggregator (Claude Opus, in-session)" with an arrow out to a document icon labeled "final-plan.md". Footer caption: "4 labs · broadcast refinement · 6–12 min wall-clock". Accent color: one restrained blue for layer headers; no gradients, no shadows, no 3D.

**Prompt 2 — hero/banner (optional, top of README):**
> Minimal wide banner (3:1), dark charcoal background. Center: the text "MoA-X" in a bold geometric sans, with a subtle circuit-like motif of four thin colored lines (blue, red, teal, amber) converging from the left edge into a single white line exiting right — symbolizing four model providers merging into one plan. Small subtitle text: "Cross-Lab Mixture of Agents for coding plans". Flat, high contrast, no photorealism, no robots, no brains.

**Prompt 3 — "how a run flows" strip (optional, for docs/usage.md):**
> Four-panel horizontal storyboard, flat pastel illustration style, consistent stroke weight. Panel 1: a terminal window with a prompt line and the caption "You write a spec". Panel 2: three small robot terminals reading the same stack of documents in parallel, caption "3 proposers read your repo". Panel 3: two magnifier-wielding robot terminals inspecting all three proposals laid on a table, caption "2 refiners cross-check every plan". Panel 4: one larger terminal assembling pages into a single bound document, caption "Opus writes the final plan". No text other than captions; generous whitespace.

Badge line update: `providers-codex | claude--code | opencode | cursor`.

---

## 8. Sequencing, testing, risk

**PR sequence (each its own branch off main, per repo workflow):**

1. **A**: merge #2 → merge #3 → coordinate #4 rebase (merge when green).
2. **B**: `feat(adapters): opencode harness + glm/kimi built-ins` — additive, offline tests extended.
3. **C**: `refactor!: remove gemini adapter and built-in provider` — the breaking one; includes migration notes; tag v0.3.0 after merge.
4. **D+E**: `feat(config): .env provider definitions; cursor composer built-in; binary rename` — can fold into B/C reviews if small.
5. **F**: `docs: post-gemini README, architecture doc, new diagram` — needs the generated image committed.

**Verification gates per phase:**
- `python3 harness/scripts/test_offline.py` green (count will move; CLAUDE.md stops hardcoding it).
- `python3 harness/scripts/install_deps.py` green with opencode installed and authed.
- One real end-to-end `/mixture-of-agents` run on a non-trivial spec after B and after C, checking the manifest for all-agent success and the synthesis input for correct roster labels.
- CI stays offline/credential-free (hard rule).

**Risks / open questions:**
- opencode `--format json` event schema unverified → v1 uses text + shared extractor; revisit once verified locally.
- opencode read-only config injection (`OPENCODE_CONFIG` env?) needs a live check; fall back to documented global config + prompt rule.
- `zhipuai-coding-plan` provider id and Fireworks model presence in opencode's models.dev catalog: verify with `opencode models` before hardcoding defaults; the `MOA_GLM_MODEL` override is the escape hatch either way.
- Cursor binary rename could break PR #2's fresh `whoami` preflight on new installs — probe both names.
- GLM-5.2/K2.7 latency under subscription plans is uncharacterized; keep 1200s timeouts and note that per-provider `timeout:` (PR #2) is the tuning knob.
- Composer 2.5's rumored Kimi lineage muddies lab-independence bookkeeping — document, don't enforce.

**Effort ballpark:** A: half a day (mostly review + a live cursor run for #2's unchecked box). B: 1–1.5 days including a real GLM/Kimi end-to-end. C: 1 day, mostly docs. D+E: half a day. F: an hour plus image generation. Roughly 3–4 focused days total.
