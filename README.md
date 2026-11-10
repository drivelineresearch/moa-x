# MoA-X

<p align="center">
  <img src="docs/moa-x-header.png" alt="MoA-X: independent proposals connect through shared evidence review to one plan and a separate final check" width="100%">
</p>

MoA-X turns a task into an implementation plan using models from different
labs. Independent proposers research the task, reviewers challenge every
proposal, and a recorded synthesis produces one plan with source receipts,
visible disagreements, and an independent final evidence check.

Use it through **Claude Code**, the **local Web UI**, or **Slack**. Repository
context is optional for web and Slack requests; both accept reference uploads.
Each run retains its outputs and a self-contained HTML report.
Completed means the run finished; readiness depends on the evidence. Unsupported
or contradictory decisions remain marked for verification or revision.

## Get started

Install and authenticate the provider CLIs in the [installation guide](docs/install.md).
The default roster needs Codex, Claude Code, AGY, and OpenCode.

Install the packaged skill into a fresh directory. For upgrades and checksum
verification, follow the [skill installation guide](docs/install.md#3-install-as-a-claude-code-skill-primary-path).

```bash
MOA_X_VERSION=v0.7.0
mkdir -p ~/.claude/skills
curl -fsSL "https://github.com/drivelineresearch/moa-x/releases/download/${MOA_X_VERSION}/mixture-of-agents-${MOA_X_VERSION}.tar.gz" \
  | tar -xz -C ~/.claude/skills
python3 ~/.claude/skills/mixture-of-agents/scripts/install_deps.py
```

Inside Claude Code, run `/mixture-of-agents` and describe the task.
The skill previews the brief and asks before dispatching paid model calls.
See [usage](docs/usage.md) for the full workflow and standalone commands.

For the local Web UI:

```bash
git clone https://github.com/drivelineresearch/moa-x.git
cd moa-x
python3 -m venv .venv
.venv/bin/pip install -r requirements-web.txt
MOA_WEBUI_GITHUB_OWNER=your-github-user-or-org \
  .venv/bin/python -m harness.webui
```

Open `http://localhost:7340`. The UI reuses the server user's authenticated
CLIs and has no login; keep the listener on localhost or a trusted network.
Follow [Web UI setup](docs/webui.md) or [Slack setup](docs/slack.md) for those interfaces.

## How it works

| Stage | Default CLI roster |
|---|---|
| Scout | Frozen request and bounded repository reconnaissance |
| Propose | Gemini 4 Carbon, Grok 4.7, GPT-6 Luna |
| Review | DeepSeek V4.1 Flash and Claude Opus 5.5; each sees every proposal |
| Synthesize | GPT-6.1 Sol at `xhigh`, followed by an independent final check |

Rosters are configurable. Web and Slack offer Quick, Balanced, and Thorough
presets; see [models and configuration](docs/config.md) and [architecture](docs/architecture.md).
Optional choices include Fable 5.2, GPT-6.1 Terra, Daybreak Blue, and Haiku 5.5.
Runs commonly take 12–25 minutes; deeper runs and recovery can exceed 45 minutes.

## Explore

- [Documentation](docs/README.md) — choose a guide by task.
- [Contributing](CONTRIBUTING.md) — development, checks, PRs, and contribution priorities.
- [Agent guidance](AGENTS.md) — commands, constraints, and task-specific references.
- [Download v0.7.0](https://github.com/drivelineresearch/moa-x/releases/tag/v0.7.0) and [changelog](CHANGELOG.md) — source, installable skill, and checksums.
- [Security](SECURITY.md) — private vulnerability reporting.

MIT © 2026 Kyle Boddy. See [LICENSE](LICENSE) and
[contributors](https://github.com/drivelineresearch/moa-x/graphs/contributors).
