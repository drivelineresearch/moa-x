# Pipeline changes

Read this before editing providers, orchestration, schemas, or evidence handling.

## Model routes

Recommend reviewers from labs independent of the aggregator; justify default
reviewer changes in the PR. Harness identity alone does not prove lab diversity.
Keep stable provider IDs compatible and record the resolved model for each run.
Use [configuration](../config.md) for defaults, precedence, and provider overrides.

OpenCode models use provider/model IDs. Its adapter selects V1 or V2 behavior
from the installed major version; V2 uses a private server, NDJSON, an explicit
read-only agent, and a disposable repository snapshot. Google routes use AGY,
live catalog probes, plan mode, and sandboxing. Optional native Muse and DeepSeek
contracts live in [native harnesses](../native-harnesses.md).

## Recovery and isolation

Automatic recovery belongs in the shared orchestrator path for every provider
and role, including self-MoA and synthesis. Keep one total deadline per provider,
at most three research calls with jittered 5/15-second backoff, and one tool-free
structure repair capped at 240 seconds. Workers must not add retry loops.
Permanent auth/access/billing/config failures and workspace mutations never retry.

Retain root-shaped invalid objects for strict validation. Keep AGY's schema file
inside its isolated workspace. AGY/Gemini repair uses tool-free Claude Opus;
OpenCode prefers authenticated native Opus with its original backend as fallback.
A helper rejected before generating payload may switch once for auth/access or
local CLI/model compatibility within the same repair window. Generated output,
evidence failures, safety refusals, and billing errors never trigger that switch.
Record every attempt's log, harness, model, timing, and outcome.

Typed internal errors inside HTTP 200 responses remain retryable; mechanical
plan-boundary recovery must preserve source literals. See the full
[recovery and isolation contract](../architecture.md#provider-recovery).

## Evidence and decisions

Reject invalid proposer evidence before persistence. New decisions use the v2
final-plan schema; archives retain v1. Read the [evidence contract](../evidence-pipeline.md)
before changing claim selection, receipts, final audit, checkpoints, or readiness.
Run the relevant suites in [verification](verification.md).
