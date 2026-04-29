# Cursor CLI integration

This page documents how moa-x integrates with the Cursor CLI
(`cursor-agent`), what guarantees the integration provides, and how to
configure it. For named-provider config in general see
[`docs/config.md`](./config.md); for the architectural rationale see
[`docs/architecture.md`](./architecture.md).

## Why Cursor

Cursor is the first multi-lab CLI in the moa-x default set. A single
`cursor-agent` binary routes to OpenAI, Anthropic, Google, xAI, or
Moonshot models via the `--model` flag — useful for adding a fourth
ensemble lane, or for users who want to consolidate billing around one
subscription.

This is also why named providers exist: the legacy
`{codex, gemini, sonnet}` strings packed CLI + lab + model into one
token, which Cursor's one-CLI-many-labs shape broke. See
`docs/architecture.md` for the full design discussion.

## Filesystem guarantees

The cursor adapter invokes `cursor-agent --mode plan` for every call.
Plan mode is enforced at the Cursor CLI layer:

> `--mode plan` — read-only/planning (analyze, propose plans, no edits)

Verified: prompts that ask the model to write a file return *"plan mode
is active and I lack permission to run write/edit tools"* and produce
no file. This is a real CLI-level guarantee, not a prompt hint.

Implication: the adapter does **not** prepend the
`READ_ONLY_RULE` prompt directive that the gemini and claude adapters
use. Those adapters have no equivalent CLI flag, so the prompt is their
only line of defense. Cursor's plan mode replaces the prompt rule —
keeping both would just add token overhead.

If a future Cursor release regresses plan-mode enforcement, the
orchestrator's session is still bounded by `cwd=repo_path` (set on the
subprocess), so any rogue write lands inside the user's repo where
`.gitignore` and review surface it. Defense in depth lives at the git
layer, not the prompt layer.

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

Set `MOA_CURSOR_BIN` if `cursor-agent` lives somewhere unusual; the
default search is the system PATH.

## Command line shape

The adapter invokes:

```bash
cursor-agent -p \
  --model <model> \
  --mode plan \
  --output-format json \
  --trust \
  -- \
  <prompt>
```

| Flag                  | Purpose                                                  |
| --------------------- | -------------------------------------------------------- |
| `-p`                  | Print/non-interactive mode (required for headless)        |
| `--mode plan`         | Read-only enforcement (see *Filesystem guarantees*)        |
| `--output-format json`| Structured envelope for the orchestrator's parser          |
| `--trust`             | Bypass workspace-trust prompt (works only with `-p`)      |
| `--`                  | Fence the prompt arg from option parsing                  |

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
proposer/refiner schema Python-side, mirroring the gemini adapter's
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
| Gemini 3.1 Pro       | `gemini-3.1-pro`            |
| Gemini 3 Flash       | `gemini-3-flash`            |
| Grok 4.20            | `grok-4-20`                 |
| Kimi K2.5            | `kimi-k2.5`                 |

Notes:

- Anthropic models in Cursor are listed without a fixed thinking
  budget; reasoning controls live in the suffix (`-low`, `-medium`,
  `-high`, `-thinking-low`, etc.).
- xAI's Grok-4 is `grok-4-20` (dashed), not `grok-4.20` (dotted).
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
| Cursor    | `composer-*` (Cursor's own foundation models)              |

moa-x does not infer lab from model id at runtime. The `lab` concept
is intentionally absent from the data model — see
[`docs/architecture.md`](./architecture.md). If you want to recreate
the historical "Layer 2 should not be Anthropic" rule when using
Cursor for everything, set `refiners:` to non-Anthropic models in
`harness/config.yaml` (e.g. `[c-gpt, c-gemini]` from the
example config).

## Configuration examples

Add a fourth lane (Grok) on top of the default ensemble:

```yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4-20}
layers:
  proposers: [codex, gemini, sonnet, cursor-grok]
  refiners:  [codex, gemini]
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

Override a model at runtime:

```bash
MOA_CURSOR_GROK_MODEL=grok-4-20-thinking python harness/scripts/run_moa.py ...
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
file lock (`/tmp/moa.lock`) prevents concurrent moa-x invocations from
racing on it; lanes within a single MoA run are safe under that lock.

Running 3-4 concurrent `cursor-agent` lanes per MoA call means 3-4× the
burn against one Cursor subscription or API key. There's no harness-side
budget cap today; if you're on a metered plan, watch your dashboard.

## Workspace trust in CI

`--trust` works only with `-p` (headless / `--print` mode), per the
Cursor CLI help text. CI environments invoking moa-x through the
orchestrator pick this up automatically. No further configuration is
needed; the first-run trust prompt that interactive users see is
bypassed in headless mode.

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
