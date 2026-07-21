# Cursor CLI integration

This page documents how moa-x integrates with the Cursor CLI, what
guarantees the integration provides, and how to configure it. For
named-provider config in general see [`docs/config.md`](./config.md);
for the architectural rationale see
[`docs/architecture.md`](./architecture.md).

Cursor's CLI binary was historically named `cursor-agent`; newer
releases rename it to `agent`. The adapter probes `cursor-agent` first,
then `agent`, honoring `MOA_CURSOR_BIN` if set. Command examples below
use `cursor-agent`; substitute `agent` on a newer install.

## Why Cursor

Cursor is a multi-lab CLI: a single binary routes to OpenAI, Anthropic,
Google, xAI, or Moonshot models — plus Cursor's own in-house
`composer` models — via the `--model` flag. That makes it useful for
adding an ensemble lane, or for users who want to consolidate billing
around one subscription. moa-x ships `composer` (composer-2.5) as a
built-in provider on the cursor harness.

This is also why named providers exist: the legacy `{codex, sonnet, …}`
strings packed CLI + lab + model into one token, which Cursor's
one-CLI-many-labs shape broke. See `docs/architecture.md` for the full
design discussion.

## Filesystem guarantees

Read-only discipline is layered. The primary guarantee is
`cursor-agent --mode plan`, enforced at the Cursor CLI layer:

> `--mode plan` — read-only/planning (analyze, propose plans, no edits)

Verified: prompts that ask the model to write a file return *"plan mode
is active and I lack permission to run write/edit tools"* and produce
no file. `--mode plan` is a CLI-level guarantee, not a prompt hint.

**The flag is feature-detected per cursor-agent build.** It was present
historically, briefly removed in some 2025.10 builds, and restored in
current releases. The adapter probes `cursor-agent --help` once (cached)
and then:

- **When `--mode plan` is supported** (current builds): the adapter uses
  it **and still prepends** the shared `READ_ONLY_RULE` to the prompt as
  defense-in-depth. Two independent layers — a hard CLI guarantee plus
  the soft prompt rule.
- **When it is absent** (rare old builds): the adapter falls back to the
  `READ_ONLY_RULE` prompt directive *alone* and prints a loud stderr
  warning that read-only is now soft — bare `-p --trust` otherwise has
  full write/shell access. Fail-safe: any probe error is treated as
  "unsupported," so we never assume a hard guarantee we don't have.

So unlike the codex/claude/opencode adapters (prompt rule only — those
CLIs have no equivalent flag), the cursor adapter carries *both*
enforcement paths and prefers the CLI one. An earlier revision dropped
the prompt rule whenever plan mode was active; that traded away the
fallback layer and was reverted after review.

Belt-and-suspenders: you can also pin the workspace read-only at the
sandbox layer — a `sandbox.json` with `workspace_readonly` plus
`permissions.deny: ["Write(**)"]`. Note the known bug where headless
`-p` runs could ignore `sandbox.json` (cursor forum thread 157095); that
unreliability is why `--mode plan` (plus the prompt rule) is the primary
guarantee and the sandbox settings are a secondary layer.

If a future Cursor release regresses plan-mode enforcement, the adapter
degrades to the prompt rule (with the warning above), and the
orchestrator's session is still bounded by `cwd=repo_path` (set on the
subprocess), so any rogue write lands inside the user's repo where
`.gitignore` and review surface it. Defense in depth lives at the CLI,
prompt, git, and sandbox layers.

## Authentication

Two auth modes, either is acceptable:

| Mode          | Setup                                | Detection                    |
| ------------- | ------------------------------------ | ---------------------------- |
| Subscription  | `cursor-agent login`                 | `~/.cursor/` directory exists |
| API-billed    | `export CURSOR_API_KEY=sk-...`       | `CURSOR_API_KEY` env var set  |

`check_available()` runs `cursor-agent whoami` as the auth probe.
Exits 0 when authenticated (prints `✓ Logged in as <email>`); exits
non-zero with a helpful message when not. This catches stale tokens
or expired sessions during preflight, before the orchestrator wastes
wall-clock launching a real call that will fail.

Set `MOA_CURSOR_BIN` if the binary lives somewhere unusual or is named
something other than `cursor-agent` / `agent`; the default search
probes `cursor-agent` first, then `agent`, on the system PATH.

## Command line shape

The adapter invokes (with the prompt on **stdin**, not as an argv entry):

```bash
printf '%s' "$READ_ONLY_RULE

$prompt" | cursor-agent -p \
  --model <model> \
  --mode plan \          # only when the build supports it (feature-detected)
  --output-format json \
  --trust
```

| Flag                  | Purpose                                                  |
| --------------------- | -------------------------------------------------------- |
| `-p`                  | Print/non-interactive mode (required for headless)        |
| `--mode plan`         | Read-only enforcement, added only when supported (see *Filesystem guarantees*) |
| `--output-format json`| Structured envelope for the orchestrator's parser          |
| `--trust`             | Bypass workspace-trust prompt (works only with `-p`)      |

The prompt (with `READ_ONLY_RULE` prepended) is written to stdin rather
than passed positionally: refiner prompts bundle the scout brief plus
every proposer's full output and can exceed `ARG_MAX`. cursor-agent
reads stdin when no positional prompt is given.

We deliberately do **not** use `--force` / `--yolo`. Those flags tell
Cursor to auto-approve commands the model invokes; with plan mode there
is nothing to approve, so the flags are noise. `--trust` is the
documented headless way to bypass first-run workspace-trust.

## Output envelope

Cursor's `--output-format json` produces an envelope structurally
identical to claude-cli's outer envelope without `--json-schema` set:

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 8394,
  "result": "<MODEL TEXT>",
  "session_id": "...",
  "request_id": "...",
  "usage": {
    "inputTokens": 100,
    "outputTokens": 500,
    "cacheReadTokens": 13697,
    "cacheWriteTokens": 1692
  }
}
```

Cursor has no `--output-schema` equivalent (codex's hard schema
enforcement). The adapter validates the inner JSON against the
proposer/refiner schema Python-side, mirroring the opencode adapter's
strategy. The parser handles both bare JSON and ` ```json ` fenced JSON
inside the `result` text, longest-match-first.

The `usage` block exposes input/output/cache token counts. moa-x does
not act on these today; they will feed the future `MOA_MAX_COST` work.

## Model identifiers

Cursor's CLI uses **machine ids**, which differ from the friendly names
on https://cursor.com/docs/models. Run `cursor-agent --list-models` to
see what your account can use.

Examples (from a current install):

| Friendly name        | CLI machine id              |
| -------------------- | --------------------------- |
| GPT-5.5              | `gpt-5.5-medium` (also `-high`, `-extra-high`) |
| Claude 4.7 Opus      | `claude-opus-4-7-medium` (or `-thinking-high`, etc.) |
| Claude 4.5 Sonnet    | `claude-4.5-sonnet`         |
| Composer 2.5         | `composer-2.5` (Cursor's in-house model) |
| Gemini 3.1 Pro       | `gemini-3.1-pro` (used by the Gemini migration example below) |
| Grok 4.5 (Cursor)    | `cursor-grok-4.5-high` (also `-medium`, `-low`; each has a `-fast` variant) |
| Kimi K2.7 Code       | `kimi-k2.7-code`            |

Notes:

- Anthropic models in Cursor are listed without a fixed thinking
  budget; reasoning controls live in the suffix (`-low`, `-medium`,
  `-high`, `-thinking-low`, etc.).
- Cursor's Grok ids are `cursor-`-prefixed with the reasoning tier baked
  in: `cursor-grok-4.5-high` / `-medium` / `-low` (append `-fast` for the
  faster variant). The bare `grok-4-20` id from older catalogs is gone —
  always confirm with `cursor-agent --list-models`.
- moa-x does not validate model ids — Cursor errors are surfaced
  verbatim if you typo.

## Per-model lab routing

Cursor doesn't publish a structured model-to-lab map in their CLI
reference, but the model list at https://cursor.com/docs/models groups
identifiers by provider. Useful summary as of 2026-04:

| Lab       | Cursor model prefixes                                     |
| --------- | ---------------------------------------------------------- |
| OpenAI    | `gpt-*`                                                    |
| Anthropic | `claude-*`                                                 |
| Google    | `gemini-*`                                                 |
| xAI       | `grok-*`                                                   |
| Moonshot  | `kimi-*`                                                   |
| Zhipu     | `glm-*`                                                    |
| Cursor    | `composer-*` (Cursor's own foundation models)              |

### Cursor-hosted GLM / Kimi vs opencode

Cursor's catalog also routes Kimi K2.7 Code and GLM 5.2. If you already
pay for Cursor, routing those through the cursor harness is a valid
alternative to the default `opencode` lanes — one subscription, one CLI.
The catch: **the Cursor CLI has no bring-your-own-key path**, so
Cursor-hosted GLM/Kimi run on Cursor's billing, not your Zhipu /
Moonshot keys. If you want to run GLM 5.2 or Kimi K2.7 Code on your own
provider keys, use the `opencode` harness (the default) instead — it
reads `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` directly.

moa-x does not infer lab from model id at runtime. The `lab` concept
is intentionally absent from the data model — see
[`docs/architecture.md`](./architecture.md). If you want to recreate
the historical "Layer 2 should not be Anthropic" rule when using
Cursor for everything, set `refiners:` to non-Anthropic models in
`harness/config.yaml` (e.g. `[c-gpt, c-gemini]` from the
example config).

## Configuration examples

Add the Grok lane on top of the default ensemble. `cursor-grok`
(model `cursor-grok-4.5-high`) ships as a **built-in**, so no
`providers:` block is needed — just name it in a layer:

```yaml
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex-reviewer, qwen]
```

To pin a different Cursor Grok tier, override the built-in's model:

```yaml
providers:
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-medium}
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex-reviewer, qwen]
```

One CLI delivering everything (the consolidate-around-Cursor case):

```yaml
providers:
  c-gpt:    {harness: cursor, model: gpt-5.5-medium}
  c-sonnet: {harness: cursor, model: claude-4.5-sonnet}
  c-gemini: {harness: cursor, model: gemini-3.1-pro}
layers:
  proposers: [c-gpt, c-sonnet, c-gemini]
  refiners:  [c-gpt, c-gemini]
  # CLAUDE.md recommends keeping refiners off the aggregator's lab
  # (Anthropic). The orchestrator warns but doesn't block.
```

Migrating from the removed `gemini` provider (v0.3.0 dropped the gemini
harness and built-in provider). If you relied on a Gemini lane, route it
through Cursor as a user-defined provider:

```yaml
providers:
  cursor-gemini: {harness: cursor, model: gemini-3.1-pro}
layers:
  proposers: [codex, glm, sonnet, cursor-gemini]
  refiners:  [codex-reviewer, qwen]
```

(This runs on Cursor billing — the Cursor CLI has no bring-your-own-key
path for Google models.)

Override a model at runtime:

```bash
MOA_CURSOR_GROK_MODEL=cursor-grok-4.5-medium python harness/scripts/run_moa.py ...
```

Per-provider timeout (for slower thinking models):

```yaml
providers:
  cursor-opus-think: {harness: cursor, model: claude-opus-4-7-thinking-high, timeout: 1800}
```

Or via env:

```
MOA_CURSOR_OPUS_THINK_TIMEOUT=1800
```

(`-` in the provider name becomes `_` in the env var, then uppercased.)

## Concurrency and rate limits

Cursor session/auth state lives under `~/.cursor/`. The orchestrator's
per-user lock (`<system-temp>/moa-<uid>.lock` on POSIX) prevents concurrent
MoA-X invocations from racing on it; lanes within one run share that lock.

Running 3-4 concurrent `cursor-agent` lanes per MoA call means 3-4× the
burn against one Cursor subscription or API key. There's no harness-side
budget cap today; if you're on a metered plan, watch your dashboard.

## Workspace trust in CI

`--trust` works only with `-p` (headless / `--print` mode), per the
Cursor CLI help text. CI environments invoking moa-x through the
orchestrator pick this up automatically. No further configuration is
needed; the first-run trust prompt that interactive users see is
bypassed in headless mode.

## Preflight (`install_deps.py`) is config-aware

`python3 harness/scripts/install_deps.py` reads your `harness/config.yaml`
(via the same `load_resolved_config()` path the orchestrator uses) and
checks only what your config actually needs:

- **Required harnesses** = `{p.harness for p in proposers + refiners}`.
  Harnesses not referenced in any layer are reported as "unused" and
  skipped — no failure for codex/claude/opencode not being installed if
  your config is cursor-only.
- **Schema coherence**: every resolved provider name is regex-tested
  against the proposer/refiner schemas' `agent_id` patterns. Catches
  the kind of mismatch that surfaces when user-named providers run
  against schemas hardcoded to lab names.
- **Cursor model availability**: each cursor provider's `model:` is
  checked against `cursor-agent --list-models`. Catches the most
  common typo class (friendly names vs machine ids:
  `gpt-5.5` vs `gpt-5.5-medium`, `grok-4.5` vs `cursor-grok-4.5-high`).
- **Auth probe**: each needed harness's `check_available()` runs (which
  for cursor uses `cursor-agent whoami`). Stale tokens / expired
  sessions surface here, before a real run wastes wall-clock.

If `harness/config.yaml` doesn't exist, preflight falls back to the
built-in default ensemble (`proposers: [codex, glm, sonnet]`,
`refiners: [codex-reviewer, qwen]`), which preserves the "is the moa-x shipped
baseline ready?" diagnostic.

## What this integration does NOT cover

- **Worktree isolation.** Cursor exposes `--worktree` for this; the
  adapter doesn't currently use it. Plan mode obviates the need for
  most use cases. If a user reports an actual write incident we'd
  revisit.
- **Cost accounting.** The `usage` block is captured in the adapter
  log file but not yet aggregated into the manifest. Tracked in the
  follow-up `MOA_MAX_COST` work.
- **Self-moa with Cursor.** The `--self-moa` arm currently hardcodes
  the claude adapter for instance multiplication. Generalizing it to
  any harness (so e.g. four cursor lanes with different models could
  run as a self-moa) is a separate feature.
