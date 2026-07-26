# MoA-X local Web UI — execution plan

Status: implemented on `codex/flask-webui`
Updated: 2026-07-25

## Product

Build a local mission-control UI for MoA-X that can:

- start a task-only run or a run against an allowlisted GitHub repository;
- create and review a scout brief;
- choose proposer, refiner, and aggregator models;
- use the CLI accounts already authenticated on the machine;
- show queued, active, partial, failed, and completed runs;
- summarize progress and expose raw logs when useful;
- cancel or redispatch a run;
- browse old `.moa` sessions and final reports;
- inspect, configure, install, and recheck provider CLIs;
- support lightweight browser profiles with no login.

The reviewed machine can opt into `0.0.0.0` as requested. The public default
must remain `127.0.0.1`; with no login, an all-interface bind is a
trusted-network deployment choice, not an internet-facing service.

## Architecture

```text
Browser
  ↕ Flask JSON + SSE/polling
Flask control plane
  ↕ SQLite metadata / queue
Local worker
  ↕ subprocess phases
run_moa.py
  ↕ existing adapters and authenticated CLI state
Codex · Claude · OpenCode · Cursor · AGY
  ↕
repo/.moa/<session> artifacts and report
```

Keep the current CLI as the source of execution truth. The Web UI launches it
as a subprocess; a Flask request never runs a model directly.

### Storage

- SQLite: browser profiles, managed GitHub workspace pointers, jobs, queue
  state, timestamps, upload metadata, and artifact paths.
- Existing `.moa/<session>/`: scout, payloads, logs, manifests, final plan,
  report, and immutable per-run upload snapshots.
- XDG data storage: durable uploads and shallow
  `<configured-owner>/<repo>` GitHub workspaces.
- Browser `localStorage`: generated profile ID, display name, drafts, theme,
  and UI preferences only.
- Existing CLI auth directories and environment: credentials. Never copy
  OAuth tokens or API keys into SQLite or browser storage.

### Concurrency

Run one MoA job at a time per OS account and queue the rest. This preserves the
existing global CLI-auth lock and makes multiple browser profiles predictable.

## Local-account provider behavior

The UI will probe and use the machine's existing state:

| Harness | Local account source |
|---|---|
| Codex | current `codex login` state / configured API login |
| Claude | current Claude Code login or existing `ANTHROPIC_API_KEY` |
| OpenCode | `opencode auth` state and existing provider-key environment |
| Cursor | current Cursor CLI login or existing `CURSOR_API_KEY` |
| AGY | current Antigravity account and available model catalog |

Health checks report installed, version, authenticated, model availability,
and actionable failures.

### Google path

- Use AGY as the primary consumer/subscription Google route on this machine.
- Require `--mode plan` and `--sandbox`; never retry without plan mode.
- Put large MoA prompts in the session directory and pass AGY a short
  instruction to read them.
- Keep the existing before/after workspace guard. A local smoke test confirmed
  the target repository stayed unchanged, although AGY wrote a managed scratch
  artifact and overstated what it had done.
- Add Google providers as opt-in lanes first. Do not change the
  lab-independent default roster until they have real canary history.

## UI direction

Light editorial mission control:

- pure white canvas;
- a repository-safe system sans-serif stack for headings and body text;
- goldenrod `#FFA300` as the primary accent;
- black and Mine Shaft typography;
- horizontal rules and spacing instead of shadows;
- 8–12px radii, never pill buttons;
- compact Vercel/shadcn-like controls without generic gray-card styling;
- responsive navigation and grids at 1280px, 768px, and 480px;
- reduced-motion support and full keyboard focus treatment.

Core screens:

1. **Activity** — active pipeline, queue, recent runs, provider health.
2. **New run** — repository, spec, scout, roster, review, launch.
3. **Run detail** — phase status, agent cards, summarized progress, logs,
   cancel/redispatch, artifacts, and final report.
4. **Providers** — account health, versions, models, install/auth guidance,
   recheck, and optional explicit smoke tests.
5. **History** — indexed SQLite jobs plus imported legacy `.moa` sessions.

Realtime token traces are optional. The UI should always show honest lifecycle
events and useful summaries. Raw provider output is available behind a detail
view. Cheap summarizer models can be added later, but the first release should
use deterministic status summaries so monitoring does not create hidden cost.

## Build tracks

### 1. Provider and orchestration layer

- add AGY and Gemini adapters;
- add opt-in Google provider definitions and preflight checks;
- preserve local CLI auth;
- emit clear provider/layer progress lines;
- preserve phase split, redispatch, workspace guard, manifests, and reports;
- keep offline fixtures focused.

### 2. Flask backend

- app factory and production local launcher;
- SQLite migrations and repository/session index;
- browser profile and roster preset storage;
- job queue and worker;
- explicit subprocess argv, cancellation, and restart reconciliation;
- provider health/model APIs;
- SSE when available with polling fallback.

### 3. Frontend

- polished responsive dashboard;
- job wizard and roster selection;
- provider/account workbench;
- active/queued/history views;
- agent status, summarized progress, expandable logs, and artifacts;
- accessible mobile navigation and reduced motion.

### 4. Integration and hosting

- install optional Web UI dependencies;
- run existing offline tests plus a small backend/UI smoke suite;
- launch on a stable local port, using `0.0.0.0` only as an explicit
  trusted-LAN override;
- verify health, job creation, provider discovery, history, and responsive
  rendering;
- leave the process running and report the local/LAN URL.

## Definition of done

- Existing offline tests pass.
- Web UI starts from one documented command.
- Current Codex, Claude, OpenCode, and AGY accounts show usable.
- AGY Pro and Flash availability reflects live account probes.
- A user can create, queue, watch, cancel, and reopen a run.
- Old `.moa` runs are visible.
- No credentials are stored in browser storage or SQLite.
- The app is attractive and usable on desktop and reasonably usable on mobile.
- The reviewed server may run on `0.0.0.0`; public installs default to
  loopback.

## Primary references

- [Flask application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
- [Flask deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/)
- [MDN Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
- [Antigravity CLI documentation](https://antigravity.google/docs/cli/getting-started)
