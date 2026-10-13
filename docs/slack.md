# Chat with MoA-X in Slack

The Slack service connects an existing bot through Socket Mode. Full workspace
members can DM it a question or mention it in a channel where it is a member.
The entire request, interview, launch, progress, cancellation and final delivery
stays in the originating thread. The web dashboard is optional.

## User flow

Send a task, optionally with supported reference files or a public GitHub
repository URL under the configured organization. Luna asks up to three material
questions, offering buttons or typed answers. A complete brief skips questions.
It recommends Quick, Balanced or Thorough depth; those presets share the same
server-side model policy as the browser. Balanced uses three proposals, two reviewers and one
synthesis lane; Qwen is excluded from new launches. Preview the optimized brief and actual
models, adjust depth or individual supported effort levels, then press **Run**.

The request’s inputs and resolved model versions freeze at launch. The existing
single-worker queue prepares references, runs proposals, broadcast review and
synthesis. A status card updates on meaningful progress; its dashboard link is
read-only and specific to that run. The final reply includes the recommendation,
lane success counts and confidence, followed by the full plan and HTML report
as Slack files. Failed/interrupted runs receive an explicit failure reply.

In DMs, ordinary text continues the user’s sole open request. If several are
open, reply in the relevant thread. `new: your task` starts another request;
`status` refreshes its card and `cancel` cancels it. A normal follow-up after
completion starts a new draft with bounded context from the prior plan.

Supported references: PDF; BMP/JPEG/PNG/TIFF/WebP images; CSV/TSV, Markdown,
JSON/YAML, HTML/XML, logs and other supported text formats. Limits: ten files,
25 MiB each; re-sharing an existing reference does not consume another slot.
Images and scanned PDFs use OCR text, not full visual analysis.
DOCX/XLSX, audio and video are unsupported. Reference summaries inform the
interview; the complete bounded extraction packet goes to every ensemble layer.
The worker reuses the interview's verified extraction packet. Cached extraction
is invalidated by changed input bytes or extraction settings.

## Installation

```bash
.venv/bin/pip install -r requirements-slack.txt
# Linux restricted AGY gateway:
# sudo apt install bubblewrap
```

Provision a private environment file, outside the repository:

```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
MOA_SLACK_TEAM_ID=T...
MOA_SLACK_BASE_URL=https://your-dashboard.example/moa-x
MOA_WEBUI_GITHUB_OWNER=your-approved-github-organization
# Optional explicitly approved private repositories (workspace members may use these):
# MOA_SLACK_PRIVATE_REPOS=organization/repo-a,organization/repo-b
```

Retain existing bot scopes and event subscriptions. Required bot scopes include
`chat:write`, `im:history`, `users:read`, `files:read` and `files:write`.
Mentions also require `app_mentions:read`. Subscribe to `message.im`,
`app_mention`, `message.channels`, and `message.groups` for private-channel
threads. Enable the App Home Messages tab and interactivity. The app-level token
needs `connections:write`. Reinstall when adding bot scopes. Socket Mode needs
no public event receiver or interactivity Request URL. Inspect existing receivers
before switching transports; multiple independent receivers can divide events.
Unrelated extra event subscriptions are acknowledged and ignored.

Run `python -m harness.slack` as the same OS user as the existing authenticated
CLI clients, with that environment loaded. A separate systemd service is
recommended. It shares the web UI’s XDG database/data root and never constructs
another Flask application or starts another ensemble worker. Keep the web UI
worker running. The service writes operator connection state to
`$XDG_DATA_HOME/moa-x/slack-status.json`.

## Ownership and recovery

Membership checks reject bots, deactivated users, guests and other workspaces.
Each Slack user maps to a private random backend profile. Only the request
owner can answer, edit, launch or cancel; unrelated channel traffic is ignored.
Public repository access is limited to the configured GitHub organization.
Private repositories require an explicit allowlist, independent of the shared
GitHub credential’s broader permissions. Local filesystem workspaces are not
accepted through Slack. Codex lanes receive a bounded, line-numbered source
packet rather than command access on this host. The packet omits symlinks,
secrets and other sessions, records omitted content, and never implies unshown
files were verified. Claude uses its native restricted file-tool mode inside a
disposable source snapshot that excludes other run sessions. On Linux, AGY uses
a private CLI context and a read-only project mount through bubblewrap, with
web reads allowed and shell, MCP, writes and private CLI-file reads denied.
OpenCode retains its native read-only policy.

Events and action submissions are deduplicated before processing. Draft
versions reject stale controls, stable job IDs recover launches without creating
another run, and delivery state persists in an outbox. Ambiguous message sends
are reconciled against thread metadata; file uploads retain their modern upload
IDs across retries. Message/card delivery and file upload use separate delivery
workers. SDK connection retries are disabled so the durable outbox owns write
reconciliation. Incoming envelopes are acknowledged after local persistence,
with model calls and downloads outside that callback. Cancellation uses a
reserved durable inbox partition after acknowledgment, independent of busy
interviews. Progress reconciliation also runs independently of inbox dispatch.
The owning worker polls cancellation while waiting on silent children,
escalating from termination to forced shutdown after three seconds. Failed
launches return to preview for correction instead of retrying indefinitely.
Choose one repository per run.
Malformed or incomplete AGY/OpenCode JSON receives at most one redispatch;
unavailable accounts are not silently replaced with another model.

Posting in a channel makes the question, results and run link visible to that
channel's readers, just like the uploaded result files. Use a DM for private
questions. Shared run links expose only the plan, report and verified decision
map; cancellation revokes the link. Permanent delivery errors remain in the
operator outbox instead of retrying forever, and `status` can refresh a card.

A Slack-service restart restores queued events, interviews, pending delivery and
job progress. A web-worker restart marks its interrupted active job failed;
the Slack bridge reports the interruption rather than silently buying another
ensemble. The read-only dashboard capability expires after 30 days and grants
access to one job’s status/artifacts, not another profile or launch/cancel APIs.

Credentials remain in the private environment file, not in SQLite or Git.
Socket Mode uses the bot and app-level credentials; user OAuth tokens are not
needed. Existing outbound bot workflows continue using their existing token.

## Validation

Run the offline Slack ownership, durability, upload and cancellation tests plus
the web contracts:

```bash
python3 harness/scripts/test_offline.py
python3 harness/scripts/test_opencode.py
python3 -m unittest harness.slack.tests.test_slack harness.webui.tests.test_webui \
  harness.webui.tests.test_effort_controls_browser harness.webui.tests.test_decision_map_browser
```

A live pilot should complete an interview and a real task-only/uploaded-reference
run, followed by a repository run, and verify final delivery plus the reachable
shared dashboard. Preserve existing service configuration and use a fresh idle
queue check before restarting the web worker.

References: [Socket Mode](https://docs.slack.dev/tools/python-slack-sdk/socket-mode/),
[file-share events](https://docs.slack.dev/reference/events/message/file_share/),
[modern file completion](https://docs.slack.dev/reference/methods/files.completeUploadExternal/).

Public contributions must use example workspace IDs, URLs and credentials.
Keep deployment receipts, raw run reports and operational findings outside
the repository; publish generic reproductions and code fixes only.
