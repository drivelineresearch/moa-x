from __future__ import annotations

import json
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
            session = root / "brief" / "lab-audit"
            session.mkdir(parents=True)
            app.extensions["moa_store"].insert_job(
                {
                    "id": "lab-audit",
                    "title": "Model-lab visual audit",
                    "workspace": str(root),
                    "session_dir": str(session),
                    "goal": "Exercise every model-lab visual consumer.",
                    "status": "running",
                    "phase": "layer1",
                    "progress": 35,
                    "imported": True,
                    "config": {
                        "proposers": ["agy-gemini-pro", "grok", "codex-luna"],
                        "refiners": ["qwen", "kimi", "opus"],
                        "aggregator": "codex-sol",
                        "options": {"aggregate": True},
                    },
                }
            )
            state_session = root / "brief" / "state-audit"
            state_session.mkdir(parents=True)
            (state_session / "layer1-manifest.json").write_text(
                json.dumps(
                    {
                        "layer1": [
                            {
                                "agent_id": "agy-gemini-pro",
                                "layer": 1,
                                "role": "proposer",
                                "success": True,
                                "schema_valid": True,
                                "started_at": 100,
                                "duration_seconds": 42,
                            },
                            {
                                "agent_id": "grok",
                                "layer": 1,
                                "role": "proposer",
                                "success": False,
                                "schema_valid": False,
                                "started_at": 100,
                                "duration_seconds": 45,
                                "error": "Structured response validation failed.",
                            },
                        ]
                    }
                )
            )
            app.extensions["moa_store"].insert_job(
                {
                    "id": "state-audit",
                    "title": "Lane state visual audit",
                    "workspace": str(root),
                    "session_dir": str(state_session),
                    "goal": "Exercise every live lane lifecycle treatment.",
                    "status": "running",
                    "phase": "layer2",
                    "progress": 62,
                    "imported": True,
                    "config": {
                        "proposers": ["agy-gemini-pro", "grok", "codex-luna"],
                        "refiners": ["kimi", "opus", "qwen"],
                        "aggregator": "codex-sol",
                        "options": {"aggregate": True},
                    },
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
                        f"http://127.0.0.1:{server.server_port}/new",
                        app.extensions["moa_store"],
                    )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def _assert_browser_contract(self, url: str, store) -> None:
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
            group_images = page.locator(
                ".model-provider-group .chooser-avatar img"
            )
            self.assertGreaterEqual(group_images.count(), 6)
            for index in range(group_images.count()):
                source = group_images.nth(index).get_attribute("src")
                self.assertRegex(
                    source or "",
                    r"^/static/images/lab-[a-z]+-avatar\.webp$",
                )
                self.assertTrue(
                    group_images.nth(index).evaluate(
                        "node => node.complete && node.naturalWidth > 0"
                    ),
                    source,
                )
            self.assertEqual(
                page.locator(
                    'img[src*="provider-"], img[src*="pixel-codex"], '
                    'img[src*="pixel-claude"], img[src*="pixel-opencode"], '
                    'img[src*="pixel-agy"]'
                ).count(),
                0,
            )
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
                    message: "[orchestrator] Layer 1: spawning ['grok'] in parallel... (redispatch)",
                    data: { phase: "layer1" },
                  });
                }"""
            )
            self.assertEqual(retry_trace["title"], "Proposal retry started")
            self.assertEqual(
                retry_trace["message"],
                "One proposer lane is retrying.",
            )
            profile_id = page.evaluate(
                "JSON.parse(localStorage.getItem('moax.profile')).id"
            )
            self.assertTrue(store.claim_job_profile("lab-audit", profile_id))
            self.assertTrue(store.claim_job_profile("state-audit", profile_id))
            base_url = url.rsplit("/new", 1)[0]
            page.goto(base_url + "/runs/state-audit", wait_until="domcontentloaded")
            page.wait_for_selector(".agent-pixel-stage")
            page.wait_for_timeout(300)
            stages = page.locator(".agent-pixel-stage")
            self.assertEqual(stages.count(), 7)
            self.assertEqual(
                set(
                    page.locator(".agent-card").evaluate_all(
                        "nodes => nodes.map(node => node.dataset.agentStatus)"
                    )
                ),
                {"completed", "failed", "blocked", "running", "queued"},
            )
            self.assertEqual(
                page.locator(".status-tag.running span").first.inner_text(),
                "WORKING",
            )
            self.assertEqual(
                page.locator(
                    ".status-tag.running .agent-status-icon"
                ).first.evaluate("node => getComputedStyle(node).animationName"),
                "spin",
            )
            state_colors = page.locator(
                ".status-tag.completed, .status-tag.failed, "
                ".status-tag.blocked, .status-tag.running, .status-tag.queued"
            ).evaluate_all(
                "nodes => [...new Set(nodes.map(node => "
                "getComputedStyle(node).backgroundColor))]"
            )
            self.assertEqual(len(state_colors), 5)
            self.assertEqual(stages.locator("picture").count(), 0)
            self.assertTrue(
                stages.evaluate_all(
                    """nodes => nodes.every(node => {
                      const image = node.querySelector(":scope > img");
                      const style = getComputedStyle(image);
                      return node.dataset.labId
                        && node.getAttribute("aria-label")?.includes(", ")
                        && style.objectFit === "cover"
                        && style.transform !== "none";
                    })"""
                )
            )
            for viewport in (
                {"width": 1440, "height": 1000},
                {"width": 390, "height": 844},
            ):
                page.set_viewport_size(viewport)
                for route in ("/", "/new", "/runs", "/providers", "/runs/lab-audit"):
                    page_url = base_url + route
                    page.goto(page_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(600)
                    label = f"{page_url} at {viewport['width']}px"
                    self.assertEqual(
                        page.locator(
                            'img[src*="provider-"], img[src*="pixel-codex"], '
                            'img[src*="pixel-claude"], img[src*="pixel-opencode"], '
                            'img[src*="pixel-agy"]'
                        ).count(),
                        0,
                        label,
                    )
                    lab_images = page.locator('img[src*="/static/images/lab-"]')
                    self.assertGreater(lab_images.count(), 0, label)
                    self.assertTrue(
                        lab_images.evaluate_all(
                            "nodes => nodes.every(node => node.complete && node.naturalWidth > 0)"
                        ),
                        label,
                    )
            self.assertEqual(console_errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
