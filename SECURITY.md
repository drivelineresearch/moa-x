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

MoA-X is a thin orchestrator around authenticated external vendor CLIs
(`codex`, `claude`, `opencode`, `cursor`, `agy`) and includes an optional
local Flask control room. Reports in scope:

- Command injection, path traversal, or similar in the orchestrator or
  adapters (`harness/scripts/`)
- Schema-validation bypasses that let a proposer or refiner smuggle
  unintended data through to the aggregator
- Subprocess-teardown failures that leak file descriptors, processes,
  or tmpdirs across MoA runs
- Any path by which the harness writes to disk outside the session
  directory against the read-only discipline contract
- Web UI command injection, path traversal, upload boundary failures,
  cross-origin state changes, artifact disclosure beyond the configured
  workspace/data roots, or GitHub allowlist bypasses

The Web UI has no login, authorization layer, or tenant isolation. Its safe
default is loopback-only. Direct public-internet exposure is unsupported and
is not a vulnerability by itself; reports showing that a remote browser can
access a server deliberately bound to a public interface without a protective
proxy are out of scope.

Uploaded PDF/text contents are copied into the run and embedded in prompts for
every selected provider. They can also appear in local logs and the
self-contained HTML report. Treat attachments as provider-bound data and do
not upload secrets or regulated material unless every selected provider and
the machine hosting the Web UI are approved to process it.

Reports out of scope (please take these upstream):

- Vulnerabilities in the codex / claude / opencode / cursor CLIs themselves
- Vulnerabilities in the underlying LLM APIs
- Issues specific to auth state (subscription or API) managed by those CLIs
