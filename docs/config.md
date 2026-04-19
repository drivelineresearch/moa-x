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
  codex:
    model: gpt-5.4
    effort: xhigh
  gemini:
    model: gemini-2.5-pro
  claude:
    model: claude-sonnet-4-6
layers:
  proposers: [codex, gemini, sonnet]
  refiners: [codex, gemini]
```

## Knobs

| Variable | Default | What it does |
|---|---|---|
| `MOA_CODEX_BIN` | `codex` | Path or name of the codex binary. Set this if codex isn't on PATH or lives somewhere non-standard. |
| `MOA_GEMINI_BIN` | `gemini` | Same for gemini. |
| `MOA_CLAUDE_BIN` | `claude` | Same for claude. |
| `MOA_CODEX_MODEL` | `gpt-5.4` | Codex model id. |
| `MOA_CODEX_EFFORT` | `high` | One of `low`, `medium`, `high`, `xhigh`. Higher = better, slower. Default `--codex-timeout` scales with this. |
| `MOA_GEMINI_MODEL` | `gemini-2.5-pro` | Gemini model id. `gemini-3.1-pro-preview` is available but flaky. |
| `MOA_SONNET_MODEL` | `claude-sonnet-4-6` | Model for the sonnet proposer (the `claude` CLI in sonnet mode). |
| `MOA_CODEX_TIMEOUT` | effort-scaled | Wall-clock cap for codex calls. xhigh=1500s, high=1200s, medium/low=900s. |
| `MOA_GEMINI_TIMEOUT` | `1200` | Wall-clock cap for gemini calls, in seconds. |
| `MOA_SONNET_TIMEOUT` | `1200` | Wall-clock cap for sonnet calls, in seconds. |
| `MOA_PROPOSERS` | `codex,gemini,sonnet` | Comma-separated subset of proposers to spawn. |
| `MOA_REFINERS` | `codex,gemini` | Comma-separated subset of refiners. Sonnet is not a valid refiner. |
| `MOA_SKIP_LAYER2` | unset | Set to `1` to skip the refinement layer entirely. |

CLI flag equivalents exist for every row here. Run
`python3 harness/scripts/run_moa.py --help` to see them.

## Supported providers

Hard-capped to `codex`, `claude-code`, and `gemini`. This is an
architectural constraint, not a TODO. See
[`docs/architecture.md`](architecture.md#why-these-three) for the
rationale. PRs that add providers need a design discussion up front;
see [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Secrets

Put secrets in `.env` or your shell environment. Never commit keys.
The repo's `.gitignore` already covers `.env`, `.env.local`, and
`.env.*.local`.
