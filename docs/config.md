# Configuration

MoA-X resolves configuration in this order:

1. runner CLI flags;
2. `MOA_*` process environment variables;
3. the repository `.env`;
4. `harness/config.yaml`;
5. built-in defaults.

Never commit `.env`, provider keys, or generated `harness/config.yaml`.

## Curated routes

| Route | Execution harness | Model | Roles | Effort |
|---|---|---|---|---|
| `agy-gemini-pro` | AGY | `gemini-3.1-pro-high` | proposer, refiner | model-depth selector |
| `grok` | OpenCode | `opencode-go/grok-4.5` | proposer, refiner | provider-managed |
| `codex-luna` | Codex | `gpt-5.6-luna` | proposer, refiner | adjustable; medium default |
| `codex` | Codex | `gpt-5.6-terra` | proposer, refiner | adjustable; high default |
| `qwen` | OpenCode | `qwen-token-plan/qwen3.8-max-preview` | proposer, refiner | configured variant |
| `qwen-opencode` | OpenCode | `opencode-go/qwen3.7-max` | proposer, refiner | provider-managed |
| `kimi` | OpenCode | `opencode-go/kimi-k3` | proposer, refiner | provider-managed |
| `sonnet` | Claude Code | `claude-sonnet-5` | proposer, refiner | adjustable; high default |
| `opus` | Claude Code | `claude-opus-5` | proposer, refiner, aggregator | adjustable; high default |
| `codex-sol` | Codex | `gpt-5.6-sol` | proposer, refiner, aggregator | adjustable; xhigh default |
| `fable` | Claude Code | `claude-fable-5` | aggregator only | fixed xhigh |

Cursor is not a supported harness. GLM and DeepSeek are not curated launch
routes after repeated incomplete or schema-invalid live outputs. Archived
manifests still preserve and display their original model-lab identity.

## Default ensemble

```yaml
layers:
  proposers: [agy-gemini-pro, grok, codex-luna]
  refiners: [qwen, kimi, opus]
  aggregator: codex-sol
  skip_refinement: false
```

The Web UI's optimized profiles use:

| Mode | Proposers | Refiners | Aggregator |
|---|---|---|---|
| Quick | Gemini Pro `low`; Grok 4.5 | Kimi K3 | GPT-5.6 Sol `xhigh` |
| Balanced | Gemini Pro `high`; Grok 4.5; GPT-5.6 Luna `medium` | Qwen 3.8; Kimi K3; Opus `high` | GPT-5.6 Sol `xhigh` |
| Thorough | Gemini Pro `high`; Grok 4.5; GPT-5.6 Luna `medium`; GPT-5.6 Terra `high` | Qwen 3.8; Kimi K3; Opus `max` | GPT-5.6 Sol `xhigh` |

Gemini Pro enters every mode for early web evidence. Grok enters every mode as
an independent xAI proposer. Kimi is always a refiner; Opus joins Balanced and
Thorough. The default OpenAI aggregator stays lab-independent from all
recommended refiners.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MOA_PROPOSERS` | `agy-gemini-pro,grok,codex-luna` | comma-separated proposer route ids |
| `MOA_REFINERS` | `qwen,kimi,opus` | comma-separated refiner route ids |
| `MOA_AGGREGATOR` | `codex-sol` | aggregator route id |
| `MOA_SKIP_LAYER2` | false | skip broadcast refinement |
| `MOA_<NAME>_MODEL` | route default | per-route model override |
| `MOA_<NAME>_TIMEOUT` | harness default | per-route timeout |
| `MOA_<NAME>_EFFORT` | route default | native effort/variant override |
| `MOA_CODEX_BIN` | `codex` | Codex executable |
| `MOA_CLAUDE_BIN` | `claude` | Claude executable |
| `MOA_OPENCODE_BIN` | `opencode` | OpenCode executable |
| `MOA_AGY_BIN` | `agy` | AGY executable |
| `MOA_CODEX_TIMEOUT` | effort-derived | Codex wall-clock cap |
| `MOA_SONNET_TIMEOUT` | `1200` | Claude wall-clock cap |
| `MOA_OPENCODE_TIMEOUT` | Claude timeout | OpenCode wall-clock cap |
| `MOA_AGY_TIMEOUT` | Claude timeout | AGY wall-clock cap |
| `QWEN_TOKEN_PLAN_API_KEY` | none | required dedicated `sk-sp-...` Qwen key |

AGY encodes depth in the model slug. MoA-X therefore changes
`gemini-3.1-pro-low` / `gemini-3.1-pro-high` and never sends a conflicting
effort flag.

## User-defined routes

Supported harnesses are `codex`, `claude`, `opencode`, `agy`, and `gemini`.
The loader rejects any other harness before dispatch.

```yaml
providers:
  custom-reviewer:
    harness: opencode
    model: provider/model-id
    timeout: 600
    effort: high

layers:
  proposers: [agy-gemini-pro, grok, codex-luna]
  refiners: [qwen, kimi, opus, custom-reviewer]
  aggregator: codex-sol
```

The shell shorthand is:

```bash
MOA_PROVIDER_CUSTOM_REVIEWER=opencode:provider/model-id
MOA_REFINERS=qwen,kimi,opus,custom-reviewer
```

Names must be lowercase, dash-separated, and at most 32 characters so they fit
the output schemas. Model-lab identity is inferred from known model prefixes;
unknown custom routes use the neutral independent-lab visual.

## Aggregator alternatives

`opus` and `fable` may replace `codex-sol`. Fable is rejected from proposer
and refiner lists by both the config loader and Web API. The browser also
shows a password warning before selecting it because the 1M-thinking route
can consume extreme quota. The password is only a deliberate speed bump, not
a security boundary.

## Effort UI invariant

A roster row may say `Adjust reasoning effort` or `Adjust model depth` only
when selecting it reveals an enabled slider. Fixed routes say
`Fixed <level> effort`; routes without a controllable setting say
`Provider-managed effort`. `test_effort_controls_browser.py` enforces this
contract across all roles and optimized profiles.
