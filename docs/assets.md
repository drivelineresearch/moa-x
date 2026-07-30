# Visual assets and fonts

MoA-X does not redistribute proprietary fonts or depend on a remote font
service. The Web UI prefers Gotham when it is already installed in the
viewer's operating system, then falls back to a standard sans-serif stack. A
clean public clone therefore works without downloading or licensing
typography files.

For a trusted local deployment viewed from another computer, operators who
already have appropriately licensed web-embeddable copies may place exactly
these two runtime files in `$XDG_DATA_HOME/moa-x/fonts/` (normally
`~/.local/share/moa-x/fonts/`):

```text
GothamOffice-Regular.woff2
GothamOffice-Bold.woff2
```

The server then exposes those two files to the browser and switches the UI to
Gotham Office automatically. Set `MOA_WEBUI_LOCAL_FONT_DIR` to use another
machine-local directory. The route is an exact filename allowlist; no other
file in that directory can be fetched. Font files belong to the local
deployment, stay untracked, and must never be added to the public repository.
Confirm that your font license permits browser embedding before enabling this
option.

The WebP/PNG illustrations under `harness/webui/static/images/` and
`harness/report/assets/` were generated specifically for this project and are
distributed with the repository under its MIT license. They are original
model-lab and workflow-themed characters and scenes, not official vendor
logos, licensed characters, or claims of endorsement by a model provider.

The model-lab asset contract is centralized in
`harness/scripts/model_labs.py`. Each known lab has one 320px editorial avatar
and one 480px pixel-art character under the `lab-<id>-*` naming scheme. The
same `lab_id` must drive launch-roster groups, provider-health route portraits,
review-network nodes, live lanes, and archived-run fallbacks. Execution
harnesses such as OpenCode or AGY must never determine character identity.
Unknown custom routes use the `independent` lab set. Do not reintroduce
`provider-*` or harness-keyed `pixel-*` assets.

Live agent lanes keep that lab character static while work is in progress.
Lifecycle is communicated separately with a text label, icon, state-specific
color, and a CSS-only spinner for active work. Per-lab focal crops belong in
`harness/webui/static/css/app.css`; verify both desktop and mobile crops so
faces remain visible without restoring the empty frame baked into the source
art. Do not generate animated running/queued/completed variants.

When contributing new assets:

- use work you created or have clear redistribution rights to;
- document third-party licenses and attribution in this file;
- strip embedded credentials, personal paths, comments, and unnecessary
  metadata before committing;
- prefer WebP/PNG/SVG files that can be served directly without a build step;
- include a static fallback and useful alternative text when the image conveys
  information;
- never commit screenshots containing private repositories, prompts, reports,
  account names, email addresses, API keys, or local file paths.
