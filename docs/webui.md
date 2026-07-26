# Local Web UI

The Flask control room is a local-first interface over the existing
`run_moa.py` phases. It queues one run at a time, streams lifecycle events,
shows provider health, and indexes completed and historical `.moa` sessions.
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

No Node.js or frontend build tool is required. HTML, CSS, JavaScript, the
operating-system font stack, and project-owned image assets are already
versioned under `harness/webui/`. Gotham is preferred automatically when it
is installed on the viewing workstation but is not redistributed. A server
may also expose locally licensed Gotham Office Regular and Bold files from
`$XDG_DATA_HOME/moa-x/fonts/` to remote browsers; see
[`docs/assets.md`](assets.md) for the exact filenames and licensing boundary.
Waitress is installed from `requirements-web.txt` and selected automatically;
Flask's development server is only the fallback when Waitress is unavailable.

The worker inherits the server process's `HOME`, `PATH`, environment, CLI
keychains, and authenticated accounts. It never copies API keys or OAuth
tokens into the browser or SQLite.

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
Profiles organize “mine” versus all-local activity; without authentication
they are not a privacy or authorization boundary.

Run payloads, logs, manifests, and final artifacts remain in
`.moa/<session>/` under the managed task or GitHub workspace. A **Task only**
run uses a private per-job workspace under `workspaces/brief/`, so a repository
is never required. Existing repository sessions can still be imported into
the SQLite index without moving their artifacts.

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
Scanned PDFs with no text layer are rejected with an OCR instruction rather
than silently becoming unavailable.

Images are not base64-dumped into model prompts. Base64 would consume large
amounts of context and text-only CLI transports would not interpret it as
pixels. Instead, PNG, JPEG, WebP, TIFF, and BMP uploads are OCRed locally with
Tesseract and the extracted text is shared across providers. Install it with
`sudo apt install tesseract-ocr` on Ubuntu/Debian or
`brew install tesseract` on macOS. Set `MOA_ATTACHMENT_OCR_LANG` to a locally
installed Tesseract language code (default `eng`). OCR preserves readable
labels and screenshot copy but cannot reliably describe purely visual charts
or diagrams; attach a short text description when visual relationships matter.

The default prompt limits are 180,000 characters per file and 400,000 across
one run. Trusted deployments can override them with
`MOA_ATTACHMENT_MAX_FILE_CHARS` and `MOA_ATTACHMENT_MAX_TOTAL_CHARS`. The
scout brief records paths, sizes, hashes, extraction metadata, and truncation
state.

Uploads are treated as untrusted input data and are never executed.
Attachment contents can flow into provider prompts, local logs, and the
self-contained report. Credentials and data the selected providers are not
permitted to process do not belong in uploaded files.

## Run monitoring and recovery

The run page shows one provider character card per configured lane. During an
active stage, completion events move individual cards into their finished
state immediately; persisted manifests become the source of truth once a
checkpoint exists, so completed, failed, and downstream-blocked lanes remain
accurate after reloads. Running characters use a small work animation and
completed characters switch to a brief victory flourish. These are authored
eight-frame animated WebP sequences for each provider character—not transform
effects applied to a still image. Both work and victory sequences loop while
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

The app exposes job goals, logs, reports, uploaded context, and controls to
any browser that can reach it. Binding to `0.0.0.0` is an explicit opt-in for
a trusted LAN and is not suitable for direct internet exposure. If remote
access is required, put an authenticated, TLS-terminating reverse proxy or
private overlay network in front of the loopback-bound service; the
application itself does not provide accounts, authorization, or tenant
isolation.
