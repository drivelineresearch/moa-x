"""Exercise repository refresh success, failures and selection in the live UI."""

import tempfile
import hashlib
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from harness.webui.app import create_app
from harness.webui.github import GitHubWorkspaceError
from harness.webui.tests.test_effort_controls_browser import _ready_catalogs


class GitHubRefreshBrowserTest(unittest.TestCase):
    def test_refresh_preserves_selection_reports_errors_and_avoids_cache(self):
        providers, models = _ready_catalogs()
        sample = {"name": "sample", "nameWithOwner": "example-org/sample",
                  "description": "Original description", "defaultBranchRef": {"name": "main"}}
        calls = []
        refreshing = threading.Event()
        release = threading.Event()

        def listing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return [sample]
            if len(calls) == 2:
                refreshing.set()
                release.wait(timeout=10)
                return [{**sample, "description": "Latest description"},
                        {"name": "added", "nameWithOwner": "example-org/added"}]
            if len(calls) == 3:
                raise GitHubWorkspaceError("Temporary GitHub outage")
            return [{"name": "added", "nameWithOwner": "example-org/added"}]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app({"TESTING": True, "START_WORKER": False,
                              "DATABASE": str(root / "jobs.sqlite3"),
                              "UPLOAD_DIR": str(root / "uploads"),
                              "GITHUB_WORKSPACE_DIR": str(root / "github"),
                              "BRIEF_WORKSPACE_DIR": str(root / "brief"),
                              "LOCAL_FONT_DIR": str(root / "fonts"),
                              "GITHUB_OWNER": "example-org", "WORKSPACE_ROOTS": [root]})
            with patch("harness.webui.app.list_repositories", side_effect=listing), \
                 patch("harness.webui.app.provider_catalog", return_value=providers), \
                 patch("harness.webui.app.model_catalog", return_value=models):
                server = make_server("127.0.0.1", 0, app, threaded=True)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        page = browser.new_page(viewport={"width": 1280, "height": 1000})
                        failures = []
                        page.on("pageerror", lambda e: failures.append(str(e)))
                        page.goto(f"http://127.0.0.1:{server.server_port}/new", wait_until="networkidle")
                        page.locator('[data-source-mode="github"]').click()
                        select = page.locator("#github-repo")
                        select.select_option("example-org/sample")
                        button = page.get_by_role("button", name="Refresh repositories")
                        button.click()
                        self.assertTrue(refreshing.wait(timeout=3))
                        self.assertTrue(button.is_disabled())
                        self.assertEqual(button.get_attribute("aria-busy"), "true")
                        release.set()
                        page.wait_for_function("document.querySelector('#github-repo').options.length === 3")
                        self.assertEqual(select.input_value(), "example-org/sample")
                        self.assertIn("Latest description", page.locator("#github-repo-meta").inner_text())
                        button.click()
                        page.wait_for_function("document.querySelector('#github-refresh-status').textContent.includes('Refresh failed')")
                        self.assertEqual(select.input_value(), "example-org/sample")
                        self.assertEqual(select.locator("option").count(), 3)
                        self.assertTrue(button.is_enabled())
                        button.click()
                        page.wait_for_function("document.querySelector('#github-repo').options.length === 2")
                        self.assertEqual(select.input_value(), "")
                        response = page.evaluate("async () => { const r=await fetch('/api/github/repos'); return {cache:r.headers.get('Cache-Control'), ok:r.ok}; }")
                        self.assertTrue(response["ok"])
                        self.assertIn("no-store", response["cache"])
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertTrue(button.is_visible())
                        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                        page.route("**/api/github/repos", lambda route: route.fulfill(
                            status=502, content_type="application/json", body='{"error":"offline"}'))
                        page.reload(wait_until="networkidle")
                        page.locator('[data-source-mode="github"]').click()
                        self.assertIn("No repository list is available", page.locator("#github-refresh-status").inner_text())
                        self.assertTrue(button.is_enabled())
                        page.unroute("**/api/github/repos")
                        button.click()
                        page.wait_for_function("document.querySelector('#github-repo').options.length === 2")
                        store = app.extensions["moa_store"]
                        store.upsert_profile("shared-owner", "Test")
                        session = root / "shared-session"
                        session.mkdir()
                        store.insert_job({"id": "shared-commit", "profile_id": "shared-owner",
                                          "title": "Source revision", "goal": "Review",
                                          "workspace": str(root), "session_dir": str(session),
                                          "status": "completed", "config": {"source": {
                                              "type": "github", "commit": "a" * 40, "ref": "main"}}})
                        token = "s" * 48
                        store.create_run_share(job_id="shared-commit", profile_id="shared-owner",
                                               token_hash=hashlib.sha256(token.encode()).hexdigest())
                        page.goto(f"http://127.0.0.1:{server.server_port}/shared/runs/{token}")
                        page.wait_for_function("document.querySelector('#source').textContent.includes('aaaaaaaaaaaa')")
                        self.assertEqual(page.locator("#source").inner_text(), "Source commit " + "a" * 40 + " · main")
                        self.assertEqual(failures, [])
                        browser.close()
                finally:
                    release.set()
                    server.shutdown()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
