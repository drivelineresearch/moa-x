# Install

MoA-X runs inside **Claude Code** as a skill. You can also invoke the
orchestrator directly from a shell. Either way, you need three vendor
CLIs on your PATH, each authenticated. Any auth path the CLI itself
supports is fine. Subscription (OAuth / keychain) is what I run;
API-key auth works too.

## 1. Install the three CLIs

```bash
# OpenAI codex
npm i -g @openai/codex
codex login

# Anthropic Claude Code
# See https://docs.claude.com/en/docs/claude-code/quickstart

# opencode (drives the GLM and Kimi providers)
curl -fsSL https://opencode.ai/install | bash
# or: npm i -g opencode-ai
opencode auth login    # interactive login
# or export a provider key (no login needed):
#   export ZHIPU_API_KEY=...       # GLM
#   export MOONSHOT_API_KEY=...    # Kimi
#   export FIREWORKS_API_KEY=...   # GLM / Kimi via Fireworks
```

The default roster is `codex` + `sonnet` (via `claude`) + `glm` and
`kimi` (both via `opencode`) — four labs: OpenAI, Anthropic, Zhipu,
Moonshot. GLM and Kimi both run on the `opencode` harness; their
model ids are provider/model strings (`zhipuai/glm-5.2`,
`moonshotai/kimi-k2.7-code`, or the Fireworks variants
`fireworks-ai/accounts/fireworks/models/glm-5p2` and
`…/kimi-k2p7-code`).

Subscription auth is the path I use and what the docs lead with. If
you'd rather bill through an API key, each vendor CLI already handles
that on its own; MoA-X defers to whatever auth state the CLI is in
when you invoke it. Better API-billing ergonomics (cost surfacing,
per-layer accounting, a `MOA_MAX_COST` knob) are on the open wish
list. See the PR-wanted section of the top-level README.

### Optional: Cursor CLI (extra provider)

The Cursor CLI is optional. Its binary is `cursor-agent` (older
installs) or `agent` (newer, renamed). It's a single binary that
routes to OpenAI, Anthropic, Google, xAI, and Moonshot models, plus
Cursor's own `composer-2.5` — useful if you want an extra lane in the
ensemble or want to consolidate around one CLI for billing.

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login    # subscription
# or
export CURSOR_API_KEY=...    # API-billed
```

Then add a `providers:` block to `harness/config.yaml`. See
`harness/config.example.yaml` for examples. The built-in `composer`
provider (harness `cursor`, model `composer-2.5`) is available once
the CLI is installed.

## 2. Verify

```bash
python3 harness/scripts/install_deps.py
```

The script checks each CLI's version and auth state. It does not
install anything or prompt for credentials. If something's missing,
it prints the exact `login` command to run yourself.

## 3. Install as a Claude Code skill (primary path)

The main way to run MoA-X is `/mixture-of-agents` inside Claude Code.
Drop the `harness/` directory into your skills folder:

```bash
# From a clone of this repo:
cp -r harness ~/.claude/skills/mixture-of-agents
```

Restart Claude Code. `/mixture-of-agents` should now autocomplete.
See [`docs/usage.md`](usage.md) for what happens next.

## 4. Or run standalone (secondary)

The Python orchestrator works outside Claude Code too. You just don't
get the scout-brief and aggregation steps for free:

```bash
python3 harness/scripts/run_moa.py \
  --scout-brief path/to/your-scout-brief.json
```

You'll need to write the scout brief JSON yourself and read
`.moa/<session>/synthesis-input.md` afterward to assemble the final
plan. See [`docs/usage.md`](usage.md#running-standalone) for the
format and the manual aggregation step.

PRs that improve the standalone path, add support for OpenCode or
other agent harnesses, or make API-billed auth first-class are all
welcome. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Offline tests

```bash
python3 harness/scripts/test_offline.py
```

No network, no external CLIs. All tests should pass. CI runs the
same thing on every push.
