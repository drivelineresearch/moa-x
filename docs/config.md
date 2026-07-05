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

> **The `config.yaml` lane needs PyYAML.** `run_moa.py` / `install_deps.py`
> run under the system `python3`; the loader raises if `config.yaml` exists but
> PyYAML is not installed (`pip install pyyaml`). Levels 1–3 above (CLI flags,
> `MOA_*` env vars, `.env`) need no dependency — use them if you'd rather not
> install PyYAML.

## Named providers

A provider is a `{name, harness, model}` triple. The `harness` is
which CLI gets invoked; the `model` is what that harness asks for;
the `name` is a user-facing label that becomes the `agent_id` in
payloads.

### Built-in defaults

These five are always available without declaring them:

| Name | Harness | Default model |
|---|---|---|
| `codex` | `codex` CLI | `gpt-5.4` |
| `sonnet` | `claude` CLI | `claude-sonnet-4-6` |
| `glm` | `opencode` CLI | `opencode-go/glm-5.2` |
| `kimi` | `opencode` CLI | `opencode-go/kimi-k2.7-code` |
| `composer` | `cursor` CLI | `composer-2.5` |

The default roster draws four labs from these: proposers
`[codex, glm, sonnet]` (OpenAI, Zhipu, Anthropic) and refiners
`[codex, kimi]` (OpenAI, Moonshot). The refiners stay independent
of the Anthropic aggregator (Opus).

Override built-in models via CLI flags (`--codex-model`,
`--sonnet-model`) or the `MOA_CODEX_MODEL` / `MOA_SONNET_MODEL` /
`MOA_GLM_MODEL` / `MOA_KIMI_MODEL` env vars.

### User-defined providers

Add your own under `providers:` in `harness/config.yaml`:

```yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4-20}
```

Then reference the name in `layers:`:

```yaml
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex, kimi]
```

For user-named providers, model and timeout are overridable via env var
using the name uppercased with `-` → `_`:

| Pattern | Example | What it does |
|---|---|---|
| `MOA_<NAME>_MODEL` | `MOA_CURSOR_GROK_MODEL=grok-4-20-thinking` | Override model for that provider |
| `MOA_<NAME>_TIMEOUT` | `MOA_CURSOR_GROK_TIMEOUT=900` | Wall-clock cap in seconds |

### Env-var shorthand: `MOA_PROVIDER_<NAME>`

You can define a provider entirely from the environment, no
`config.yaml` block required. Set `MOA_PROVIDER_<NAME>=<harness>:<model>`;
the `<NAME>` is lowercased and `_` → `-` to form the provider name.

```bash
# Defines a provider named `glm-fw` on the opencode harness,
# routed through Fireworks:
MOA_PROVIDER_GLM_FW=opencode:fireworks-ai/accounts/fireworks/models/glm-5p2
```

Then add `glm-fw` to `MOA_PROPOSERS` / `MOA_REFINERS` or a `layers:`
block. If a provider of the same name is also declared in a
`config.yaml` `providers:` block, the YAML block wins. A malformed
value (missing the `harness:model` split) fails loudly.

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
MOA_GLM_MODEL=opencode-go/glm-5.2
```

### `harness/config.yaml` (structured)

```bash
cp harness/config.example.yaml harness/config.yaml
```

Then edit. Example:

```yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4-20}
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex, kimi]
```

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `MOA_CODEX_BIN` | `codex` | Path or name of the codex binary. Set this if codex isn't on PATH or lives somewhere non-standard. |
| `MOA_CLAUDE_BIN` | `claude` | Same for claude. |
| `MOA_OPENCODE_BIN` | `opencode` | Same for opencode (GLM / Kimi harness). |
| `MOA_CURSOR_BIN` | `cursor-agent` | Same for cursor (binary is `cursor-agent`, or `agent` on newer installs). |
| `MOA_CODEX_MODEL` | `gpt-5.4` | Codex model id. |
| `MOA_CODEX_EFFORT` | `high` | One of `low`, `medium`, `high`, `xhigh`. Higher = better, slower. Default `--codex-timeout` scales with this. |
| `MOA_SONNET_MODEL` | `claude-sonnet-4-6` | Model for the sonnet proposer (the `claude` CLI in sonnet mode). |
| `MOA_GLM_MODEL` | `opencode-go/glm-5.2` | Model id for the `glm` provider (opencode harness). Provider/model string. |
| `MOA_KIMI_MODEL` | `opencode-go/kimi-k2.7-code` | Model id for the `kimi` provider (opencode harness). Provider/model string. |
| `MOA_CODEX_TIMEOUT` | effort-scaled | Wall-clock cap for codex calls. xhigh=1500s, high=1200s, medium/low=900s. |
| `MOA_SONNET_TIMEOUT` | `1200` | Wall-clock cap for sonnet calls, in seconds. |
| `MOA_OPENCODE_TIMEOUT` | `1200` | Wall-clock cap for opencode calls (glm / kimi), in seconds. |
| `MOA_CURSOR_TIMEOUT` | `1200` | Wall-clock cap for cursor calls, in seconds. |
| `MOA_<NAME>_MODEL` | — | Model override for any user-named provider (name uppercased, `-` → `_`). |
| `MOA_<NAME>_TIMEOUT` | `1200` | Timeout override for any user-named provider. |
| `MOA_PROVIDER_<NAME>` | — | Define a provider inline as `<harness>:<model>` (name lowercased, `_` → `-`). No `config.yaml` needed. |
| `MOA_PROPOSERS` | `codex,glm,sonnet` | Comma-separated subset of proposers to spawn. |
| `MOA_REFINERS` | `codex,kimi` | Comma-separated subset of refiners. |
| `MOA_SKIP_LAYER2` | unset | Set to `1` to skip the refinement layer entirely. |
| `MOA_NO_REPORT` | unset | Set to `1` to skip generating `<session>/report.html` after a run (same as `--no-report`). See [`docs/report.md`](report.md). |

CLI flag equivalents exist for every row here. Run
`python3 harness/scripts/run_moa.py --help` to see them.

## Examples

### 5-lane mix (defaults + cursor-grok)

```yaml
# harness/config.yaml
providers:
  cursor-grok: {harness: cursor, model: grok-4-20}
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex, kimi]
```

Adds an extra proposer lane without touching the built-in roster.

### GLM / Kimi through Fireworks

The `glm` and `kimi` defaults route through the opencode-go gateway
(`opencode-go/…`). To run the same models through their native
providers (`zhipuai/glm-5.2`, `moonshotai/kimi-k2.7-code`) or through
Fireworks instead, declare user providers with those model strings:

```yaml
# harness/config.yaml
providers:
  glm-fw:  {harness: opencode, model: fireworks-ai/accounts/fireworks/models/glm-5p2}
  kimi-fw: {harness: opencode, model: fireworks-ai/accounts/fireworks/models/kimi-k2p7-code}
layers:
  proposers: [codex, glm-fw, sonnet]
  refiners:  [codex, kimi-fw]
```

Or define them inline without a config file:

```bash
MOA_PROVIDER_GLM_FW=opencode:fireworks-ai/accounts/fireworks/models/glm-5p2
MOA_PROVIDER_KIMI_FW=opencode:fireworks-ai/accounts/fireworks/models/kimi-k2p7-code
MOA_PROPOSERS=codex,glm-fw,sonnet
MOA_REFINERS=codex,kimi-fw
```

### One CLI, many labs (cursor-everywhere)

```yaml
# harness/config.yaml
providers:
  c-gpt:    {harness: cursor, model: gpt-5.5-medium}
  c-sonnet: {harness: cursor, model: claude-4.5-sonnet}
  c-composer: {harness: cursor, model: composer-2.5}
layers:
  proposers: [c-gpt, c-sonnet, c-composer]
  refiners:  [c-gpt, c-composer]
```

Consolidates billing through the Cursor CLI while keeping
cross-lab diversity at the model level.

## Migrating from gemini

The `gemini` provider and its adapter were removed in v0.3.0. There
is no longer a `gemini` harness, no built-in `gemini` provider, and
no `MOA_GEMINI_*` knobs or `--gemini-model` / `--gemini-timeout`
flags. The default roster's cross-lab diversity now comes from GLM
(Zhipu) and Kimi (Moonshot) via the `opencode` harness.

If you still want a Gemini model in the ensemble, route it through
the `cursor` harness as a user provider:

```yaml
# harness/config.yaml
providers:
  cursor-gemini: {harness: cursor, model: gemini-3.1-pro}
layers:
  proposers: [codex, glm, sonnet, cursor-gemini]
  refiners:  [codex, kimi]
```

## Secrets

Put secrets in `.env` or your shell environment. Never commit keys.
The repo's `.gitignore` already covers `.env`, `.env.local`, and
`.env.*.local`.
