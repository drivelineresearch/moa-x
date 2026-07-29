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
the `name` is a stable route identifier that becomes the `agent_id` in
payloads. Compatibility names such as `sonnet`, `opus`, `kimi`, and `qwen`
remain stable while their resolved model ids can advance. The Web UI shows
the canonical model label and keeps the route identifier in run provenance.

### Built-in defaults

These curated routes are always available without declaring them. The
configuration name is a stable internal route identifier; the Web UI presents
the canonical model and effort. Legacy `codex-reviewer` and
`codex-aggregator` names still resolve for old configs but are hidden from
the curated Web UI.

| Name | Harness | Default model | Effort |
|---|---|---|---|
| `codex` | `codex` CLI | `gpt-5.6-terra` | high |
| `codex-sol` | `codex` CLI | `gpt-5.6-sol` | high; xhigh as default aggregator |
| `codex-luna` | `codex` CLI | `gpt-5.6-luna` | medium |
| `sonnet` | `claude` CLI | `claude-sonnet-5` | high |
| `opus` | `claude` CLI | `claude-opus-5` | high |
| `glm` | `opencode` CLI | `opencode-go/glm-5.2` | provider default |
| `kimi` | `opencode` CLI | `opencode-go/kimi-k3` | provider default |
| `qwen` | `opencode` CLI | `qwen-token-plan/qwen3.8-max-preview` | provider default |
| `qwen-opencode` | `opencode` CLI | `opencode-go/qwen3.7-max` | provider default |
| `deepseek` | `opencode` CLI | `opencode-go/deepseek-v4-pro` | provider default |
| `deepseek-flash` | `opencode` CLI | `opencode-go/deepseek-v4-flash` | provider default |
| `composer` | `cursor` CLI | `composer-2.5` | encoded in model id |
| `grok` | `opencode` CLI | `opencode-go/grok-4.5` | provider default |
| `cursor-grok` | `cursor` CLI | `cursor-grok-4.5-high` | encoded in model id |
| `agy-gemini-pro` | `agy` CLI | `gemini-3.1-pro-high` | high |
| `fable` | `claude` CLI | `claude-fable-5` | xhigh; aggregator only |

Most curated routes can be proposers or refiners. `fable` is
aggregator-only and is rejected from Layers 1 and 2. The Web UI defaults to
`codex-sol` and exposes Fable only behind a conspicuous quota warning and
acknowledgement phrase. Explicit custom or legacy CLI configurations can still
dispatch Layer 3 through Claude. DeepSeek uses the authenticated
OpenCode Go account; `deepseek` is listed before Flash as the preferred
full-capability route.

The default roster uses a distinct lab for every lane: proposers
`[agy-gemini-pro, grok, glm]` (Google, xAI, Zhipu), refiners
`[qwen, kimi, opus]` (Alibaba, Moonshot, Anthropic), and aggregator
`codex-sol` at `xhigh` (OpenAI).

Override built-in models with matching `MOA_<NAME>_MODEL` environment
variables. Dedicated CLI flags remain for `codex`, `sonnet`, and the
aggregator; `--codex-reviewer-model` is retained only for legacy configs.
Provider effort can be set with `effort:` in YAML or
`MOA_<NAME>_EFFORT` when the underlying CLI has a separate native effort
flag. AGY is the exception: its model suffix selects depth and takes
precedence over a separate effort override.

### User-defined providers

Add your own under `providers:` in `harness/config.yaml`:

```yaml
providers:
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-high}
```

Then reference the name in `layers:`:

```yaml
layers:
  proposers: [agy-gemini-pro, grok, glm, cursor-grok]
  refiners:  [qwen, kimi, opus]
  aggregator: codex-sol
```

For user-named providers, model and timeout are overridable via env var
using the name uppercased with `-` → `_`:

| Pattern | Example | What it does |
|---|---|---|
| `MOA_<NAME>_MODEL` | `MOA_CURSOR_GROK_MODEL=cursor-grok-4.5-medium` | Override model for that provider |
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
MOA_CODEX_MODEL=gpt-5.6-terra
MOA_CODEX_EFFORT=high
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
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-high}
layers:
  proposers: [agy-gemini-pro, grok, glm, cursor-grok]
  refiners:  [qwen, kimi, opus]
  aggregator: codex-sol
```

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `MOA_CODEX_BIN` | `codex` | Path or name of the codex binary. Set this if codex isn't on PATH or lives somewhere non-standard. |
| `MOA_CLAUDE_BIN` | `claude` | Same for claude. |
| `MOA_OPENCODE_BIN` | `opencode` | Same for opencode (GLM / Qwen harness). |
| `MOA_CURSOR_BIN` | `cursor-agent` | Same for cursor (binary is `cursor-agent`, or `agent` on newer installs). |
| `MOA_AGY_BIN` | `agy` | Same for the Antigravity consumer-Google harness. |
| `MOA_CODEX_MODEL` | `gpt-5.6-terra` | Codex proposer model id. |
| `MOA_CODEX_REVIEWER_MODEL` | `gpt-5.6-sol` | Codex reviewer model id. |
| `MOA_CODEX_EFFORT` | `high` | One of `low`, `medium`, `high`, `xhigh`. Higher = better, slower. Default `--codex-timeout` scales with this. |
| `MOA_CODEX_REVIEWER_EFFORT` | `high` | Independent reasoning effort for codex-harness refiners. |
| `MOA_SONNET_MODEL` | `claude-sonnet-5` | Pinned Claude Sonnet 5 proposer model. The stable provider name remains `sonnet`. |
| `MOA_AGGREGATOR_MODEL` | provider model | Override the model recorded or invoked for Layer 3. |
| `MOA_AGGREGATOR_EFFORT` | `xhigh` | Reasoning effort for a Codex Layer-3 subprocess. |
| `MOA_GLM_MODEL` | `opencode-go/glm-5.2` | Model id for the `glm` provider (opencode harness). Provider/model string. |
| `MOA_KIMI_MODEL` | `opencode-go/kimi-k3` | Model id for the `kimi` provider (opencode harness). Provider/model string. |
| `MOA_QWEN_MODEL` | `qwen-token-plan/qwen3.8-max-preview` | Model id for the built-in Qwen Token Plan refiner. |
| `MOA_CODEX_TIMEOUT` | effort-scaled | Wall-clock cap for codex calls. xhigh=1500s, high=1200s, medium/low=900s. |
| `MOA_SONNET_TIMEOUT` | `1200` | Wall-clock cap for sonnet calls, in seconds. |
| `MOA_OPENCODE_TIMEOUT` | `1200` | Harness-level wall-clock cap for opencode calls; built-in Qwen overrides this to `600`. |
| `MOA_CURSOR_TIMEOUT` | `1200` | Wall-clock cap for cursor calls, in seconds. |
| `MOA_AGY_TIMEOUT` | `1200` | Harness-level wall-clock cap for AGY. |
| `MOA_<NAME>_MODEL` | — | Model override for any user-named provider (name uppercased, `-` → `_`). |
| `MOA_<NAME>_TIMEOUT` | `1200` | Timeout override for any user-named provider. |
| `MOA_<NAME>_EFFORT` | provider default | Provider-native effort/variant override. Codex supports `low` through `xhigh`; Claude additionally supports `max`; OpenCode receives the value as `--variant`. AGY and Cursor select depth in the model id; an AGY model suffix takes precedence over this variable. |
| `MOA_PROVIDER_<NAME>` | — | Define a provider inline as `<harness>:<model>` (name lowercased, `_` → `-`). No `config.yaml` needed. |
| `MOA_PROPOSERS` | `agy-gemini-pro,grok,glm` | Comma-separated provider names to spawn as proposers. |
| `MOA_REFINERS` | `qwen,kimi,opus` | Comma-separated provider names to spawn as refiners. |
| `MOA_AGGREGATOR` | `codex-sol` | Named Layer-3 provider. The shipped path uses GPT-5.6 Sol at `xhigh`; `fable` is the quota-heavy aggregator-only alternative. |
| `MOA_SKIP_LAYER2` | unset | Set to `1` to skip the refinement layer entirely. |
| `MOA_NO_REPORT` | unset | Set to `1` to skip generating `<session>/report.html` after a run (same as `--no-report`). See [`docs/report.md`](report.md). |

CLI flag equivalents exist for the runner-level controls; provider-specific
models and credentials stay in environment/config. Run
`python3 harness/scripts/run_moa.py --help` for the full CLI surface.

Provider-specific model overrides such as `MOA_QWEN_MODEL` are environment
or `.env` settings; there is no dedicated `--qwen-model` flag. Select the
provider with `--proposers ...qwen...` or `MOA_PROPOSERS`.

## Examples

### Configure the default Qwen Token Plan refiner

The default built-in `qwen` refiner uses Qwen Cloud Token Plan through the
OpenCode harness. Store the dedicated `sk-sp-...` key in the gitignored
`.env` file:

```bash
QWEN_TOKEN_PLAN_API_KEY=sk-sp-...
MOA_REFINERS=qwen,kimi,opus
```

Its default model string is `qwen-token-plan/qwen3.8-max-preview`, with a
600-second provider timeout. The adapter creates
an isolated OpenCode provider configuration for
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` and
references the key through OpenCode's `{env:QWEN_TOKEN_PLAN_API_KEY}` syntax;
Token Plan keys and pay-as-you-go keys/endpoints are not interchangeable.
See the official [Qwen Token Plan quick start](https://docs.qwencloud.com/token-plan/team/token-plan-team-quickstart)
and [OpenCode setup](https://docs.qwencloud.com/developer-guides/clients-and-developer-tools/opencode).

### 4-lane mix (defaults + cursor-grok)

```yaml
# harness/config.yaml
providers:
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-high}
layers:
  proposers: [agy-gemini-pro, grok, glm, cursor-grok]
  refiners:  [qwen, kimi, opus]
  aggregator: codex-sol
```

Adds an extra proposer lane without touching the built-in roster.

### GLM through Fireworks

The `glm` default routes through the opencode-go gateway. To run GLM-5.2
through its native provider (`zhipuai/glm-5.2`) or Fireworks instead, declare
a user provider with that provider/model string:

```yaml
# harness/config.yaml
providers:
  glm-fw:  {harness: opencode, model: fireworks-ai/accounts/fireworks/models/glm-5p2}
layers:
  proposers: [agy-gemini-pro, grok, glm-fw]
  refiners:  [qwen, kimi, opus]
  aggregator: codex-sol
```

Or define them inline without a config file:

```bash
MOA_PROVIDER_GLM_FW=opencode:fireworks-ai/accounts/fireworks/models/glm-5p2
MOA_PROPOSERS=agy-gemini-pro,grok,glm-fw
MOA_REFINERS=qwen,kimi,opus
MOA_AGGREGATOR=codex-sol
```

## Google models through AGY

Google models are available through the AGY harness. It reuses the
account already signed in locally; MoA-X does not ask for, copy, or store
credentials.

- `agy-gemini-pro` selects Gemini 3.1 Pro for proposer or refiner work. It is
  the only curated Gemini route; the former Flash routes are retired.
  Live AGY 1.1.7 validation on July 26, 2026 passed a 380,311-character
  schema-shaped proposer prompt with exact end-marker retention and no schema
  or evidence cross-field errors. Pro is part of every recommended proposer
  roster.

The route uses Antigravity CLI's consumer Google-account path. AGY 1.1.5+ is
required for stable model slugs. The Web UI's depth control changes the
`-low`, `-medium`, or `-high` model suffix and only enables variants present
in the signed-in account's live `agy models` catalog; it never sends a
separate AGY `--effort` flag.
The adapter requires plan mode plus sandboxing and supports proposer and
broadcast-refiner roles. Gemini Pro is part of the shipped proposer roster:

```yaml
layers:
  proposers: [agy-gemini-pro, grok, glm]
  refiners:  [qwen, kimi, opus]
  aggregator: codex-sol
```

`fable` resolves to `claude-fable-5` through the authenticated Claude CLI. It
is intentionally restricted to aggregation. The Web UI warning
and acknowledgement phrase are a quota-speed-bump, not an authentication
boundary; direct CLI users remain responsible for shared account limits.

Run `agy models` to see the stable slugs enabled for the current account.
Preflight rejects a configured AGY model that is not in that list. Custom
providers can target the harness, for example
`{harness: agy, model: gemini-3.1-pro-low}`. Model names and timeouts may
also be overridden with `MOA_<NAME>_MODEL` / `MOA_<NAME>_TIMEOUT`;
`MOA_AGY_BIN` selects a nonstandard executable path.

## Secrets

Put secrets in `.env` or your shell environment. Never commit keys.
The repo's `.gitignore` already covers `.env`, `.env.local`, and
`.env.*.local`.
