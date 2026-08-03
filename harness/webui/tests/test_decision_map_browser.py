from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Page, sync_playwright
from werkzeug.serving import make_server

from harness.scripts import run_moa as run_moa_module
from harness.scripts import report
from harness.webui import app as webui_app
from harness.webui import providers as web_providers


def _ready_catalogs() -> tuple[list[dict], list[dict]]:
    providers = web_providers.provider_catalog(probe=False)
    for provider in providers:
        provider.update(authenticated=True, installed=True, status="ready")
        for route in provider.get("routes", []):
            route.update(available=True, availability_detail="ready")
    models = web_providers.model_catalog(probe=False)
    for model in models:
        model.update(available=True, availability_detail="ready")
    return providers, models


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _result(agent_id: str, model: str, json_path: str, *, reviewing=None) -> dict:
    return {
        "agent_id": agent_id,
        "success": True,
        "schema_valid": True,
        "transient_empty": False,
        "duration_seconds": 3.5,
        "started_at": 100.0,
        "json_path": json_path,
        "log_path": None,
        "error": None,
        "model": model,
        "reviewing": reviewing,
        "reported_agent_id": None,
        "workspace_mutations": [],
    }


def _git_repo(root: Path) -> Path:
    repo = root / "repository"
    source = repo / "app" / "cache.py"
    source.parent.mkdir(parents=True)
    source.write_text("CACHE_NAMESPACE = 'moax'\n", encoding="utf-8")
    commands = (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "browser-test@example.invalid"),
        ("git", "config", "user.name", "MoA-X Browser Test"),
        ("git", "add", "app/cache.py"),
        ("git", "commit", "-qm", "fixture"),
    )
    for command in commands:
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    return repo


def _proposer_payload() -> dict:
    claim = "Every cache key uses a stable MoA-X namespace."
    return {
        "agent_id": "codex",
        "summary": "Use one explicit cache namespace and test its contract.",
        "plan": [{
            "step": "Make cache-key namespacing explicit",
            "why": "One stable boundary prevents collisions across environments.",
            "files_touched": ["app/cache.py"],
            "evidence": [
                {
                    "type": "code",
                    "file": "app/cache.py",
                    "line": 1,
                    "url": None,
                    "snippet": "CACHE_NAMESPACE = 'moax'",
                    "claim": claim,
                },
                {
                    "type": "external",
                    "file": None,
                    "line": None,
                    "url": "https://redis.io/docs/latest/develop/use/keyspace/",
                    "snippet": "Use a consistent key naming convention.",
                    "claim": claim,
                },
            ],
            "risks": ["Existing keys need a bounded migration."],
        }],
        "open_questions": ["Is a compatibility read needed for one release?"],
        "alternatives_rejected": [
            {"approach": "Implicit prefixes", "reason": "They are hard to audit."},
            {"approach": "No namespace", "reason": "It permits collisions."},
        ],
        "research_sources": [
            {"url": f"https://example.com/{index}", "title": f"Source {index}", "summary": "Fixture source.", "relevance": "Namespacing."}
            for index in range(1, 6)
        ],
    }


def _refiner_payload(agent_id: str) -> dict:
    verifications = [
        {
            "proposer": "codex",
            "claim_index_path": f"plan[0].evidence[{index}]",
            "status": "verified",
            "actual_finding": "The retained source independently supports the namespace claim.",
            "source_url": "https://redis.io/docs/latest/develop/use/keyspace/",
        }
        for index in range(2)
    ]
    return {
        "agent_id": agent_id,
        "reviewing": ["codex"],
        "per_proposer_verdicts": [{"proposer": "codex", "verdict": "accept_as_is", "summary": "Evidence and implementation align."}],
        "agreements": ["The namespace boundary is explicit."],
        "disagreements": [],
        "cross_proposer_observations": ["The evidence is consistent across source types."],
        "missing_steps": [],
        "incorrect_steps": [],
        "verifications": verifications,
        "additional_research": [],
        "synthesis_recommendation": "Retain the namespacing step and its acceptance test.",
        "overall_verdict": "accept_as_is",
    }


def _base_manifest(session: Path, repo: Path) -> dict:
    return {
        "session_id": session.name,
        "architecture_version": "v3-named-roster",
        "config": {
            "repo_path": str(repo),
            "proposers": [{"name": "codex", "model": "gpt-5.6-terra"}],
            "refiners": [
                {"name": "qwen", "model": "qwen3.8-max-preview"},
                {"name": "opus", "model": "claude-opus-5"},
            ],
            "aggregator": {"name": "codex-sol", "model": "gpt-5.6-sol"},
        },
        "layer2_mode": "broadcast",
        "started_at": 100.0,
        "finished_at": 120.0,
        "duration_seconds": 20.0,
        "layer1": [_result("codex", "gpt-5.6-terra", "codex.json")],
        "layer2": [],
        "summary": {},
    }


def _refresh_received_map(session: Path) -> dict:
    """Exercise the production map writer so browser fixtures carry its receipt."""
    manifest_path = session / "manifest.json"
    output = run_moa_module._refresh_decision_map(session, manifest_path)
    if output is None:
        raise AssertionError("decision-map fixture failed production refresh")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _seed_proposals(session: Path, repo: Path) -> dict:
    session.mkdir(parents=True)
    _write_json(session / "scout-brief.json", {
        "session_id": session.name,
        "repo_path": str(repo),
        "frozen_spec": "Ship an auditable cache namespace decision.",
    })
    _write_json(session / "codex.json", _proposer_payload())
    manifest = _base_manifest(session, repo)
    _write_json(session / "manifest.json", manifest)
    return _refresh_received_map(session)


def _seed_review(session: Path, manifest: dict) -> dict:
    for agent_id in ("qwen", "opus"):
        _write_json(session / f"{agent_id}.json", _refiner_payload(agent_id))
    manifest = json.loads(json.dumps(manifest))
    manifest["layer2"] = [
        _result("qwen", "qwen3.8-max-preview", "qwen.json", reviewing=["codex"]),
        _result("opus", "claude-opus-5", "opus.json", reviewing=["codex"]),
    ]
    manifest["finished_at"] = 140.0
    manifest["duration_seconds"] = 40.0
    _write_json(session / "manifest.json", manifest)
    return _refresh_received_map(session)


def _seed_complete(session: Path, manifest: dict) -> None:
    lineage = {
        "version": 1,
        "title": "Auditable cache namespace",
        "summary": "Keep one explicit namespace with code and external receipts.",
        "confidence": {"level": "high", "rationale": "Two labs verified two independent receipts."},
        "steps": [{
            "id": "cache-namespace",
            "title": "Retain the explicit namespace",
            "description": "Keep the namespace constant and validate generated keys.",
            "files_touched": ["app/cache.py"],
            "decision": "accepted",
            "adjudication": "Both independent reviewers verified the material claim.",
            "proposer_refs": [{"agent_id": "codex", "step_index": 0, "relationship": "adopted", "note": "The evidence is material."}],
            "refiner_refs": [
                {"agent_id": "qwen", "kind": "verification", "index": 0, "note": "Alibaba-lab verification."},
                {"agent_id": "opus", "kind": "verification", "index": 0, "note": "Anthropic-lab verification."},
            ],
        }],
        "rejected_inputs": [],
    }
    _write_json(session / "final-plan.json", lineage)
    (session / "final-plan.md").write_text(
        "# Auditable cache namespace\n\nKeep the explicit namespace and validate generated keys.\n",
        encoding="utf-8",
    )
    _write_json(session / "codex-sol.json", {"agent_id": "codex-sol", "summary": "Final plan retained."})
    manifest = json.loads(json.dumps(manifest))
    manifest["layer3"] = [_result("codex-sol", "gpt-5.6-sol", "codex-sol.json")]
    manifest["finished_at"] = 160.0
    manifest["duration_seconds"] = 60.0
    _write_json(session / "manifest.json", manifest)
    report.generate(session, session / "report.html")


class DecisionMapBrowserTest(unittest.TestCase):
    def test_setup_live_review_complete_and_report_workflow(self):
        providers, models = _ready_catalogs()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = _git_repo(root)
            session = root / "brief" / "decision-map-demo"
            manifest = _seed_proposals(session, repo)
            app = webui_app.create_app({
                "TESTING": True,
                "START_WORKER": False,
                "DATABASE": str(root / "webui.sqlite3"),
                "UPLOAD_DIR": str(root / "uploads"),
                "GITHUB_WORKSPACE_DIR": str(root / "github"),
                "BRIEF_WORKSPACE_DIR": str(root / "brief"),
                "LOCAL_FONT_DIR": str(root / "fonts"),
                "WORKSPACE_ROOTS": [root],
            })
            store = app.extensions["moa_store"]
            store.insert_job({
                "id": "decision-map-demo",
                "title": "Auditable cache namespace",
                "workspace": str(repo),
                "session_dir": str(session),
                "goal": "Ship an auditable cache namespace decision.",
                "status": "running",
                "phase": "layer1",
                "progress": 35,
                "imported": True,
                "config": {
                    "proposers": ["codex"],
                    "refiners": ["qwen", "opus"],
                    "aggregator": "codex-sol",
                    "options": {"aggregate": True},
                },
                "created_at": time.time(),
            })
            capture_dir = Path(os.environ.get("MOAX_E2E_CAPTURE_DIR", root / "captures"))
            capture_dir.mkdir(parents=True, exist_ok=True)
            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (
                patch.object(webui_app, "provider_catalog", return_value=providers),
                patch.object(webui_app, "model_catalog", return_value=models),
                patch.object(webui_app, "list_repositories", return_value=[]),
            ):
                thread.start()
                try:
                    self._exercise(
                        f"http://127.0.0.1:{server.server_port}",
                        store,
                        session,
                        manifest,
                        capture_dir,
                    )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def _capture(self, page: Page, capture_dir: Path, name: str) -> None:
        page.evaluate("""
            if (document.activeElement instanceof HTMLElement) {
                document.activeElement.blur();
            }
            document.documentElement.style.scrollBehavior = "auto";
            window.scrollTo({ top: 0, left: 0, behavior: "instant" });
        """)
        page.wait_for_function("window.scrollY === 0")
        page.wait_for_timeout(200)
        page.screenshot(path=str(capture_dir / name), full_page=True)

    def _assert_no_body_overflow(self, page: Page) -> None:
        self.assertTrue(page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        ))

    def _exercise(self, base_url: str, store, session: Path, manifest: dict, capture_dir: Path) -> None:
        unexpected_requests: list[str] = []
        console_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("request", lambda request: unexpected_requests.append(request.url) if not request.url.startswith(base_url) else None)

            page.goto(f"{base_url}/new", wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('.model-option').length > 10")
            page.locator("#run-goal").fill("Ship an auditable cache namespace decision.")
            for step in range(1, 5):
                page.locator(f'.wizard-step[data-step="{step}"].is-active [data-next-step]').click()
            page.locator("#review-network .dm-shell").wait_for()
            self.assertEqual(page.locator("#review-network .dm-stage").text_content(), "Ready for dispatch")
            self.assertGreaterEqual(page.locator("#review-network .dm-node-agent").count(), 3)
            page.locator("#review-network .dm-node-claim").focus()
            page.keyboard.press("Enter")
            self.assertIn("Evidence receipts", page.locator("#review-network .dm-detail-title").inner_text())
            self._assert_no_body_overflow(page)
            self._capture(page, capture_dir, "01-setup-review.png")

            profile_id = page.evaluate("JSON.parse(localStorage.getItem('moax.profile')).id")
            self.assertTrue(store.claim_job_profile("decision-map-demo", profile_id))
            page.goto(f"{base_url}/runs/decision-map-demo", wait_until="domcontentloaded")
            page.locator("#live-decision-map .dm-shell").wait_for()
            page.wait_for_function("document.querySelectorAll('#live-decision-map .dm-node-evidence').length === 2")
            page.wait_for_function("document.querySelectorAll('#live-decision-map .dm-edge.is-contributes').length >= 2")
            self.assertEqual(page.locator("#live-decision-map .dm-stage").text_content(), "Evidence collected")
            self.assertEqual(page.locator("#live-decision-map .dm-node-evidence").count(), 2)
            self.assertEqual(page.locator("#live-decision-map .dm-node-claim").count(), 1)
            self.assertGreaterEqual(page.locator("#live-decision-map .dm-edge.is-contributes").count(), 2)
            self._assert_no_body_overflow(page)
            self._capture(page, capture_dir, "02-live-proposals.png")

            manifest = _seed_review(session, manifest)
            store.update_job("decision-map-demo", phase="layer2", progress=70)
            page.reload(wait_until="domcontentloaded")
            page.locator("#live-decision-map .dm-shell").wait_for()
            page.wait_for_function("document.querySelectorAll('#live-decision-map .dm-node-claim.is-verified').length === 1")
            self.assertEqual(page.locator("#live-decision-map .dm-stage").text_content(), "Claims under review")
            self.assertEqual(page.locator("#live-decision-map .dm-node-claim.is-verified").count(), 1)
            self._capture(page, capture_dir, "03-live-review.png")

            _seed_complete(session, manifest)
            store.update_job(
                "decision-map-demo",
                status="completed",
                phase="complete",
                progress=1,
                summary="High-confidence evidence map and report are ready.",
                finished_at=time.time(),
                exit_code=0,
            )
            page.reload(wait_until="domcontentloaded")
            page.locator("#live-decision-map .dm-shell").wait_for()
            page.wait_for_function("document.querySelectorAll('#live-decision-map .dm-node-decision').length === 1")
            page.wait_for_function("document.querySelectorAll('#live-decision-map .dm-edge.is-synthesizes').length >= 1")
            self.assertEqual(page.locator("#live-decision-map .dm-stage").text_content(), "Decision complete")
            self.assertEqual(page.locator("#live-decision-map .dm-metric").first.locator("strong").inner_text(), "High")
            self.assertEqual(
                page.locator("#live-decision-map .dm-metric").nth(1).locator("strong").inner_text(),
                "High / High",
            )
            self.assertEqual(page.locator("#live-decision-map .dm-node-decision").count(), 1)
            self.assertGreaterEqual(page.locator("#live-decision-map .dm-edge.is-synthesizes").count(), 1)
            page.locator("#live-decision-map .dm-ledger summary").click()
            self.assertEqual(page.locator("#live-decision-map .dm-ledger tbody tr").count(), 1)
            self._assert_no_body_overflow(page)
            self._capture(page, capture_dir, "04-live-complete.png")

            page.goto(
                f"{base_url}/api/jobs/decision-map-demo/artifacts/report.html",
                wait_until="networkidle",
            )
            page.locator(".report-decision-map .dm-shell").wait_for()
            self.assertEqual(page.locator(".report-decision-map .dm-stage").text_content(), "Decision complete")
            self.assertEqual(page.locator(".lineage-shell").count(), 1)
            self.assertEqual(page.locator("script[src], link[rel='stylesheet']").count(), 0)
            self._assert_no_body_overflow(page)
            self._capture(page, capture_dir, "05-self-contained-report.png")

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="networkidle")
            page.locator(".report-decision-map .dm-shell").wait_for()
            self._assert_no_body_overflow(page)
            self._capture(page, capture_dir, "06-report-mobile.png")
            self.assertEqual(console_errors, [])
            self.assertEqual(unexpected_requests, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
