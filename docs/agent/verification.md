# Verification

Read this when choosing checks for a change. CI is defined in
[ci.yml](../../.github/workflows/ci.yml); keep new tests credential-free.

## Harness and release baseline

```bash
python3 harness/scripts/test_offline.py
python3 harness/scripts/test_opencode.py
python3 harness/scripts/test_native.py
python3 harness/scripts/test_recovery.py
python3 harness/scripts/test_scout.py
python3 harness/scripts/test_evidence_pipeline.py
```

## Web, Slack, and browser contracts

Install the development dependencies and Chromium once:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-web-dev.txt -r requirements-slack.txt
.venv/bin/python -m playwright install chromium
```

Run the suites used by browser CI:

```bash
.venv/bin/python -m unittest \
  harness.webui.tests.test_webui \
  harness.webui.tests.test_github_snapshots \
  harness.webui.tests.test_github_refresh_browser \
  harness.slack.tests.test_slack \
  harness.slack.tests.test_repositories \
  harness.webui.tests.test_effort_controls_browser \
  harness.webui.tests.test_decision_map_browser \
  harness.webui.tests.test_evidence_progress_browser
```

For docs changes, check relative links and equivalent agent guidance. For a
release, also verify the extracted archives as described in [releasing](releases.md).
Provider preflight uses local accounts and catalog probes; it is separate from
the offline baseline and is needed when actually dispatching model calls.
