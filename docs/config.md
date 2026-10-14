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
| `agy-gemini-pro` | AGY | `gemini-4-argon-high` | proposer, refiner | model-depth selector |
| `grok` | OpenCode | `opencode-go/grok-4.7` | proposer, refiner | provider-managed |
| `codex-luna` | Codex | `gpt-6-luna` | proposer, refiner | adjustable; medium default |
| `codex` | Codex | `gpt-6-astra` | proposer, refiner | adjustable; high default |
| `minimax` | OpenCode | `opencode-go/minimax-m3` | optional proposer/refiner | provider-managed |
| `muse-spark` | OpenCode | `opencode-go/muse-spark-1.3-contributor` | optional proposer/refiner | provider-managed |
| `glm` | OpenCode | `opencode-go/glm-5.3` | proposer, refiner | provider-managed |
| `deepseek-flash` | OpenCode | `opencode-go/deepseek-v4.1-flash` | proposer, refiner | provider-managed |
| `sonnet` | Claude Code | `claude-sonnet-5-5` | proposer, refiner | adjustable; high default |
| `opus` | Claude Code | `claude-opus-5-5` | proposer, refiner, aggregator | adjustable; high default |
| `codex-sol` | Codex | `gpt-6.1-sol` | proposer, refiner, aggregator | adjustable; xhigh default |
| `fable` | Claude Code | `claude-fable-5-1` | aggregator only | fixed xhigh |

Cursor is not a supported harness. GLM 5.3 is the fourth proposer in the
Thorough Web UI preset. DeepSeek V4.1 Flash is the sole curated DeepSeek route
and the default refiner. Qwen, Moonshot and MiMo are excluded from new launches.
Archived manifests preserve their original model-lab identities.

## Default ensemble

```yaml
layers:
  proposers: [agy-gemini-pro, grok, codex-luna]
  refiners: [deepseek-flash, opus]
  aggregator: codex-sol
  skip_refinement: false
```

The browser and Slack share these server-side profiles:

| Mode | Proposers | Refiners | Aggregator |
|---|---|---|---|
| Quick | Gemini 4 Argon `low`; Grok 4.7 | DeepSeek V4.1 Flash | GPT-6.1 Sol `xhigh` |
| Balanced | Gemini 4 Argon `high`; Grok 4.7; GPT-6 Luna `medium` | DeepSeek V4.1 Flash; Opus `high` | GPT-6.1 Sol `xhigh` |
| Thorough | Gemini 4 Argon `high`; Grok 4.7; GPT-6 Astra `high`; GLM 5.3 | DeepSeek V4.1 Flash; Opus `max` | GPT-6.1 Sol `xhigh` |

Gemini Pro enters every mode for early web evidence. Grok enters every mode as
an independent xAI proposer. DeepSeek V4.1 Flash is always a refiner; Opus joins
Balanced and Thorough. The default OpenAI aggregator stays lab-independent
from all recommended refiners.

Optional routes must pass actual planning and broadcast-review qualification.
A catalog listing or synthetic response alone is insufficient.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MOA_PROPOSERS` | `agy-gemini-pro,grok,codex-luna` | comma-separated proposer route ids |
| `MOA_REFINERS` | `deepseek-flash,opus` | comma-separated refiner route ids |
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

AGY encodes depth in the model slug. MoA-X therefore changes
`gemini-4-argon-low` / `gemini-4-argon-high` and never sends a conflicting
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
  refiners: [deepseek-flash, opus, custom-reviewer]
  aggregator: codex-sol
```

The shell shorthand is:

```bash
MOA_PROVIDER_CUSTOM_REVIEWER=opencode:provider/model-id
MOA_REFINERS=deepseek-flash,opus,custom-reviewer
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

## Slack runtime configuration

`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `MOA_SLACK_TEAM_ID`, and
`MOA_SLACK_BASE_URL` belong in a private runtime environment file. The bot
checks full membership in the configured workspace, freezes models and efforts
at launch, and uses the existing queue. Optional `MOA_SLACK_PRIVATE_REPOS`
explicitly grants repositories to workspace members; leave it empty to
permit only public repositories under `MOA_WEBUI_GITHUB_OWNER`. See [Slack](slack.md).

Qwen and Moonshot model families are excluded from launch catalogs, even when
configured under custom aliases. Historical model identities remain available
for archives. MiniMax M3 adds an optional independent proposer/reviewer through
[OpenCode Go](https://opencode.ai/v2/docs/console/go/). Preset choices stay
explicit in `harness/webui/rosters.py`. Both roles passed live qualification;
independent review and final synthesis remain necessary for answer accuracy.
MiMo was excluded after its broadcast reviewer exhausted the qualification
budget. A valid proposal alone is insufficient to qualify an unreliable route.


### Optional model qualification and privacy

MiniMax M3, GLM 5.3 and Muse Spark 1.3 Contributor passed native proposer and
broadcast-review requests. MiniMax's incomplete first review and Muse's
incomplete first proposal recovered through the shared retry budget; GLM passed
both roles on its first invocation. These are scoped compatibility
checks, not a general model-quality benchmark or an availability guarantee.
MiniMax's default total budget is 900 seconds; GLM and Muse use the OpenCode
harness budget (1200 seconds by default). Recommended presets keep their existing
rosters; choose optional routes explicitly in a custom roster.

MiMo and Hy4 Preview each produced a valid proposal but exhausted their review
budgets, so both are excluded from new launches, including custom aliases.
Muse Spark 1.3 Contributor requires
OpenCode's workspace permission for paid endpoints that train on request data.
The `muse-spark` route is optional and labels its training policy in the model
picker; it is available only through workspaces that permit this endpoint. Contributor prompts and completions may be
used for model training; operators must choose this policy knowingly before
submitting repository or uploaded-file context. See the provider's
[Go privacy policy](https://opencode.ai/v2/docs/console/go/#privacy).
