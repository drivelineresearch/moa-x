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

These eleven are always available without declaring them:

| Name | Harness | Default model |
|---|---|---|
| `codex` | `codex` CLI | `gpt-5.6-terra` |
| `codex-reviewer` | `codex` CLI | `gpt-5.6-sol` |
| `codex-aggregator` | `codex` CLI | `gpt-5.6-sol` (Layer 3; 600s cap) |
| `sonnet` | `claude` CLI | `sonnet` (rolling alias) |
| `opus` | `claude` CLI | `opus` (rolling alias; Layer 3) |
| `glm` | `opencode` CLI | `opencode-go/glm-5.2` |
| `kimi` | `opencode` CLI | `opencode-go/kimi-k2.7-code` |
| `qwen` | `opencode` CLI | `qwen-token-plan/qwen3.8-max-preview` |
| `composer` | `cursor` CLI | `composer-2.5` |
| `grok` | `opencode` CLI | `xai/grok-4.5` (needs `XAI_API_KEY`) |
| `cursor-grok` | `cursor` CLI | `cursor-grok-4.5-high` |

The default roster draws four labs from these: proposers
`[codex, glm, sonnet]` (OpenAI, Zhipu, Anthropic), refiners
`[codex-reviewer, qwen]` (OpenAI, Alibaba), and aggregator `opus`.
The refiners stay independent of the Anthropic aggregator.

Override built-in models via CLI flags (`--codex-model`,
`--codex-reviewer-model`, `--sonnet-model`, `--aggregator-model`) or the
matching `MOA_<NAME>_MODEL` environment variables.

### User-defined providers

Add your own under `providers:` in `harness/config.yaml`:

```yaml
providers:
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-high}
```

Then reference the name in `layers:`:

```yaml
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex-reviewer, qwen]
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
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex-reviewer, qwen]
  aggregator: opus
```

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `MOA_CODEX_BIN` | `codex` | Path or name of the codex binary. Set this if codex isn't on PATH or lives somewhere non-standard. |
| `MOA_CLAUDE_BIN` | `claude` | Same for claude. |
| `MOA_OPENCODE_BIN` | `opencode` | Same for opencode (GLM / Qwen harness). |
| `MOA_CURSOR_BIN` | `cursor-agent` | Same for cursor (binary is `cursor-agent`, or `agent` on newer installs). |
| `MOA_CODEX_MODEL` | `gpt-5.6-terra` | Codex proposer model id. |
| `MOA_CODEX_REVIEWER_MODEL` | `gpt-5.6-sol` | Codex reviewer model id. |
| `MOA_CODEX_EFFORT` | `high` | One of `low`, `medium`, `high`, `xhigh`. Higher = better, slower. Default `--codex-timeout` scales with this. |
| `MOA_CODEX_REVIEWER_EFFORT` | `high` | Independent reasoning effort for codex-harness refiners. |
| `MOA_SONNET_MODEL` | `sonnet` | Rolling Claude Code alias for the Sonnet proposer. |
| `MOA_AGGREGATOR_MODEL` | provider model | Override the model recorded or invoked for Layer 3. |
| `MOA_AGGREGATOR_EFFORT` | `high` | Reasoning effort for a Codex Layer-3 subprocess. |
| `MOA_GLM_MODEL` | `opencode-go/glm-5.2` | Model id for the `glm` provider (opencode harness). Provider/model string. |
| `MOA_KIMI_MODEL` | `opencode-go/kimi-k2.7-code` | Model id for the `kimi` provider (opencode harness). Provider/model string. |
| `MOA_QWEN_MODEL` | `qwen-token-plan/qwen3.8-max-preview` | Model id for the built-in Qwen Token Plan refiner. |
| `MOA_CODEX_TIMEOUT` | effort-scaled | Wall-clock cap for codex calls. xhigh=1500s, high=1200s, medium/low=900s. |
| `MOA_SONNET_TIMEOUT` | `1200` | Wall-clock cap for sonnet calls, in seconds. |
| `MOA_OPENCODE_TIMEOUT` | `1200` | Harness-level wall-clock cap for opencode calls; built-in Qwen overrides this to `600`. |
| `MOA_CURSOR_TIMEOUT` | `1200` | Wall-clock cap for cursor calls, in seconds. |
| `MOA_<NAME>_MODEL` | — | Model override for any user-named provider (name uppercased, `-` → `_`). |
| `MOA_<NAME>_TIMEOUT` | `1200` | Timeout override for any user-named provider. |
| `MOA_PROVIDER_<NAME>` | — | Define a provider inline as `<harness>:<model>` (name lowercased, `_` → `-`). No `config.yaml` needed. |
| `MOA_PROPOSERS` | `codex,glm,sonnet` | Comma-separated provider names to spawn as proposers. |
| `MOA_REFINERS` | `codex-reviewer,qwen` | Comma-separated provider names to spawn as refiners. |
| `MOA_AGGREGATOR` | `opus` | Named Layer-3 provider. Set `codex-aggregator` when using `--phase layer3` through Codex. |
| `MOA_SKIP_LAYER2` | unset | Set to `1` to skip the refinement layer entirely. |
| `MOA_NO_REPORT` | unset | Set to `1` to skip generating `<session>/report.html` after a run (same as `--no-report`). See [`docs/report.md`](report.md). |

CLI flag equivalents exist for the runner-level controls; provider-specific
models and credentials stay in environment/config. Run
`python3 harness/scripts/run_moa.py --help` for the full CLI surface.

Provider-specific model overrides such as `MOA_QWEN_MODEL` are environment
or `.env` settings; there is no dedicated `--qwen-model` flag. Select the
provider with `--proposers ...qwen...` or `MOA_PROPOSERS`.

## Examples

### Add Qwen Token Plan

The optional built-in `qwen` provider uses Qwen Cloud Token Plan through the
OpenCode harness. Store the dedicated `sk-sp-...` key in the gitignored
`.env` file:

```bash
QWEN_TOKEN_PLAN_API_KEY=sk-sp-...
MOA_REFINERS=codex-reviewer,qwen
```

Its default model string is `qwen-token-plan/qwen3.8-max-preview`, with a
600-second provider timeout. The adapter creates
an isolated OpenCode provider configuration for
`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` and
references the key through OpenCode's `{env:QWEN_TOKEN_PLAN_API_KEY}` syntax;
the secret is never copied into a session prompt, manifest, or log. Qwen
Token Plan keys and pay-as-you-go keys/endpoints are not interchangeable.
See the official [Qwen Token Plan quick start](https://docs.qwencloud.com/token-plan/team/token-plan-team-quickstart)
and [OpenCode setup](https://docs.qwencloud.com/developer-guides/clients-and-developer-tools/opencode).

### 5-lane mix (defaults + cursor-grok)

```yaml
# harness/config.yaml
providers:
  cursor-grok: {harness: cursor, model: cursor-grok-4.5-high}
layers:
  proposers: [codex, glm, sonnet, cursor-grok]
  refiners:  [codex-reviewer, qwen]
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
  refiners:  [codex-reviewer, kimi-fw]
```

Or define them inline without a config file:

```bash
MOA_PROVIDER_GLM_FW=opencode:fireworks-ai/accounts/fireworks/models/glm-5p2
MOA_PROVIDER_KIMI_FW=opencode:fireworks-ai/accounts/fireworks/models/kimi-k2p7-code
MOA_PROPOSERS=codex,glm-fw,sonnet
MOA_REFINERS=codex-reviewer,kimi-fw
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
  refiners:  [codex-reviewer, qwen]
```

## Secrets

Put secrets in `.env` or your shell environment. Never commit keys.
The repo's `.gitignore` already covers `.env`, `.env.local`, and
`.env.*.local`.
