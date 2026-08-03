# Local Web UI

The Flask control room is a local-first interface over the existing
`run_moa.py` phases. It queues one run at a time, streams lifecycle events,
shows an evidence-weighted living decision map, reports provider health, and
indexes completed and historical `.moa` sessions.
The CLI remains the execution source of truth.

## Start it

```bash
git clone https://github.com/drivelineresearch/moa-x.git
cd moa-x
python3 -m venv .venv
.venv/bin/pip install -r requirements-web.txt
MOA_WEBUI_GITHUB_OWNER=your-github-user-or-org \
  .venv/bin/python -m harness.webui
```

Open `http://localhost:7340`. The default bind is `127.0.0.1:7340`; override
it with `MOA_WEBUI_HOST` and `MOA_WEBUI_PORT`. Set
`MOA_WEBUI_HOST=0.0.0.0` only when you deliberately want trusted-LAN access.
There is intentionally no login, so never expose the listener directly to the
public internet.

When Waitress is installed, the HTTP/SSE layer uses a CPU-derived thread pool
with a minimum of 8 and maximum of 32 threads; override it with
`MOA_WEBUI_THREADS`. This request pool remains separate from the background
OCR executor, so long-lived browser streams do not consume the page workers.
Flask's development fallback also runs threaded, but it is not the production
server.

No Node.js or frontend build tool is required. HTML, CSS, JavaScript, the
operating-system font stack, and project-owned image assets are already
versioned under `harness/webui/`. Gotham is preferred automatically when it
is installed on the viewing workstation but is not redistributed. A server
may also expose locally licensed Gotham Office Regular and Bold files from
`$XDG_DATA_HOME/moa-x/fonts/` to remote browsers; see
[`docs/assets.md`](assets.md) for the exact filenames and licensing boundary.
Waitress is installed from `requirements-web.txt` and selected automatically;
Flask's development server is only the fallback when Waitress is unavailable.

### Model depth and effort

The route chooser only exposes an adjustable control when the underlying CLI
can honor it safely. Codex and Claude use their native reasoning-effort flags.
AGY Gemini Pro selects depth by changing the model suffix (for example,
`gemini-3.1-pro-low` to `gemini-3.1-pro-high`), so the UI sends a model
variant and never sends a conflicting AGY `--effort` flag. OpenCode variants
remain configured by their named route/model id.

The roster has a strict presentation contract: any row that says **Adjust**
must render an enabled slider whenever that route is selected, in the
proposer, refiner, and aggregator steps alike. The same capability decision
drives both the row copy and control rendering. Routes without a safe runtime
control never imply adjustability: an explicit configured value is labeled
**Fixed _level_ effort**, while routes whose provider owns the setting are
labeled **Provider-managed effort**. Route metadata and the slider container
use separate DOM attributes so a visibility sync cannot accidentally target
the route input instead of its control. A runtime contract check disables only
the malformed route and logs an error, and the browser-level CI test exercises
the selected, unselected, fixed, and provider-managed cases in all three
roster layers.

The three recommended depth modes deliberately keep Google search coverage
and the strongest synthesis route in every run:

| Mode | Proposers | Broadcast refiners | Aggregator |
|---|---|---|---|
| Quick | Gemini Pro `low`; Grok 4.5 | DeepSeek V4 Pro | GPT-5.6 Sol `xhigh` |
| Balanced | Gemini Pro `high`; Grok 4.5; GPT-5.6 Luna `medium` | Qwen 3.8; DeepSeek V4 Pro; Opus `high` | GPT-5.6 Sol `xhigh` |
| Thorough | Gemini Pro `high`; Grok 4.5; GPT-5.6 Terra `high`; GLM 5.2 | Qwen 3.8; DeepSeek V4 Pro; Opus `max` | GPT-5.6 Sol `xhigh` |

Gemini Pro stays in the proposal layer because it contributes a
Google-native research lane before the ensemble converges. Grok contributes
the xAI proposal lane, Luna joins at Balanced, and Terra joins at Thorough.
DeepSeek V4 Pro is the Quick reviewer and remains in the full broadcast-review
set alongside Qwen and Opus at higher depths. Every named default lane comes
from a distinct lab within its own layer, and all recommended refiners are
lab-independent from the OpenAI aggregator. GPT-5.6 Sol remains the selected
`xhigh` aggregator in every recommended mode.

Kimi K3 remains visible on the OpenCode provider page only for archived
provenance. New launches are disabled because OpenCode Go repeatedly rejects
the route before inference with `Provider rate limit exceeded`, including when
balance fallback is enabled.

Claude Opus 5 is available as a normal aggregator alternative. Fable 5 is
available as an aggregator-only alternative. Selecting Fable opens a
quota warning and requires the shared acknowledgement phrase before the radio
selection is applied. This is intentionally only a warning shot—the phrase is
present in browser JavaScript and is not an authentication or authorization
boundary. Fable never appears in proposer or refiner lists.

Whenever a route is selected—by initial defaults, a depth-mode change, or a
manual click—the corresponding model-lab accordion opens automatically so the
checked model and its effort control remain visible.

Every roster group, review node, live lane, health-card portrait, and archived
run resolves the same `lab_id` and lab-specific art. The CLI transport is
shown as execution metadata but never determines the character identity.

### Prompt coach

The optional **Strengthen with AI** flow remains deliberately small rather
than starting a second ensemble. GPT-5.6 Luna is the fast primary editor;
Gemini 3.1 Pro is the bounded fallback. The coach sees only the user's draft,
source type, attachment count, and selected planning depth—not repository or
attachment contents. It asks at most three material questions, then returns a
preview that the user may apply or discard.

Both primary and fallback responses are locally schema-validated before the UI
uses them. The selected Quick/Balanced/Thorough mode is included in analysis
and finalization so a Quick brief does not accidentally grow into a
Thorough-scale mission. A future improvement should be evaluated with saved,
de-identified before/after briefs rather than adding more models by default;
the current latency and predictability are strengths worth preserving.

The worker inherits the server process's `HOME`, `PATH`, environment, CLI
keychains, and authenticated accounts. It never copies API keys or OAuth
tokens into the browser or SQLite.

## Provider health alerts

When the Web UI worker starts, it probes every configured CLI account
immediately and repeats the checks hourly. A healthy-to-unhealthy transition
creates a Sentry error grouped by provider, including the CLI version, status,
and probe detail. Repeated hourly failures remain one Sentry issue; after a
provider recovers, a later regression raises a new event.

Install the Web UI requirements and set the project DSN without committing it:

```bash
.venv/bin/pip install -r requirements-web.txt
printf '%s\n' 'MOA_SENTRY_DSN=https://public-key@org.ingest.sentry.io/project' \
  >> .env
```

The monitor uses the Web UI service's OS account so it sees the same `HOME`,
`PATH`, keychain, and CLI sessions as real runs. Override the one-hour interval
with `MOA_PROVIDER_MONITOR_INTERVAL_SECONDS`; set
`MOA_SENTRY_ENVIRONMENT` to distinguish machines. Sentry notifications are
controlled by the project's issue-alert rules; enable an alert for new issues
where the `component` tag equals `provider-health`.

The same SDK also captures unhandled Flask exceptions plus handled
infrastructure failures that would otherwise only become a 5xx response or
local log entry: worker crashes and nonzero run exits, runs interrupted by a
server restart, provider/model catalog failures, prompt-coach outages, GitHub
listing and clone failures, and failures in the provider-monitor loop. Expected
4xx input validation, cancellations, unreadable user attachments, and missing
files are deliberately excluded. Request bodies, local variables, and default
PII are disabled in Sentry.

## Storage and profiles

The default data root is `$XDG_DATA_HOME/moa-x`, or
`~/.local/share/moa-x` when `XDG_DATA_HOME` is unset:

```text
moa-x/
├── fonts/               # optional, local-only Gotham Office runtime files
├── webui.sqlite3        # profiles, jobs, queue state, and events
├── uploads/             # durable uploaded source files
└── workspaces/
    ├── brief/            # isolated repository-free run workspaces
    └── github/           # managed GitHub clones
```

Each browser creates a local profile ID in `localStorage`. Its display name
and preferences are also persisted server-side so they survive page reloads.
The server binds that profile to a high-entropy browser capability stored in
an HttpOnly, SameSite cookie; only that capability may list, open, stream,
download, cancel, or redispatch the profile's runs and uploads. Job ownership
is always derived from the cookie rather than browser-supplied profile IDs or
query parameters. A pre-upgrade profile is claimed by the first browser that
returns with its matching local profile ID. Clearing both browser storage and
cookies intentionally loses access to that private profile.

This protects one browser profile from other browsers using the same Web UI,
but it is not OS-level or internet-grade multi-tenant isolation. The server
process and operating-system account can still read SQLite, workspaces,
uploads, and `.moa` artifacts directly. Use separate OS accounts or machines
when operators must not trust the host administrator.

Run payloads, logs, manifests, and final artifacts remain in
`.moa/<session>/` under the managed task or GitHub workspace. A **Task only**
run uses a private per-job workspace under `workspaces/brief/`, so a repository
is never required. Existing repository sessions can still be imported into
the SQLite index without moving their artifacts.

## Sharing a final report

Completed runs can create a **Share report** link from the result controls or
the header of the private report artifact itself. It is a high-entropy,
revocable bearer link that exposes only the self-contained
`report.html`—not the run page, logs, source uploads, manifests, or other
artifacts. Creating a new link revokes the prior one; the owner can also revoke
the active link immediately. Shared reports are served with no-store and
no-index headers, but anyone who receives the link can read it, so do not share
reports containing material that the recipient is not permitted to process.

## Context sources

The new-run flow offers two explicit choices:

- **Task only** — create an isolated managed workspace for the brief and any
  attachments. This is the default.
- **GitHub repo** — clone a repository owned by the one GitHub user or
  organization configured in `MOA_WEBUI_GITHUB_OWNER`, using this machine's
  authenticated `gh` account.

Changing planning depth changes the roster and supported reasoning effort; it
does not silently add a repository or provider.

## Reference uploads

A launch may include up to 10 files, 25 MB per file. Uploads are stored under
the XDG data root with generated names and SHA-256 metadata. At launch, PDF,
Markdown, text, CSV, TSV, JSON, YAML, XML, HTML, RST, log files, and common
image formats are copied into `.moa/<session>/inputs/` and converted locally into
`.moa/<session>/attachment-context.md`. That bounded Markdown packet is
embedded in the scout brief, so every proposer, broadcast refiner, and
aggregator receives identical contents without depending on its CLI sandbox
being able to open `.moa/`. PDF page headings are preserved for citations.
Scanned PDFs with no text layer are rendered page-by-page with Poppler and
OCRed locally with Tesseract; independent pages run concurrently and the
recovered text is reassembled in original page order before it is shared
across providers.
Install both tools with `sudo apt install poppler-utils tesseract-ocr` on
Ubuntu/Debian or `brew install poppler tesseract` on macOS. PDF OCR uses a
bounded 200-DPI render (maximum 2,200 pixels on the long edge) and preserves
page headings for citations. By default the worker budgets approximately all
logical CPUs, up to 12 concurrent pages and three OpenMP threads per Tesseract
process. Each worker owns only one bounded render at a time, so memory scales
with the worker count instead of the PDF's total page count.

Reference preparation runs in the local worker rather than holding the launch
request open. The launch dialog shows completed OCR pages, active pages, and
the worker count while pages render and OCR in parallel; after preparation, it
transitions to the normal live run trace.

Images are not base64-dumped into model prompts. Base64 would consume large
amounts of context and text-only CLI transports would not interpret it as
pixels. Instead, PNG, JPEG, WebP, TIFF, and BMP uploads are OCRed locally with
Tesseract and the extracted text is shared across providers. Install it with
`sudo apt install tesseract-ocr` on Ubuntu/Debian or
`brew install tesseract` on macOS. Set `MOA_ATTACHMENT_OCR_LANG` to a locally
installed Tesseract language code (default `eng`). OCR preserves readable
labels and screenshot copy but cannot reliably describe purely visual charts
or diagrams; attach a short text description when visual relationships matter.

`MOA_ATTACHMENT_OCR_WORKERS` overrides the concurrent-page count; set it to
`1` to force serial OCR. `MOA_ATTACHMENT_OCR_THREADS_PER_WORKER` controls the
OpenMP thread ceiling passed to each Tesseract process. As a practical CPU
budget, keep `workers × threads-per-worker` near the logical CPU count. The
defaults are derived from `os.cpu_count()` and bounded for shared hosts.

The default prompt limits are 180,000 characters per file and 400,000 across
one run. Trusted deployments can override them with
`MOA_ATTACHMENT_MAX_FILE_CHARS` and `MOA_ATTACHMENT_MAX_TOTAL_CHARS`. The
scout brief records paths, sizes, hashes, extraction metadata, and truncation
state.

Uploads are treated as untrusted input data and are never executed.
Attachment contents can flow into provider prompts, local logs, and the
self-contained report. Credentials and data the selected providers are not
permitted to process do not belong in uploaded files.

## Living decision map

The setup and run pages share one decision-map vocabulary. Before a checkpoint
exists, the map shows the configured model-lab lanes and pending evidence,
claim, and decision stages. Retained Layer 1, Layer 2, and Layer 3 artifacts
then replace that preview with the orchestrator-derived `decision-map.json`;
browser events may update lane status, but never manufacture evidence or
quality scores.

The map connects evidence receipts to atomic claims, independent reviewer
findings, and final decisions. Its quality panel separates model-stated
confidence from the deterministic evidence ceiling and exposes receipt,
coverage, contradiction, reviewer-independence, source-diversity, lineage, and
pointer-integrity gates. Selectable nodes have a detail panel, and the ledger
below provides the same claim status and decision use in a table. Evidence debt
remains visible instead of being averaged into a single consensus grade.

Reloads use retained artifacts as truth. Legacy sessions can still build a map,
but unavailable repository receipts and later repository drift remain explicit
rather than being silently recaptured from current files.

## Run monitoring and recovery

The run page shows one provider character card per configured lane. During an
active stage, completion events move individual cards into their finished
state immediately; persisted manifests become the source of truth once a
checkpoint exists, so completed, failed, and downstream-blocked lanes remain
accurate after reloads. Running characters use a small work animation and
completed characters switch to a brief victory flourish. These are authored
animated WebP sequences for each provider character—not transform effects
applied to a still image. Both work and victory sequences loop while
their card remains in that state. Reduced-motion browser preferences replace
both with the provider's static portrait.

The live trace turns worker and orchestrator events into plain-language stage,
agent, validation, and failure updates. Repetitive setup logs are collapsed,
while the underlying event remains available in a **Technical detail**
disclosure for diagnosis.

If a stage fails, recovery suggestions are derived from the failed manifest
rather than from the last generic phase event. Targeted redispatch can retain
successful checkpoints and rerun selected failed lanes. A clean redispatch
starts every lane again. If Layer 1 has no accepted proposal, the worker stops
before starting paid reviewers and records the rejection causes in the trace.

## GitHub workspaces

The GitHub picker is deliberately restricted to one exact owner/name shape.
Set the owner before starting the server; it defaults to `drivelineresearch`
for the upstream project:

```bash
gh auth status
export MOA_WEBUI_GITHUB_OWNER=your-github-user-or-org
```

Repository discovery and shallow cloning use the authenticated `gh` CLI.
Browser query parameters and request payloads cannot override the configured
owner. Managed clones live under the XDG data root and appear in the GitHub
picker after creation. GitHub and upload commands use explicit argument arrays
rather than a shell.

## Build, test, and customize

The Web UI is intentionally buildless. To work on it:

```bash
# Runtime dependencies and server
.venv/bin/pip install -r requirements-web.txt

# Credential-free harness/renderer checks (the same command CI runs)
python3 harness/scripts/test_offline.py

# Flask API, persistence, upload, GitHub allowlist, and worker tests
.venv/bin/python -m unittest harness.webui.tests.test_webui

# Browser-level roster effort contract (one-time: playwright install chromium)
.venv/bin/pip install -r requirements-web-dev.txt
.venv/bin/playwright install chromium
.venv/bin/python -m unittest harness.webui.tests.test_effort_controls_browser

# Start with local source changes
MOA_WEBUI_GITHUB_OWNER=your-github-user-or-org \
  .venv/bin/python -m harness.webui
```

Backend routes and worker logic live in `harness/webui/*.py`; browser behavior
is in `harness/webui/static/js/`; layout and responsive rules are in
`harness/webui/static/css/app.css`; the page shell is in
`harness/webui/templates/`. Provider model metadata is centralized in
`harness/webui/providers.py`, while actual execution remains in the adapters
under `harness/scripts/adapters/`. Update both surfaces when adding a provider
so the UI never advertises a route the worker cannot execute.

Use environment variables for local configuration and CLI credentials. Do not
commit `.env`, XDG data, `.moa/` sessions, SQLite databases, uploads, provider
auth stores, or generated reports. The repository `.gitignore` excludes the
normal local forms, but contributors should still inspect `git status` before
publishing.

## Workspace and network controls

Legacy API and archive-import callers can use `MOA_WEBUI_WORKSPACE_ROOTS`, an
OS-path-separator-delimited allowlist of local roots (colon-delimited on
Linux/macOS). Paths are resolved before use, so symlinks cannot escape the
configured roots. The new-run UI exposes only managed GitHub and Task-only
roots; it never accepts an arbitrary browser-supplied local path.

The app requires a private browser capability before exposing job goals,
logs, reports, uploaded context, and controls. Binding to `0.0.0.0` remains an
explicit opt-in for a trusted LAN: plain HTTP can expose cookies to network
observers and the application does not provide passwords, account recovery,
administrator roles, or OS-level tenant isolation. For remote access, use
TLS plus an authenticated reverse proxy or private overlay network.
