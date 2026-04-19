# Contributing to MoA-X

Thanks for wanting to contribute. This project is built to run inside
**Claude Code** as a skill (`/mixture-of-agents`). Standalone `python3`
works too, but the skill path is what we exercise most. PRs that
improve the non-Claude-Code runner (OpenCode, other harnesses, other
model providers) are very welcome; see below.

## Dev environment

```bash
# 1. Install the three external CLIs (subscription plans, not API keys):
npm i -g @openai/codex          && codex login
npm i -g @google/gemini-cli     && gemini       # interactive OAuth once
# claude CLI: see https://docs.claude.com/en/docs/claude-code/quickstart

# 2. Verify everything is wired up:
python3 harness/scripts/install_deps.py

# 3. Run the offline test suite (no network, no external CLIs):
python3 harness/scripts/test_offline.py
```

New tests must run offline so CI stays credential-free.

## Pull request protocol

1. Fork, branch, PR. Don't push to `main`.
2. One topic per branch.
3. `python3 harness/scripts/test_offline.py` must pass in CI.
4. Describe the *why* in the PR body. A clean diff alone rarely
   tells the whole story for a reference harness.
5. Update `README.md` and/or `CLAUDE.md` if behavior, install, or
   config surface changed.

## Where help is especially welcome

- Making MoA-X run well outside Claude Code: OpenCode, other agent
  harnesses, a plain shell. Right now the Claude Code skill path is
  the best-trodden, so PRs that close the gap for other runners are
  genuinely useful.
- Additional model / provider support beyond `codex`, `claude`, and
  `gemini`. Open an issue first so we can talk through auth and
  adapter shape before you build it; adding a provider touches the
  orchestrator, preflight, and prompt assumptions.
- Adapter robustness: timeouts, subprocess-tree teardown, clearer
  error diagnostics. See `harness/scripts/adapters/`.
- Offline test coverage, especially around config precedence and
  adapter error paths.
- Docs that clear up confusion you hit while getting started.

## License

By contributing you agree that your contributions will be licensed
under the [MIT License](LICENSE).
