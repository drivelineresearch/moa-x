# Contributing to MoA-X

Thanks for wanting to contribute. This project is built to run inside
**Claude Code** as a skill (`/mixture-of-agents`). Standalone `python3`
works too, but the skill path is what we exercise most. PRs that complete
the standalone scout workflow, add normalized usage/cost telemetry, strengthen
provider capability checks, or harden adapters are especially welcome. The
top-level README
has the canonical, more specific priority list.

## Dev environment

```bash
# 1. Install the external CLIs. Any auth the CLI itself supports
#    works — subscription OAuth (what I use) or API key both fine.
npm i -g @openai/codex                            && codex login
curl -fsSL https://opencode.ai/install | bash     && opencode auth login  # GLM + Kimi
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
5. Update `README.md` and/or `CLAUDE.md` (agents also read it via
   `AGENTS.md`) if behavior, install, or config surface changed.

## Where help is especially welcome

- Complete the standalone path: generate the Layer 0 scout brief from a raw
  spec and drive the existing Layer 1/2 checkpoints plus recorded Layer 3 from
  one command.
- Normalize usage, quota, and cost metadata across subscription and API-key
  auth. Auth already works through the underlying CLIs; trustworthy accounting
  and pre-dispatch budget controls do not.
- Extend workspace-integrity assurance beyond the existing Git-visible digest,
  especially for ignored/untracked paths that are intentionally excluded from
  the current dirty-safe check.
- Add tested provider recipes for models such as DeepSeek, MiniMax, Grok, and
  Mistral. Include config, credential preflight, parser fixtures, and smoke-test
  evidence; Qwen Token Plan is already built in.
- Harden CLI compatibility and recovery: capability probes, real-world error
  fixtures, clearer diagnostics, and resume paths that preserve successful
  work. See `harness/scripts/adapters/` and `harness/scripts/run_moa.py`.
- Expand offline coverage around config precedence, adapter error paths,
  workspace integrity, and checkpoint recovery.
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
