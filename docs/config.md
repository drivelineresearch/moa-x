# Configuration

Every knob MoA-X exposes has a built-in default. You only need to
touch configuration when you want to override one.

## Precedence

Highest wins. If the same value is set in more than one place, the
one further up this list takes effect:

1. **CLI flags** passed to `run_moa.py` (or forwarded by the skill).
2. **Shell / process environment variables** in the `MOA_*` namespace.
3. **`.env`** file at the repo root.
4. **`harness/config.yaml`**.
5. **Built-in defaults** in `run_moa.py` and the adapters.

The loader lives in `harness/scripts/config.py`. An offline test
(`test_offline.py`, case 14) asserts this precedence. If you change
it, that test will tell you.

## Named providers

A provider is a `{name, harness, model}` triple. The `harness` is
which CLI gets invoked; the `model` is what that harness asks for;
the `name` is a user-facing label that becomes the `agent_id` in
payloads.

### Built-in defaults

These three are always available without declaring them:

| Name | Harness | Default model |
|---|---|---|
| `codex` | `codex` CLI | `gpt-5.4` |
| `gemini` | `gemini` CLI | `gemini-2.5-pro` |
| `sonnet` | `claude` CLI | `claude-sonnet-4-6` |

Override built-in models via CLI flags (`--codex-model`, etc.) or
the `MOA_CODEX_MODEL` / `MOA_GEMINI_MODEL` / `MOA_SONNET_MODEL` env vars.

### User-defined providers

Add your own under `providers:` in `harness/config.yaml`:

```yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4.20}
```

Then reference the name in `layers:`:

```yaml
layers:
  proposers: [codex, gemini, sonnet, cursor-grok]
  refiners:  [codex, gemini]
```

For user-named providers, model and timeout are overridable via env var
using the name uppercased with `-` → `_`:

| Pattern | Example | What it does |
|---|---|---|
| `MOA_<NAME>_MODEL` | `MOA_CURSOR_GROK_MODEL=grok-4.21` | Override model for that provider |
| `MOA_<NAME>_TIMEOUT` | `MOA_CURSOR_GROK_TIMEOUT=900` | Wall-clock cap in seconds |

## Two file shapes

Pick whichever matches how you like to work; they do the same thing.

### `.env` (flat)

```bash
cp .env.example .env
```

Then edit. Format is plain `KEY=value` with `#` comments. Example:

```
MOA_CODEX_MODEL=gpt-5.4
MOA_CODEX_EFFORT=xhigh
MOA_SONNET_TIMEOUT=1500
```

### `harness/config.yaml` (structured)

```bash
cp harness/config.example.yaml harness/config.yaml
```

Then edit. Example:

```yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4.20}
layers:
  proposers: [codex, gemini, sonnet, cursor-grok]
  refiners:  [codex, gemini]
```

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `MOA_CODEX_BIN` | `codex` | Path or name of the codex binary. Set this if codex isn't on PATH or lives somewhere non-standard. |
| `MOA_GEMINI_BIN` | `gemini` | Same for gemini. |
| `MOA_CLAUDE_BIN` | `claude` | Same for claude. |
| `MOA_CURSOR_BIN` | `cursor-agent` | Same for cursor. |
| `MOA_CODEX_MODEL` | `gpt-5.4` | Codex model id. |
| `MOA_CODEX_EFFORT` | `high` | One of `low`, `medium`, `high`, `xhigh`. Higher = better, slower. Default `--codex-timeout` scales with this. |
| `MOA_GEMINI_MODEL` | `gemini-2.5-pro` | Gemini model id. `gemini-3.1-pro-preview` is available but flaky. |
| `MOA_SONNET_MODEL` | `claude-sonnet-4-6` | Model for the sonnet proposer (the `claude` CLI in sonnet mode). |
| `MOA_CODEX_TIMEOUT` | effort-scaled | Wall-clock cap for codex calls. xhigh=1500s, high=1200s, medium/low=900s. |
| `MOA_GEMINI_TIMEOUT` | `1200` | Wall-clock cap for gemini calls, in seconds. |
| `MOA_SONNET_TIMEOUT` | `1200` | Wall-clock cap for sonnet calls, in seconds. |
| `MOA_<NAME>_MODEL` | — | Model override for any user-named provider (name uppercased, `-` → `_`). |
| `MOA_<NAME>_TIMEOUT` | `1200` | Timeout override for any user-named provider. |
| `MOA_PROPOSERS` | `codex,gemini,sonnet` | Comma-separated subset of proposers to spawn. |
| `MOA_REFINERS` | `codex,gemini` | Comma-separated subset of refiners. |
| `MOA_SKIP_LAYER2` | unset | Set to `1` to skip the refinement layer entirely. |

CLI flag equivalents exist for every row here. Run
`python3 harness/scripts/run_moa.py --help` to see them.

## Examples

### 4-lane mix (defaults + cursor-grok)

```yaml
# harness/config.yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4.20}
layers:
  proposers: [codex, gemini, sonnet, cursor-grok]
  refiners:  [codex, gemini]
```

Adds a fourth proposer lane without touching the three built-ins.

### One CLI, many labs (cursor-everywhere)

```yaml
# harness/config.yaml
providers:
  c-gpt:    {harness: cursor, model: gpt-5.5}
  c-sonnet: {harness: cursor, model: claude-sonnet-4-6}
  c-gemini: {harness: cursor, model: gemini-3.1-pro}
layers:
  proposers: [c-gpt, c-sonnet, c-gemini]
  refiners:  [c-gpt, c-gemini]
```

Consolidates billing through the Cursor CLI while keeping
cross-lab diversity at the model level.

## Secrets

Put secrets in `.env` or your shell environment. Never commit keys.
The repo's `.gitignore` already covers `.env`, `.env.local`, and
`.env.*.local`.
