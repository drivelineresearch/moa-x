# Install

MoA-X runs inside **Claude Code** as a skill. You can also invoke the
orchestrator directly from a shell. Either way, you need three vendor
CLIs on your PATH, each authenticated against its subscription plan.

## 1. Install the three CLIs

```bash
# OpenAI codex
npm i -g @openai/codex
codex login

# Google gemini
npm i -g @google/gemini-cli
gemini            # run interactively once to complete OAuth

# Anthropic Claude Code
# See https://docs.claude.com/en/docs/claude-code/quickstart
```

Subscription plans only; no API keys. If your shell has
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` set for
other tools, that's fine. MoA-X ignores them.

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

PRs that improve the standalone path, or add support for OpenCode or
other agent harnesses, are welcome. See
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Offline tests

```bash
python3 harness/scripts/test_offline.py
```

No network, no external CLIs. All 23 tests should pass. CI runs the
same thing on every push.
