# MoA-X

MoA-X produces evidence-backed implementation plans through independent
proposals, broadcast review, and recorded synthesis. Model-lab diversity helps
reviewers catch blind spots; execution harnesses and model labs are distinct.

## Stack and layout

Python orchestrator, adapters, prompts, and schemas live in `harness/`.
`harness/webui/` serves the buildless Flask UI; `harness/slack/` shares its queue.
Human documentation starts at [docs/README.md](docs/README.md).

## Commands

| Task | Command |
|---|---|
| Credential-free baseline | `python3 harness/scripts/test_offline.py` |
| Provider preflight, when running models | `python3 harness/scripts/install_deps.py` |
| Complete verification | [docs/agent/verification.md](docs/agent/verification.md) |

## Constraints

- Use branch → push → PR → merge; never push directly to main.
- Keep tests offline and credential-free.
- Keep session artifacts, runtime databases, credentials, and private config out of Git.
- Preserve frozen model, effort, and provenance settings in existing runs.
- When guidance conflicts with code or an approved plan, identify the conflict;
  code takes precedence over plans, and plans over this file.

### When editing providers, orchestration, schemas, or evidence

Keep shared recovery bounded, research read-only, and invalid evidence closed.
Read [pipeline guidance](docs/agent/pipeline.md) before changing these contracts.

### When editing web, Slack, reports, or artwork

Preserve effort-control behavior, model-lab identity, and self-contained reports.
Read [interface guidance](docs/agent/interfaces.md) before changing these contracts.

## Read when needed

- [Configuration](docs/config.md) — changing routes, defaults, or precedence.
- [Verification](docs/agent/verification.md) — choosing checks for a change.
- [Releasing](docs/agent/releases.md) — packaging and publishing a GitHub release.
- [Contributing](CONTRIBUTING.md) — development setup and PR expectations.

CLAUDE.md is the guidance source; keep AGENTS.md equivalent plain Markdown,
replacing conditional blocks with “When …” headings. Use ordinary links for
on-demand detail; avoid eager imports.
