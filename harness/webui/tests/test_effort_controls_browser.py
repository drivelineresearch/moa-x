from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from harness.webui import app as webui_app
from harness.webui import providers as web_providers


def _ready_catalogs() -> tuple[list[dict], list[dict]]:
    providers = web_providers.provider_catalog(probe=False)
    for provider in providers:
        provider.update(
            authenticated=True,
            installed=True,
            status="ready",
        )
        for route in provider.get("routes", []):
            route.update(available=True, availability_detail="ready")

    models = web_providers.model_catalog(probe=False)
    for model in models:
        model.update(available=True, availability_detail="ready")
    return providers, models


class EffortControlsBrowserTest(unittest.TestCase):
    def test_every_roster_effort_contract_in_live_dom(self):
        providers, models = _ready_catalogs()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = webui_app.create_app(
                {
                    "TESTING": True,
                    "START_WORKER": False,
                    "DATABASE": str(root / "webui.sqlite3"),
                    "UPLOAD_DIR": str(root / "uploads"),
                    "GITHUB_WORKSPACE_DIR": str(root / "github"),
                    "BRIEF_WORKSPACE_DIR": str(root / "brief"),
                    "LOCAL_FONT_DIR": str(root / "fonts"),
                    "WORKSPACE_ROOTS": [root],
                }
            )
            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (
                patch.object(webui_app, "provider_catalog", return_value=providers),
                patch.object(webui_app, "model_catalog", return_value=models),
                patch.object(webui_app, "list_repositories", return_value=[]),
            ):
                thread.start()
                try:
                    self._assert_browser_contract(
                        f"http://127.0.0.1:{server.server_port}/new"
                    )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def _assert_browser_contract(self, url: str) -> None:
        expected_presets = {
            "quick": {"agy-gemini-pro": "Low", "codex-sol": "Xhigh"},
            "balanced": {
                "agy-gemini-pro": "High",
                "opus": "High",
                "codex-sol": "Xhigh",
            },
            "thorough": {
                "agy-gemini-pro": "High",
                "opus": "Max",
                "codex-sol": "Xhigh",
            },
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            console_errors: list[str] = []
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text)
                    if message.type == "error"
                    else None
                ),
            )
            page.goto(url, wait_until="networkidle")
            page.wait_for_function(
                "document.querySelectorAll('.model-option').length > 10"
            )

            rows = page.locator(".model-option")
            self.assertGreater(rows.count(), 10)
            for index in range(rows.count()):
                row = rows.nth(index)
                route = row.locator(".route-choice")
                adjustable = (
                    route.get_attribute("data-effort-adjustable") == "true"
                )
                controls = row.locator("fieldset[data-effort-control]")
                self.assertEqual(
                    controls.count(),
                    int(adjustable),
                    route.get_attribute("value"),
                )
                copy = row.locator(".model-row-copy small").inner_text()
                self.assertEqual("Adjust" in copy, adjustable, copy)
                if not adjustable:
                    self.assertTrue(
                        "Fixed " in copy or "Provider-managed effort" in copy,
                        copy,
                    )
                    continue
                selected = route.is_checked() and route.is_enabled()
                visible = controls.evaluate(
                    "node => !node.hidden && getComputedStyle(node).display !== 'none'"
                )
                range_control = controls.locator("[data-effort-range]")
                enabled = range_control.is_enabled()
                self.assertEqual(visible, selected, route.get_attribute("value"))
                self.assertEqual(enabled, selected, route.get_attribute("value"))
                self.assertTrue(
                    range_control.get_attribute("aria-label").startswith(
                        controls.locator("legend").inner_text()
                    ),
                    route.get_attribute("value"),
                )

            for index, (preset, expected) in enumerate(expected_presets.items()):
                depth = page.locator("#depth-range")
                depth.fill(str(index))
                depth.dispatch_event("input")
                depth.dispatch_event("change")
                for route_id, effort in expected.items():
                    route = page.locator(
                        f'.route-choice[value="{route_id}"]:checked'
                    ).first
                    self.assertEqual(route.count(), 1, f"{preset}: {route_id}")
                    row = route.locator("xpath=..")
                    slider = row.locator("[data-effort-range]")
                    self.assertEqual(
                        slider.get_attribute("aria-valuetext"),
                        effort,
                        f"{preset}: {route_id}",
                    )
                    self.assertTrue(slider.is_enabled(), f"{preset}: {route_id}")
                    self.assertTrue(
                        row.locator("fieldset[data-effort-control]").evaluate(
                            "node => !node.hidden && getComputedStyle(node).display !== 'none'"
                        ),
                        f"{preset}: {route_id}",
                    )
                    group = row.locator("xpath=../..")
                    self.assertEqual(group.evaluate("node => node.tagName"), "DETAILS")
                    self.assertTrue(
                        group.evaluate("node => node.open"),
                        f"{preset}: {route_id}",
                    )

            fable = page.locator(
                'input[name="aggregator"][value="fable"]'
            ).locator("xpath=..")
            self.assertEqual(
                fable.locator("fieldset[data-effort-control]").count(),
                0,
            )
            self.assertIn(
                "Fixed Xhigh effort",
                fable.locator(".model-row-copy small").inner_text(),
            )
            progress_values = page.evaluate(
                """async () => {
                  const { attachmentProgressPercent } = await import(
                    "/static/js/app.js"
                  );
                  return [
                    { stage: "extracting", page_number: 1, page_count: 319 },
                    { stage: "extracting", page_number: 319, page_count: 319 },
                    {
                      stage: "ocr-starting", page_count: 319,
                      ocr_page_count: 319, completed_pages: 0,
                    },
                    {
                      stage: "ocr-complete", page_count: 319,
                      ocr_page_count: 319, completed_pages: 1,
                    },
                    {
                      stage: "ocr-complete", page_count: 319,
                      ocr_page_count: 319, completed_pages: 160,
                    },
                    { stage: "complete" },
                  ].map(attachmentProgressPercent);
                }"""
            )
            self.assertEqual(progress_values, sorted(progress_values))
            self.assertEqual(progress_values[-1], 100)
            retry_trace = page.evaluate(
                """async () => {
                  const { tracePresentation } = await import(
                    "/static/js/app.js"
                  );
                  return tracePresentation({
                    seq: 1,
                    kind: "log",
                    message: "[orchestrator] Layer 1: spawning ['cursor-grok'] in parallel... (redispatch)",
                    data: { phase: "layer1" },
                  });
                }"""
            )
            self.assertEqual(retry_trace["title"], "Proposal retry started")
            self.assertEqual(
                retry_trace["message"],
                "One proposer lane is retrying.",
            )
            self.assertEqual(console_errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
