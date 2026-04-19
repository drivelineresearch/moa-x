# Security Policy

## Reporting a vulnerability

**Do not file a public GitHub issue for security reports.**

If you believe you've found a security vulnerability in MoA-X, please
report it privately by opening a
[GitHub Security Advisory](https://github.com/drivelineresearch/moa-x/security/advisories/new)
on this repository. Private advisories allow coordinated disclosure.

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce
- Affected version / commit SHA
- Any suggested fix, if you have one

We'll acknowledge within a reasonable window, work with you on a fix,
and credit you in the advisory unless you prefer otherwise.

## Scope

MoA-X is a thin orchestrator around three external subscription CLIs
(`codex`, `gemini`, `claude`). Reports in scope:

- Command injection, path traversal, or similar in the orchestrator or
  adapters (`harness/scripts/`)
- Schema-validation bypasses that let a proposer or refiner smuggle
  unintended data through to the aggregator
- Subprocess-teardown failures that leak file descriptors, processes,
  or tmpdirs across MoA runs
- Any path by which the harness writes to disk outside the session
  directory against the read-only discipline contract

Reports out of scope (please take these upstream):

- Vulnerabilities in the codex / gemini / claude CLIs themselves
- Vulnerabilities in the underlying LLM APIs
- Issues specific to subscription auth state managed by those CLIs
