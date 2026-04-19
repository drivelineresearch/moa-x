# Contributing to MoA-X

Thanks for wanting to contribute. This project is built to run inside
**Claude Code** as a skill (`/mixture-of-agents`). Standalone `python3`
works too, but the skill path is what we exercise most. PRs that
improve the non-Claude-Code runner (OpenCode, other harnesses), add
API-billing ergonomics, or bring in other model providers (including
Chinese-lab frontier models) are especially welcome. The top-level
README has a more specific wishlist.

## Dev environment

```bash
# 1. Install the three external CLIs. Any auth the CLI itself supports
#    works — subscription OAuth (what I use) or API key both fine.
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

- First-class API-billing support. The adapters currently assume
  subscription auth. Making `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `GEMINI_API_KEY` a supported, documented path (with cost surfacing
  in the manifest and a `MOA_MAX_COST` ceiling) is a priority.
- Running outside Claude Code: OpenCode, aider, codex-as-harness,
  roo/cline/continue, or a plain shell. The Claude Code skill path
  is the best-trodden today.
- Chinese-lab models: DeepSeek, Qwen, Kimi, GLM, MiniMax. The
  cross-lab diversity argument is weaker when all three providers
  are US-based; a Chinese proposer would test and strengthen it.
- More providers generally: xAI Grok, Mistral, anything with a
  credible coding story. Open an issue first so we can talk through
  auth and adapter shape; adding a provider touches the orchestrator,
  preflight, and prompt assumptions.
- Adapter robustness: timeouts, subprocess-tree teardown, clearer
  error diagnostics. See `harness/scripts/adapters/`.
- Offline test coverage, especially around config precedence and
  adapter error paths.
- Docs that clear up confusion you hit while getting started.

## How to submit a PR

You don't have to fork. If you have push access to the repo, just
push a topic branch and open a PR against `main`. If you don't,
fork the repo, push your branch there, and open a cross-repo PR.
Either works from the reviewer's side; forks are the right default
for external contributors who want the work in their own namespace.

## License

By contributing you agree that your contributions will be licensed
under the [MIT License](LICENSE).
