"""Reader-facing readiness and evidence counts must agree with saved decisions."""

from __future__ import annotations
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from harness.webui import app as webui_app
from harness.webui.tests.test_decision_map_browser import _ready_catalogs
from harness.scripts import test_evidence_pipeline as evidence_cases
from harness.scripts import evidence_pipeline, run_moa


class EvidenceProgressBrowserTest(unittest.TestCase):
    def test_verified_needs_revision_and_mobile_states(self):
        fixture = evidence_cases.EvidenceTests()
        fixture.setUp()
        try:

            def refresh():
                fixture.make_map()
                (fixture.session / "final-plan.md").write_text(
                    evidence_pipeline.render_plan(fixture.lineage)
                )
                (fixture.session / "final-audit.md").write_text(
                    "# Final evidence check\n\nSynthetic fixture: checked cache.py:2.\n"
                )
                manifest = json.loads((fixture.session / "manifest.json").read_text())
                manifest["layer3"] = [
                    {
                        "agent_id": "codex-sol",
                        "layer": 3,
                        "role": "aggregator",
                        "success": True,
                        "schema_valid": True,
                        "model": "gpt-6.1-sol",
                    }
                ]
                (fixture.session / "manifest.json").write_text(json.dumps(manifest))
                run_moa._refresh_decision_map(
                    fixture.session, fixture.session / "manifest.json"
                )

            refresh()
            app = webui_app.create_app(
                {
                    "TESTING": True,
                    "START_WORKER": False,
                    "DATABASE": str(fixture.root / "browser.sqlite3"),
                    "UPLOAD_DIR": str(fixture.root / "uploads"),
                    "GITHUB_WORKSPACE_DIR": str(fixture.root / "github"),
                    "BRIEF_WORKSPACE_DIR": str(fixture.root / "brief"),
                    "WORKSPACE_ROOTS": [fixture.root],
                }
            )
            store = app.extensions["moa_store"]
            job_id = "evidence-fixture"
            store.insert_job(
                {
                    "id": job_id,
                    "title": "Seeded cache contract fixture",
                    "workspace": str(fixture.repo),
                    "session_dir": str(fixture.session),
                    "goal": "Check exact expiry and falsy values.",
                    "status": "completed",
                    "phase": "complete",
                    "progress": 1,
                    "imported": True,
                    "config": {
                        "proposers": ["grok"],
                        "refiners": ["deepseek-flash", "opus"],
                        "aggregator": "codex-sol",
                        "options": {"depth": "balanced"},
                    },
                    "created_at": time.time(),
                }
            )
            store.update_job(
                job_id,
                status="completed",
                phase="complete",
                progress=1,
                finished_at=time.time(),
            )
            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            providers, models = _ready_catalogs()
            with (
                patch.object(webui_app, "provider_catalog", return_value=providers),
                patch.object(webui_app, "model_catalog", return_value=models),
                patch.object(webui_app, "list_repositories", return_value=[]),
            ):
                thread.start()
                try:
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(headless=True)
                        page = browser.new_page(
                            viewport={"width": 1440, "height": 1000}
                        )
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        base = f"http://127.0.0.1:{server.server_port}"
                        page.goto(base + "/new", wait_until="networkidle")
                        page.wait_for_function(
                            "localStorage.getItem('moax.profile') !== null"
                        )
                        profile = page.evaluate(
                            "JSON.parse(localStorage.getItem('moax.profile')).id"
                        )
                        self.assertTrue(store.claim_job_profile(job_id, profile))
                        page.goto(base + "/runs/" + job_id, wait_until="networkidle")
                        page.locator("#result-panel").wait_for(state="visible")
                        self.assertIn(
                            "1 of 1 selected claims verified",
                            page.locator("#run-evidence-progress").inner_text(),
                        )
                        self.assertIn(
                            "ready for human review",
                            page.locator("#result-panel").inner_text().lower(),
                        )
                        capture = Path(
                            os.environ.get(
                                "MOAX_E2E_CAPTURE_DIR", fixture.root / "captures"
                            )
                        )
                        capture.mkdir(parents=True, exist_ok=True)
                        page.screenshot(
                            path=str(capture / "v2-ready-desktop.png"), full_page=True
                        )
                        fixture.lineage["outcome"] = "insufficient_evidence"
                        refresh()
                        page.reload(wait_until="networkidle")
                        self.assertIn(
                            "the result needs verification",
                            page.locator("#result-panel").inner_text().lower(),
                        )
                        self.assertIn(
                            "Needs verification",
                            page.locator("#run-readiness").inner_text(),
                        )
                        self.assertNotIn(
                            "Pending",
                            page.locator(
                                "#live-decision-map .dm-metric"
                            ).first.inner_text(),
                        )
                        self.assertNotIn(
                            "completion estimate",
                            page.locator("#run-evidence-progress").inner_text(),
                        )
                        page.screenshot(
                            path=str(capture / "v2-needs-verification-desktop.png"),
                            full_page=True,
                        )
                        fixture.lineage["outcome"] = "needs_revision"
                        refresh()
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.reload(wait_until="networkidle")
                        self.assertIn(
                            "the plan needs revision",
                            page.locator("#result-panel").inner_text().lower(),
                        )
                        self.assertTrue(
                            page.evaluate(
                                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                            )
                        )
                        page.screenshot(
                            path=str(capture / "v2-needs-revision-mobile.png"),
                            full_page=True,
                        )
                        # Contradictions in the final audit must not hide behind good upstream claim counts.
                        audit = {
                            "success": True,
                            "findings": [
                                {
                                    "step_id": "fix-expiry",
                                    "status": "contradicted",
                                    "finding": "Synthetic final-decision contradiction",
                                    "source_url": "cache.py:2",
                                }
                            ],
                        }
                        (fixture.session / "final-audit.json").write_text(
                            json.dumps(audit)
                        )
                        run_moa._refresh_decision_map(
                            fixture.session, fixture.session / "manifest.json"
                        )
                        page.reload(wait_until="networkidle")
                        self.assertIn(
                            "1 contradicted",
                            page.locator("#run-evidence-progress").inner_text(),
                        )
                        self.assertTrue(
                            page.locator(".dm-gates").get_attribute("open") is not None
                        )
                        self.assertIn(
                            "needs-review",
                            page.locator(".dm-stage").get_attribute("class"),
                        )
                        page.goto(base + "/runs", wait_until="networkidle")
                        self.assertIn(
                            "needs revision",
                            page.locator("#run-archive").inner_text().lower(),
                        )
                        page.goto(base + "/runs/" + job_id, wait_until="networkidle")
                        # A failed restart cannot promote an old ready plan.
                        fixture.lineage["outcome"] = "ready"
                        refresh()
                        store.update_job(job_id, status="failed", phase="failed")
                        page.reload(wait_until="networkidle")
                        self.assertTrue(page.locator("#run-readiness").is_hidden())
                        store.update_job(job_id, status="completed", phase="complete")
                        del fixture.lineage["outcome"]
                        refresh()
                        page.reload(wait_until="networkidle")
                        self.assertIn(
                            "readiness not recorded",
                            page.locator("#result-panel").inner_text().lower(),
                        )
                        self.assertEqual(errors, [])
                        browser.close()
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
