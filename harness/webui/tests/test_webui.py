from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.scripts import attachments as attachment_module
from harness.scripts import run_moa
from harness.webui.app import _server_thread_count, create_app
from harness.webui.github import _run_gh, parse_repo_pointer
from harness.webui.monitoring import ProviderHealthMonitor
from harness.webui import providers as web_providers
from harness.webui import prompt_coach
from harness.webui.store import redact_event_text
from harness.webui.worker import JobWorker


def _minimal_text_pdf(text: str) -> bytes:
    """Build a tiny standards-compliant PDF without a fixture dependency."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


class WebUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(
            {
                "TESTING": True,
                "START_WORKER": False,
                "DATABASE": str(self.root / "webui.sqlite3"),
                "UPLOAD_DIR": str(self.root / "data" / "uploads"),
                "GITHUB_WORKSPACE_DIR": str(
                    self.root / "data" / "workspaces" / "github"
                ),
                "BRIEF_WORKSPACE_DIR": str(
                    self.root / "data" / "workspaces" / "brief"
                ),
                "LOCAL_FONT_DIR": str(self.root / "data" / "fonts"),
                "WORKSPACE_ROOTS": [self.root],
                "SSE_POLL_SECONDS": 0.01,
            }
        )
        self.client = self.app.test_client()
        claimed = self.client.post(
            "/api/profiles",
            json={"id": "browser_123", "display_name": "Test operator"},
        )
        self.assertEqual(claimed.status_code, 200)

    def _prepare_job_attachments(self, created) -> None:
        job_id = created.get_json()["id"]
        store = self.app.extensions["moa_store"]
        worker = self.app.extensions["moa_worker"]
        worker._prepare_attachments(store.get_job(job_id))

    def test_waitress_thread_pool_scales_with_host_cpu_count(self):
        with (
            patch("harness.webui.app.os.cpu_count", return_value=36),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("MOA_WEBUI_THREADS", None)
            self.assertEqual(_server_thread_count(), 32)
            os.environ["MOA_WEBUI_THREADS"] = "16"
            self.assertEqual(_server_thread_count(), 16)
            os.environ["MOA_WEBUI_THREADS"] = "invalid"
            self.assertEqual(_server_thread_count(), 32)

    def test_provider_monitor_alerts_once_and_realerts_after_recovery(self):
        state = {
            "status": "needs_auth",
            "authenticated": False,
            "detail": "Please sign in",
        }
        captured = []
        monitor = ProviderHealthMonitor(
            lambda: [
                {
                    "id": "agy",
                    "label": "Antigravity",
                    **state,
                }
            ],
            interval_seconds=3600,
            capture=lambda provider: captured.append(dict(provider)) or True,
        )

        monitor.run_once()
        monitor.run_once()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["id"], "agy")

        state.update(status="ready", authenticated=True, detail="ok")
        monitor.run_once()
        state.update(
            status="needs_auth",
            authenticated=False,
            detail="Please sign in",
        )
        monitor.run_once()
        self.assertEqual(len(captured), 2)

    def test_provider_monitor_retries_when_alert_delivery_is_unavailable(self):
        attempts = []
        monitor = ProviderHealthMonitor(
            lambda: [
                {
                    "id": "agy",
                    "label": "Antigravity",
                    "status": "needs_auth",
                    "authenticated": False,
                    "detail": "Please sign in",
                }
            ],
            capture=lambda provider: attempts.append(provider["id"]) or False,
        )

        monitor.run_once()
        monitor.run_once()
        self.assertEqual(attempts, ["agy", "agy"])

    def test_agy_routes_are_gated_by_the_live_account_catalog(self):
        with (
            patch.dict(
                web_providers.PROVIDER_META["agy"],
                {
                    "binary": lambda: "agy",
                    "probe": lambda: (True, "account ready"),
                },
            ),
            patch.object(web_providers.shutil, "which", return_value="/usr/bin/agy"),
            patch.object(
                web_providers,
                "_run",
                return_value=(True, "agy 1.1.7"),
            ),
            patch.object(
                web_providers.agy,
                "list_models",
                return_value=(
                    True,
                    ["gemini-3.1-pro-high", "gemini-3.1-pro-low"],
                    "2 models available",
                ),
            ),
        ):
            result = web_providers.probe_provider("agy")

        routes = {route["id"]: route for route in result["routes"]}
        self.assertTrue(result["authenticated"])
        self.assertTrue(routes["agy-gemini-pro"]["available"])
        self.assertNotIn("agy-gemini-flash", routes)

    def test_browser_event_text_redacts_operator_identity_and_home_path(self):
        redacted = redact_event_text(
            "Logged in as operator@example.com; "
            "session /home/operator/.local/share/moa-x"
        )
        self.assertEqual(
            redacted,
            "Logged in as [redacted email]; session ~/.local/share/moa-x",
        )

    def test_log_endpoint_redacts_existing_operator_details(self):
        session = self.root / ".moa" / "redacted-log"
        session.mkdir(parents=True)
        (session / "webui.log").write_text(
            "Logged in as operator@example.com\n"
            "repo: /home/operator/project\n",
            encoding="utf-8",
        )
        self.app.extensions["moa_store"].insert_job(
            {
                "id": "redacted-log",
                "profile_id": "browser_123",
                "title": "Redacted log",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Keep operator details private.",
                "status": "failed",
                "phase": "failed",
                "config": {},
                "created_at": 1,
            }
        )
        lines = self.client.get("/api/jobs/redacted-log/logs").get_json()["lines"]
        self.assertEqual(
            lines,
            ["Logged in as [redacted email]", "repo: ~/project"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_local_fonts_are_optional_and_strictly_allowlisted(self):
        page = self.client.get("/")
        self.assertNotIn(b"/local-assets/fonts.css", page.data)
        self.assertEqual(
            self.client.get("/local-assets/fonts.css").status_code, 404
        )

        font_dir = Path(self.app.config["LOCAL_FONT_DIR"])
        font_dir.mkdir(parents=True)
        (font_dir / "GothamOffice-Regular.woff2").write_bytes(b"regular-font")
        (font_dir / "GothamOffice-Bold.woff2").write_bytes(b"bold-font")

        page = self.client.get("/")
        self.assertIn(b"/local-assets/fonts.css", page.data)
        self.assertIn(b"new FontFace", page.data)
        self.assertIn(b"gotham-office-ready", page.data)
        stylesheet = self.client.get("/local-assets/fonts.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(b'MoAX Gotham Office', stylesheet.data)
        self.assertIn(b"gotham-office-ready", stylesheet.data)
        font = self.client.get(
            "/local-assets/fonts/GothamOffice-Regular.woff2"
        )
        self.assertEqual(font.status_code, 200)
        self.assertEqual(font.mimetype, "font/woff2")
        self.assertEqual(font.data, b"regular-font")
        font.close()
        self.assertEqual(
            self.client.get(
                "/local-assets/fonts/../private.otf"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/local-assets/fonts/GothamOffice-Italic.woff2"
            ).status_code,
            404,
        )

    def test_fable_warning_modal_is_present_without_exposing_a_server_secret(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="fable-warning-dialog"', page.data)
        self.assertIn(b'id="fable-warning-password"', page.data)
        self.assertIn(b"substantial hit to shared limits", page.data)
        self.assertNotIn(b"driveline11", page.data)
        app_source = (
            Path(__file__).parents[1] / "static" / "js" / "app.js"
        ).read_text()
        self.assertIn('input.dataset.fableAuthorized !== "true"', app_source)
        self.assertIn('$("#launch-form").addEventListener("change"', app_source)
        self.assertIn("openFableWarning(fableInput);", app_source)

    def test_initial_views_expose_visible_accessible_loading_states(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        for message in (
            b"Checking provider accounts",
            b"Loading run archive",
            b"Loading latest runs",
            b"Checking active work",
            b"Loading proposer routes",
            b"Loading agent lanes",
            b"Connecting to live trace",
        ):
            self.assertIn(message, page.data)
        self.assertGreaterEqual(page.data.count(b'aria-busy="true"'), 8)
        self.assertIn(b"lab-independent-pixel.webp", page.data)
        self.assertIn(b"prefers-reduced-motion: reduce", page.data)
        self.assertIn(b'class="task-compose-layout"', page.data)
        self.assertIn(b'data-step-target="5"', page.data)
        self.assertEqual(page.data.count(b"data-optimized-loadout="), 3)
        self.assertNotIn(b"data-model-search=", page.data)
        self.assertIn(b'id="review-network"', page.data)

        app_source = (
            Path(__file__).parents[1] / "static" / "js" / "app.js"
        ).read_text()
        self.assertIn("const settle = async", app_source)
        self.assertIn('state.loading[key] = false', app_source)
        self.assertIn("setButtonLoading", app_source)
        self.assertIn("applyOptimizedRole", app_source)
        self.assertIn("window.MoaDecisionMap.render", app_source)
        self.assertIn(
            "if (!force && !state.detailJob?.artifacts?.decision_map) return;",
            app_source,
        )
        self.assertIn("if (state.detailJob?.id === jobId) {", app_source)
        self.assertIn("state.decisionMap = null;", app_source)
        self.assertIn(b'id="prompt-coach-button"', page.data)
        self.assertIn(b'id="prompt-coach-dialog"', page.data)
        self.assertIn(b"prompt-coach-teacher.webp", page.data)
        self.assertIn("analyzePrompt", app_source)
        self.assertIn("finalizePrompt", app_source)

    @patch("harness.webui.app.analyze_prompt")
    def test_prompt_helper_analyze_is_bounded_and_returns_model_metadata(
        self, analyze_prompt
    ):
        analyze_prompt.return_value = {
            "suitability": "needs_clarification",
            "score": 58,
            "summary": "Clarify the desired tradeoff.",
            "questions": [
                {
                    "id": "priority",
                    "prompt": "What matters most?",
                    "why": "It changes the plan.",
                    "options": [
                        {
                            "label": "Reliability",
                            "description": "Prefer safer changes.",
                            "recommended": True,
                        },
                        {
                            "label": "Speed",
                            "description": "Prefer the fastest path.",
                            "recommended": False,
                        },
                    ],
                    "allow_custom": True,
                }
            ],
            "model": {
                "provider": "codex",
                "model": "gpt-5.6-luna",
                "fallback": False,
            },
        }
        response = self.client.post(
            "/api/prompt-helper/analyze",
            json={
                "brief": "Improve billing reliability.",
                "context_mode": "github",
                "attachment_count": 2,
                "planning_depth": "thorough",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["questions"][0]["id"], "priority")
        self.assertEqual(response.get_json()["model"]["model"], "gpt-5.6-luna")
        analyze_prompt.assert_called_once_with(
            "Improve billing reliability.",
            context_mode="github",
            attachment_count=2,
            planning_depth="thorough",
        )

    @patch("harness.webui.app.finalize_prompt")
    def test_prompt_helper_finalize_returns_preview_without_mutating_job_state(
        self, finalize_prompt
    ):
        finalize_prompt.return_value = {
            "optimized_prompt": "Review billing reliability and prioritize safe fixes.",
            "changes": ["Added a decision criterion."],
            "assumptions": [],
            "remaining_risks": [],
            "model": {
                "provider": "agy",
                "model": "gemini-3.1-pro-high",
                "fallback": True,
            },
        }
        response = self.client.post(
            "/api/prompt-helper/finalize",
            json={
                "brief": "Improve billing.",
                "questions": [{"id": "priority"}],
                "answers": [{"question_id": "priority", "answer": "Reliability"}],
                "planning_depth": "quick",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("Review billing reliability", payload["optimized_prompt"])
        self.assertTrue(payload["model"]["fallback"])
        finalize_prompt.assert_called_once_with(
            "Improve billing.",
            questions=[{"id": "priority"}],
            answers=[{"question_id": "priority", "answer": "Reliability"}],
            context_mode=None,
            attachment_count=None,
            planning_depth="quick",
        )

    def test_prompt_helper_rejects_an_empty_brief(self):
        response = self.client.post(
            "/api/prompt-helper/analyze", json={"brief": "  "}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Add a task", response.get_json()["error"])

    def test_profile_and_job_lifecycle(self):
        profile = self.client.post(
            "/api/profiles",
            json={
                "id": "browser_123",
                "display_name": "Casey",
                "settings": {"compact_events": True},
            },
        )
        self.assertEqual(profile.status_code, 200)
        renamed = self.client.post(
            "/api/profiles", json={"id": "browser_123", "name": "Casey B"}
        )
        self.assertEqual(renamed.get_json()["settings"]["compact_events"], True)
        persisted = self.client.get("/api/profiles/browser_123")
        self.assertEqual(persisted.status_code, 200)
        self.assertEqual(persisted.get_json()["name"], "Casey B")

        created = self.client.post(
            "/api/jobs",
            json={
                "workspace": str(self.root),
                "goal": "Review this repository and propose a concise plan.",
                "profile_id": "browser_123",
                "proposers": ["codex", "sonnet"],
                "refiners": ["qwen"],
                "aggregator": "opus",
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        self.assertEqual(job["status"], "queued")
        scout = Path(job["session_dir"]) / "scout-brief.json"
        self.assertEqual(json.loads(scout.read_text())["repo_path"], str(self.root))

        listed = self.client.get("/api/jobs?profile_id=browser_123").get_json()
        self.assertEqual([item["id"] for item in listed["jobs"]], [job["id"]])
        detail = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertEqual(detail["config"]["aggregator"], "opus")

        cancelled = self.client.post(f"/api/jobs/{job['id']}/cancel")
        self.assertEqual(cancelled.status_code, 202)
        self.assertEqual(cancelled.get_json()["status"], "cancelled")

    def test_job_without_aggregator_uses_codex_sol(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "workspace": str(self.root),
                "goal": "Verify the curated aggregation default.",
                "proposers": ["agy-gemini-pro", "sonnet"],
                "refiners": ["qwen"],
            },
        )
        self.assertEqual(created.status_code, 201)
        detail = self.client.get(
            f"/api/jobs/{created.get_json()['id']}"
        ).get_json()
        self.assertEqual(detail["config"]["aggregator"], "codex-sol")

    def test_fable_is_rejected_outside_the_aggregator_layer(self):
        for field in ("proposers", "refiners"):
            payload = {
                "workspace": str(self.root),
                "goal": "Reject Fable in upstream layers.",
                "proposers": ["grok"],
                "refiners": ["deepseek"],
                "aggregator": "codex-sol",
            }
            payload[field] = ["fable"]
            response = self.client.post("/api/jobs", json=payload)
            self.assertEqual(response.status_code, 400)
            self.assertIn("aggregator-only", response.get_json()["error"])

    def test_fable_is_accepted_as_an_explicit_aggregator(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "workspace": str(self.root),
                "goal": "Use Fable only for final synthesis.",
                "proposers": ["grok"],
                "refiners": ["deepseek"],
                "aggregator": "fable",
            },
        )
        self.assertEqual(created.status_code, 201)
        detail = self.client.get(
            f"/api/jobs/{created.get_json()['id']}"
        ).get_json()
        self.assertEqual(detail["config"]["aggregator"], "fable")

    def test_kimi_is_rejected_for_new_runs(self):
        response = self.client.post(
            "/api/jobs",
            json={
                "workspace": str(self.root),
                "goal": "Do not launch the blocked Kimi route.",
                "proposers": ["grok"],
                "refiners": ["kimi"],
                "aggregator": "codex-sol",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("route is disabled", response.get_json()["error"])

    def test_runs_are_private_to_the_browser_that_submitted_them(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Keep this run private to its submitting browser.",
                "profile_id": "spoofed_owner",
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "opus",
            },
        )
        self.assertEqual(created.status_code, 201)
        owner_job = created.get_json()
        self.assertEqual(owner_job["profile_id"], "browser_123")
        session_dir = Path(owner_job["session_dir"])
        (session_dir / "webui.log").write_text(
            "private worker output", encoding="utf-8"
        )
        (session_dir / "report.html").write_text(
            "<h1>Private report</h1>", encoding="utf-8"
        )

        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/jobs").status_code, 401)

        other = self.app.test_client()
        claimed = other.post(
            "/api/profiles",
            json={"id": "browser_456", "display_name": "Other operator"},
        )
        self.assertEqual(claimed.status_code, 200)
        cookie = claimed.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        # A caller cannot use query or body profile IDs to impersonate an owner.
        other_jobs = other.get(
            "/api/jobs?profile_id=browser_123"
        ).get_json()["jobs"]
        self.assertEqual(other_jobs, [])
        spoofed = other.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "This belongs to the second browser.",
                "profile_id": "browser_123",
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "opus",
            },
        )
        self.assertEqual(spoofed.status_code, 201)
        self.assertEqual(spoofed.get_json()["profile_id"], "browser_456")

        private_paths = (
            f"/api/jobs/{owner_job['id']}",
            f"/api/jobs/{owner_job['id']}/logs",
            f"/api/jobs/{owner_job['id']}/events",
            f"/api/jobs/{owner_job['id']}/artifacts/report.html",
            "/api/profiles/browser_123",
        )
        for path in private_paths:
            with self.subTest(path=path):
                self.assertEqual(other.get(path).status_code, 404)
        self.assertEqual(
            other.post(f"/api/jobs/{owner_job['id']}/cancel").status_code,
            404,
        )
        self.assertEqual(
            other.post(f"/api/jobs/{owner_job['id']}/redispatch").status_code,
            404,
        )

    def test_completed_report_share_link_is_public_and_revocable(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Share this completed report.",
                "proposers": ["codex"],
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        session = Path(job["session_dir"])
        (session / "manifest.json").write_text(
            json.dumps({"session_id": job["id"], "layer1": [], "layer2": []}),
            encoding="utf-8",
        )
        (session / "report.html").write_text(
            "<h1>Shared report</h1>", encoding="utf-8"
        )
        self.app.extensions["moa_store"].update_job(
            job["id"], status="completed", phase="complete", progress=1
        )

        shared = self.client.post(f"/api/jobs/{job['id']}/share")
        self.assertEqual(shared.status_code, 201)
        link = shared.get_json()["url"]
        self.assertRegex(link, r"^/shared/reports/[A-Za-z0-9_-]{32,160}$")

        anonymous = self.app.test_client()
        report = anonymous.get(link)
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Shared report", report.data)
        self.assertEqual(report.headers["Cache-Control"], "private, no-store")
        self.assertEqual(report.headers["X-Robots-Tag"], "noindex, nofollow")
        report.close()

        revoked = self.client.delete(f"/api/jobs/{job['id']}/share")
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.get_json()["revoked"], 1)
        self.assertEqual(anonymous.get(link).status_code, 404)

    def test_report_requires_active_manifest_freshness_everywhere(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Do not expose an absent or stale report.",
                "proposers": ["codex"],
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        session = Path(job["session_dir"])
        manifest_path = session / "manifest.json"
        report_path = session / "report.html"
        manifest = {
            "session_id": job["id"],
            "layer1": [],
            "layer2": [],
            "summary": {},
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.app.extensions["moa_store"].update_job(
            job["id"], status="completed", phase="complete", progress=1
        )

        no_report = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("report", no_report["artifacts"])
        self.assertEqual(
            self.client.get(
                f"/api/jobs/{job['id']}/artifacts/report.html"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/api/jobs/{job['id']}/share").status_code, 409
        )

        report_path.write_text("<h1>Fresh report</h1>", encoding="utf-8")
        manifest_ns = manifest_path.stat().st_mtime_ns
        fresh_ns = manifest_ns + 1_000_000
        os.utime(report_path, ns=(fresh_ns, fresh_ns))
        fresh = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertIn("report", fresh["artifacts"])
        served = self.client.get(
            f"/api/jobs/{job['id']}/artifacts/report.html"
        )
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, b"<h1>Fresh report</h1>")

        shared = self.client.post(f"/api/jobs/{job['id']}/share")
        self.assertEqual(shared.status_code, 201)
        public_link = shared.get_json()["url"]
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(public_link).status_code, 200)

        manifest["summary"] = {"report_regeneration": "failed"}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        stale_ns = fresh_ns + 1_000_000
        os.utime(manifest_path, ns=(stale_ns, stale_ns))
        stale = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("report", stale["artifacts"])
        self.assertEqual(
            self.client.get(
                f"/api/jobs/{job['id']}/artifacts/report.html"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/api/jobs/{job['id']}/share").status_code, 409
        )
        self.assertEqual(anonymous.get(public_link).status_code, 404)

    def test_report_requires_rerender_after_newer_active_final_inputs(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Keep the rendered report behind every active input.",
                "proposers": ["codex"],
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        session = Path(job["session_dir"])
        manifest_path = session / "manifest.json"
        report_path = session / "report.html"
        final_md_path = session / "final-plan.md"
        final_json_path = session / "final-plan.json"
        map_path = session / "decision-map.json"
        map_payload = b'{"version":1,"stage":"final"}'
        manifest = {
            "session_id": job["id"],
            "layer1": [],
            "layer2": [],
            "summary": {},
            "artifacts": {
                "decision_map": {
                    "path": "decision-map.json",
                    "version": 1,
                    "sha256": hashlib.sha256(map_payload).hexdigest(),
                }
            },
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        map_path.write_bytes(map_payload)
        manifest_ns = manifest_path.stat().st_mtime_ns
        report_ns = manifest_ns + 1_000_000
        inputs_ns = report_ns + 1_000_000
        rerender_ns = inputs_ns + 1_000_000
        os.utime(map_path, ns=(manifest_ns, manifest_ns))
        report_path.write_text("<h1>Initial report</h1>", encoding="utf-8")
        os.utime(report_path, ns=(report_ns, report_ns))
        self.app.extensions["moa_store"].update_job(
            job["id"], status="completed", phase="complete", progress=1
        )

        endpoint = f"/api/jobs/{job['id']}/artifacts/report.html"
        initial = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertIn("report", initial["artifacts"])
        self.assertEqual(self.client.get(endpoint).status_code, 200)
        shared = self.client.post(f"/api/jobs/{job['id']}/share")
        self.assertEqual(shared.status_code, 201)
        public_link = shared.get_json()["url"]
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(public_link).status_code, 200)

        final_md_path.write_text("# New final plan", encoding="utf-8")
        final_json_path.write_text('{"steps":[]}', encoding="utf-8")
        map_path.write_bytes(map_payload)
        for path in (final_md_path, final_json_path, map_path):
            os.utime(path, ns=(inputs_ns, inputs_ns))

        stale = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("report", stale["artifacts"])
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/jobs/{job['id']}/share").status_code, 409
        )
        self.assertEqual(anonymous.get(public_link).status_code, 404)

        report_path.write_text("<h1>Rerendered report</h1>", encoding="utf-8")
        os.utime(report_path, ns=(rerender_ns, rerender_ns))
        recovered = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertIn("report", recovered["artifacts"])
        served = self.client.get(endpoint)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, b"<h1>Rerendered report</h1>")
        self.assertEqual(anonymous.get(public_link).status_code, 200)
        self.assertEqual(
            self.client.post(f"/api/jobs/{job['id']}/share").status_code, 201
        )

        map_path.unlink()
        missing_map = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("report", missing_map["artifacts"])
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(anonymous.get(public_link).status_code, 404)

    def test_report_ignores_final_inputs_rejected_by_shared_staleness_predicate(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Exclude copied final output after a failed retry.",
                "proposers": ["codex"],
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        session = Path(job["session_dir"])
        manifest_path = session / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "session_id": job["id"],
                    "layer1": [],
                    "layer2": [],
                    "layer3": [{"success": False}],
                    "summary": {},
                }
            ),
            encoding="utf-8",
        )
        manifest_ns = manifest_path.stat().st_mtime_ns
        report_ns = manifest_ns + 1_000_000
        stale_final_ns = report_ns + 1_000_000
        report_path = session / "report.html"
        report_path.write_text("<h1>Partial report</h1>", encoding="utf-8")
        os.utime(report_path, ns=(report_ns, report_ns))
        for filename in ("final-plan.md", "final-plan.json"):
            path = session / filename
            path.write_text("copied stale output", encoding="utf-8")
            os.utime(path, ns=(stale_final_ns, stale_final_ns))
        self.app.extensions["moa_store"].update_job(
            job["id"], status="completed", phase="complete", progress=1
        )

        view = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertIn("report", view["artifacts"])
        self.assertNotIn("final_plan", view["artifacts"])
        served = self.client.get(
            f"/api/jobs/{job['id']}/artifacts/report.html"
        )
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, b"<h1>Partial report</h1>")
        self.assertEqual(
            self.client.post(f"/api/jobs/{job['id']}/share").status_code, 201
        )

    def test_decision_map_requires_matching_active_manifest_receipt(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Verify the decision-map receipt before exposure.",
                "proposers": ["codex"],
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        session = Path(job["session_dir"])
        manifest_path = session / "manifest.json"
        map_path = session / "decision-map.json"
        payload = b'{"version":1,"stage":"review"}'
        map_path.write_bytes(payload)
        manifest = {
            "session_id": job["id"],
            "layer1": [],
            "layer2": [],
            "summary": {"evidence_quality": {"level": "strong"}},
        }

        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        missing = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("decision_map", missing["artifacts"])
        endpoint = f"/api/jobs/{job['id']}/artifacts/decision-map.json"
        self.assertEqual(self.client.get(endpoint).status_code, 404)

        manifest["artifacts"] = {
            "decision_map": {
                "path": "decision-map.json",
                "version": 1,
                "sha256": "0" * 64,
            }
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        mismatch = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("decision_map", mismatch["artifacts"])
        self.assertEqual(self.client.get(endpoint).status_code, 404)

        manifest["artifacts"]["decision_map"]["sha256"] = hashlib.sha256(
            payload
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertIn("decision_map", verified["artifacts"])
        served = self.client.get(endpoint)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, "application/json")
        self.assertEqual(served.data, payload)

        map_path.write_bytes(b'{"version":1,"stage":"tampered"}')
        tampered = self.client.get(f"/api/jobs/{job['id']}").get_json()
        self.assertNotIn("decision_map", tampered["artifacts"])
        self.assertEqual(self.client.get(endpoint).status_code, 404)

    def test_brief_only_job_uses_an_isolated_managed_workspace(self):
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Research this brief without a repository.",
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "opus",
            },
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()
        workspace = (
            self.root / "data" / "workspaces" / "brief" / job["id"]
        ).resolve()
        self.assertEqual(Path(job["workspace"]), workspace)
        self.assertTrue(workspace.is_dir())
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            Path(job["session_dir"]),
            workspace / ".moa" / job["id"],
        )
        scout = json.loads(
            (Path(job["session_dir"]) / "scout-brief.json").read_text()
        )
        self.assertEqual(scout["repo_path"], str(workspace))
        self.assertEqual(scout["source"], {"type": "brief"})

    def test_workspace_escape_is_rejected(self):
        outside = Path(self.temp.name).parent
        response = self.client.post(
            "/api/jobs", json={"workspace": str(outside), "goal": "Nope"}
        )
        self.assertEqual(response.status_code, 400)

    def test_durable_upload_is_snapshotted_into_a_job(self):
        uploaded = self.client.post(
            "/api/uploads",
            data={
                "profile_id": "browser_123",
                "file": (io.BytesIO(b"# Research notes\nA durable input."), "../notes.md"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        upload = uploaded.get_json()["uploads"][0]
        self.assertEqual(upload["original_name"], "notes.md")
        self.assertNotIn("stored_path", upload)
        with self.client.get(upload["url"]) as download:
            self.assertEqual(download.data, b"# Research notes\nA durable input.")

        created = self.client.post(
            "/api/jobs",
            json={
                "workspace": str(self.root),
                "goal": "Use the attached research notes.",
                "profile_id": "browser_123",
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "opus",
                "attachments": [upload["id"]],
            },
        )
        self.assertEqual(created.status_code, 201)
        self._prepare_job_attachments(created)
        session = Path(created.get_json()["session_dir"])
        snapshot = session / "inputs" / "01-notes.md"
        self.assertEqual(snapshot.read_bytes(), b"# Research notes\nA durable input.")
        scout = json.loads((session / "scout-brief.json").read_text())
        self.assertEqual(scout["uploaded_files"][0]["path"], "inputs/01-notes.md")
        context = scout["attachment_context"]
        self.assertEqual(context["source_count"], 1)
        self.assertIn("# Research notes", context["markdown"])
        self.assertEqual(
            (session / "attachment-context.md").read_text(),
            context["markdown"],
        )

    def test_pdf_upload_is_extracted_and_inlined_for_every_layer(self):
        uploaded = self.client.post(
            "/api/uploads",
            data={
                "file": (
                    io.BytesIO(
                        _minimal_text_pdf(
                            "The binding allocation is fifty percent from the first dollar."
                        )
                    ),
                    "agreement.pdf",
                ),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        upload_id = uploaded.get_json()["uploads"][0]["id"]
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Review the attached agreement.",
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "opus",
                "attachments": [upload_id],
            },
        )
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self._prepare_job_attachments(created)
        session = Path(created.get_json()["session_dir"])
        scout = json.loads((session / "scout-brief.json").read_text())
        context = scout["attachment_context"]
        self.assertEqual(context["sources"][0]["pages"], 1)
        self.assertIn("### Page 1", context["markdown"])
        self.assertIn("fifty percent from the first dollar", context["markdown"])
        self.assertIn("do not assume filesystem access", context["markdown"])
        progress_events = self.app.extensions["moa_store"].events_after(
            created.get_json()["id"]
        )
        page_events = [
            event
            for event in progress_events
            if event["kind"] == "attachment-progress"
            and event["data"].get("stage") == "extracting"
        ]
        self.assertEqual(page_events[0]["data"]["page_number"], 1)
        self.assertEqual(page_events[0]["data"]["page_count"], 1)
        proposer_prompt = run_moa._build_proposer_prompt(
            scout, {"type": "object"}, "codex"
        )
        refiner_prompt = run_moa._build_refiner_prompt(
            scout, [], "codex-sol", {"type": "object"}
        )
        synthesis = run_moa.write_synthesis_input(
            scout_brief=scout,
            layer1=[],
            layer2=[],
            session_dir=session,
            proposer_agent_ids=(),
            refiner_agent_ids=(),
            aggregator_model="opus",
        ).read_text()
        for payload in (proposer_prompt, refiner_prompt, synthesis):
            self.assertIn("fifty percent from the first dollar", payload)

    @patch(
        "harness.scripts.attachments._ocr_pdf_page",
        return_value="The scanned patent describes multi-target tracking.",
    )
    def test_scanned_pdf_upload_is_ocr_and_inlined(self, ocr_pdf_page):
        uploaded = self.client.post(
            "/api/uploads",
            data={
                "file": (
                    io.BytesIO(_minimal_text_pdf("")),
                    "scanned-patent.pdf",
                ),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        upload_id = uploaded.get_json()["uploads"][0]["id"]
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Review the attached patent.",
                "proposers": ["codex"],
                "attachments": [upload_id],
            },
        )
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self._prepare_job_attachments(created)
        session = Path(created.get_json()["session_dir"])
        scout = json.loads((session / "scout-brief.json").read_text())
        source = scout["attachment_context"]["sources"][0]
        self.assertEqual(source["kind"], "pdf-ocr")
        self.assertEqual(source["pages"], 1)
        self.assertEqual(source["ocr_pages"], 1)
        self.assertEqual(source["ocr_language"], "eng")
        self.assertIn(
            "The scanned patent describes multi-target tracking.",
            scout["attachment_context"]["markdown"],
        )
        ocr_pdf_page.assert_called_once()

    def test_scanned_pdf_pages_ocr_in_parallel_and_reassemble_in_order(self):
        active = 0
        max_active = 0
        active_lock = threading.Lock()
        all_started = threading.Barrier(4, timeout=2)
        updates = []

        def fake_ocr(_path, page_number, page_count, progress, _cancelled):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            progress(
                {
                    "stage": "rendering",
                    "page_number": page_number,
                    "page_count": page_count,
                }
            )
            all_started.wait()
            time.sleep((5 - page_number) * 0.01)
            progress(
                {
                    "stage": "recognizing",
                    "page_number": page_number,
                    "page_count": page_count,
                }
            )
            with active_lock:
                active -= 1
            return f"OCR text from page {page_number}"

        reader = SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=lambda: "") for _ in range(4)],
        )
        with (
            patch("pypdf.PdfReader", return_value=reader),
            patch.object(
                attachment_module,
                "_ocr_pdf_page",
                side_effect=fake_ocr,
            ),
            patch.dict(
                attachment_module.os.environ,
                {
                    "MOA_ATTACHMENT_OCR_WORKERS": "4",
                    "MOA_ATTACHMENT_OCR_THREADS_PER_WORKER": "1",
                },
            ),
        ):
            text, page_count, ocr_pages = attachment_module._extract_pdf(
                Path("parallel-fixture.pdf"),
                progress=updates.append,
            )

        self.assertEqual(max_active, 4)
        self.assertEqual(page_count, 4)
        self.assertEqual(ocr_pages, 4)
        self.assertEqual(
            [text.index(f"### Page {page}") for page in range(1, 5)],
            sorted(text.index(f"### Page {page}") for page in range(1, 5)),
        )
        starting = [
            update for update in updates if update["stage"] == "ocr-starting"
        ]
        self.assertEqual(starting[0]["worker_count"], 4)
        completed = [
            update["completed_pages"]
            for update in updates
            if update["stage"] == "ocr-complete"
        ]
        self.assertEqual(completed, [1, 2, 3, 4])

    def test_parallel_pdf_ocr_propagates_page_failure(self):
        reader = SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=lambda: "") for _ in range(3)],
        )

        def fake_ocr(
            _path,
            page_number,
            _page_count,
            _progress,
            _cancelled,
        ):
            if page_number == 2:
                raise attachment_module.AttachmentError("page 2 failed")
            return f"page {page_number}"

        with (
            patch("pypdf.PdfReader", return_value=reader),
            patch.object(
                attachment_module,
                "_ocr_pdf_page",
                side_effect=fake_ocr,
            ),
            patch.dict(
                attachment_module.os.environ,
                {"MOA_ATTACHMENT_OCR_WORKERS": "3"},
            ),
        ):
            with self.assertRaisesRegex(
                attachment_module.AttachmentError,
                "page 2 failed",
            ):
                attachment_module._extract_pdf(Path("failing-fixture.pdf"))

    def test_parallel_pdf_ocr_stops_scheduling_after_cancellation(self):
        calls = []
        reader = SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=lambda: "") for _ in range(20)],
        )

        def fake_ocr(
            _path,
            page_number,
            _page_count,
            _progress,
            _cancelled,
        ):
            calls.append(page_number)
            time.sleep(0.02)
            return f"page {page_number}"

        with (
            patch("pypdf.PdfReader", return_value=reader),
            patch.object(
                attachment_module,
                "_ocr_pdf_page",
                side_effect=fake_ocr,
            ),
            patch.dict(
                attachment_module.os.environ,
                {"MOA_ATTACHMENT_OCR_WORKERS": "2"},
            ),
        ):
            with self.assertRaises(InterruptedError):
                attachment_module._extract_pdf(
                    Path("cancelled-fixture.pdf"),
                    cancelled=lambda: len(calls) >= 2,
                )
        self.assertEqual(sorted(calls), [1, 2])

    def test_pdf_ocr_worker_default_uses_host_cpu_budget(self):
        with (
            patch.object(attachment_module.os, "cpu_count", return_value=36),
            patch.dict(attachment_module.os.environ, {}, clear=False),
        ):
            attachment_module.os.environ.pop(
                "MOA_ATTACHMENT_OCR_WORKERS",
                None,
            )
            attachment_module.os.environ.pop(
                "MOA_ATTACHMENT_OCR_THREADS_PER_WORKER",
                None,
            )
            self.assertEqual(attachment_module._ocr_worker_count(319), 12)
            self.assertEqual(attachment_module._ocr_worker_count(5), 5)

    def test_tesseract_receives_per_worker_thread_ceiling(self):
        completed = SimpleNamespace(returncode=0, stdout="recognized", stderr="")
        with (
            patch.object(
                attachment_module.shutil,
                "which",
                return_value="/usr/bin/tesseract",
            ),
            patch.object(
                attachment_module.subprocess,
                "run",
                return_value=completed,
            ) as run,
            patch.dict(
                attachment_module.os.environ,
                {"MOA_ATTACHMENT_OCR_THREADS_PER_WORKER": "2"},
            ),
        ):
            text = attachment_module._ocr_image(
                Path("fixture.png"),
                source_name="fixture.png",
            )
        self.assertEqual(text, "recognized")
        self.assertEqual(run.call_args.kwargs["env"]["OMP_THREAD_LIMIT"], "2")

    @patch(
        "harness.scripts.attachments._extract_image",
        return_value="Chart title: Weekly velocity. Peak value: 94 mph.",
    )
    def test_image_upload_ocr_is_inlined(self, extract_image):
        uploaded = self.client.post(
            "/api/uploads",
            data={"file": (io.BytesIO(b"fixture-image"), "chart.png")},
            content_type="multipart/form-data",
        )
        upload_id = uploaded.get_json()["uploads"][0]["id"]
        created = self.client.post(
            "/api/jobs",
            json={
                "source_mode": "brief",
                "goal": "Review the attached chart.",
                "proposers": ["codex"],
                "attachments": [upload_id],
            },
        )
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self._prepare_job_attachments(created)
        session = Path(created.get_json()["session_dir"])
        scout = json.loads((session / "scout-brief.json").read_text())
        context = scout["attachment_context"]
        self.assertEqual(context["sources"][0]["kind"], "image-ocr")
        self.assertIn("Peak value: 94 mph", context["markdown"])
        extract_image.assert_called_once()

    def test_github_pointer_is_exactly_allowlisted_and_persisted(self):
        with self.assertRaises(ValueError):
            parse_repo_pointer("other-org/private-repo")
        with self.assertRaises(ValueError):
            parse_repo_pointer("DrivelineResearch/private-repo")

        target = (
            self.root
            / "data"
            / "workspaces"
            / "github"
            / "drivelineresearch"
            / "player-benchmark"
        )
        (target / ".git").mkdir(parents=True)
        clone_result = {
            "owner": "drivelineresearch",
            "repo": "player-benchmark",
            "path": str(target),
            "remote_url": "https://github.com/drivelineresearch/player-benchmark",
            "created": True,
            "git_ref": "main",
        }
        with patch(
            "harness.webui.app.clone_repository",
            return_value=clone_result,
        ):
            response = self.client.post(
                "/api/workspaces/github",
                json={
                    "repo": "drivelineresearch/player-benchmark",
                    "profile_id": "browser_123",
                },
            )
            job_response = self.client.post(
                "/api/jobs",
                json={
                    "workspace": str(self.root),
                    "goal": "Review the selected GitHub workspace.",
                    "profile_id": "browser_123",
                    "source_mode": "github",
                    "github_repository": "drivelineresearch/player-benchmark",
                    "github_ref": "main",
                    "proposers": ["codex"],
                    "refiners": ["qwen"],
                    "aggregator": "opus",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["path"], str(target))
        self.assertEqual(job_response.status_code, 201)
        job = job_response.get_json()
        self.assertEqual(job["workspace"], str(target))
        scout = json.loads(
            (Path(job["session_dir"]) / "scout-brief.json").read_text()
        )
        self.assertEqual(
            scout["source"]["repository"],
            "drivelineresearch/player-benchmark",
        )
        rejected = self.client.post(
            "/api/workspaces/github", json={"repo": "evil/player-benchmark"}
        )
        self.assertEqual(rejected.status_code, 400)

    def test_github_owner_allowlist_is_configurable(self):
        self.assertEqual(
            parse_repo_pointer("example-org/public-repo", "example-org"),
            ("example-org", "public-repo"),
        )
        with self.assertRaises(ValueError):
            parse_repo_pointer("drivelineresearch/moa-x", "example-org")
        self.app.config["GITHUB_OWNER"] = "example-org"
        with patch(
            "harness.webui.app.list_repositories", return_value=[]
        ) as list_repositories:
            response = self.client.get("/api/github/repos")
        self.assertEqual(response.get_json()["owner"], "example-org")
        list_repositories.assert_called_once_with("example-org")
        self.assertIn(b"example-org", self.client.get("/new").data)

    @patch("harness.webui.github.shutil.which", return_value="/usr/bin/gh")
    @patch("harness.webui.github.subprocess.run")
    def test_github_cli_never_uses_a_shell(self, run, _which):
        run.return_value = subprocess.CompletedProcess(
            ["gh"], 0, stdout="[]", stderr=""
        )
        result = _run_gh(
            ["repo", "list", "drivelineresearch", "--json", "name"], timeout=3
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.args[0][0], "gh")

    def test_worker_builds_phase_command_and_scoped_model_environment(self):
        worker = self.app.extensions["moa_worker"]
        job = {
            "id": "fixture",
            "workspace": str(self.root),
            "session_dir": str(self.root / ".moa" / "fixture"),
            "config": {
                "proposers": ["codex", "agy-gemini-pro"],
                "refiners": ["qwen"],
                "aggregator": "opus",
                "options": {
                    "model_overrides": {"agy-gemini-pro": "gemini-3.1-pro-low"},
                    "effort_overrides": {
                        "agy-gemini-pro": "high",
                        "unsafe": "high; touch /tmp/nope",
                    },
                },
            },
        }
        command = worker._command(job, "layer1")
        self.assertIn("--proposers", command)
        self.assertNotIn("gemini-3.1-pro-low", command)
        env = JobWorker._environment(job)
        self.assertEqual(
            env["MOA_AGY_GEMINI_PRO_MODEL"], "gemini-3.1-pro-low"
        )
        self.assertNotIn("MOA_AGY_GEMINI_PRO_EFFORT", env)
        self.assertNotIn("MOA_UNSAFE_EFFORT", env)

    def test_worker_explains_zero_success_layer1_before_review(self):
        session = self.root / ".moa" / "failed-layer1"
        session.mkdir(parents=True)
        (session / "layer1-manifest.json").write_text(
            json.dumps(
                {
                    "layer1": [
                        {
                            "agent_id": "sonnet",
                            "success": False,
                            "schema_valid": True,
                            "workspace_mutations": ["brain/state/session.json"],
                        },
                        {
                            "agent_id": "codex",
                            "success": False,
                            "schema_valid": True,
                            "workspace_mutations": ["brain/state/session.json"],
                        },
                    ]
                }
            )
        )
        diagnostic = JobWorker._manifest_diagnostic(
            {"session_dir": str(session)}, "layer1"
        )
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["accepted"], 0)
        self.assertEqual(diagnostic["rejected"], 2)
        self.assertEqual(
            diagnostic["workspace_mutation_agents"], ["sonnet", "codex"]
        )
        self.assertIn("repository files changed during analysis", diagnostic["message"])
        self.assertIn("review cannot start", diagnostic["message"])

    def test_worker_blocks_an_empty_layer1_checkpoint(self):
        session = self.root / ".moa" / "empty-layer1"
        session.mkdir(parents=True)
        (session / "layer1-manifest.json").write_text(
            json.dumps({"layer1": []})
        )
        diagnostic = JobWorker._manifest_diagnostic(
            {
                "session_dir": str(session),
                "config": {"proposers": ["codex", "sonnet"]},
            },
            "layer1",
        )
        self.assertEqual(diagnostic["accepted"], 0)
        self.assertEqual(diagnostic["rejected"], 2)
        self.assertEqual(diagnostic["failed_agents"], ["codex", "sonnet"])
        self.assertIn("no agent result records", diagnostic["message"])

    def test_worker_stops_before_paid_review_when_layer1_has_no_successes(self):
        store = self.app.extensions["moa_store"]
        worker = self.app.extensions["moa_worker"]
        session = self.root / ".moa" / "zero-success"
        session.mkdir(parents=True)
        job = store.insert_job(
            {
                "id": "zero-success",
                "title": "Rejected proposals",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Test the autonomous bridge guard.",
                "status": "running",
                "config": {
                    "proposers": ["codex"],
                    "refiners": ["qwen"],
                    "aggregator": "opus",
                    "options": {"aggregate": True},
                },
                "created_at": 1,
            }
        )
        message = (
            "0 of 1 proposer results accepted; 1 rejected because repository "
            "files changed during analysis (codex). Review the rejected lanes, "
            "then use Redispatch failures; review cannot start without at "
            "least one accepted proposal."
        )
        diagnostic = {
            "message": message,
            "phase": "layer1",
            "accepted": 0,
            "rejected": 1,
            "failed_agents": ["codex"],
            "workspace_mutation_agents": ["codex"],
            "transient_agents": [],
        }
        with (
            patch.object(worker, "_run_phase", return_value=0) as run_phase,
            patch.object(worker, "_retry_transient_once", return_value=None),
            patch.object(worker, "_manifest_diagnostic", return_value=diagnostic),
        ):
            worker._run_job(job)
        run_phase.assert_called_once_with(job, "layer1")
        finished = store.get_job("zero-success")
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["exit_code"], 4)
        self.assertEqual(finished["summary"], message)

    def test_job_view_uses_manifest_truth_for_cards_and_recovery(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "manifest-truth"
        session.mkdir(parents=True)
        (session / "layer1-manifest.json").write_text(
            json.dumps(
                {
                    "config": {
                        "proposers": [
                            {
                                "name": "codex",
                                "model": "gpt-5.6-terra",
                                "effort": "high",
                            }
                        ]
                    },
                    "layer1": [
                        {
                            "agent_id": "codex",
                            "layer": 1,
                            "role": "proposer",
                            "success": False,
                            "schema_valid": True,
                            "started_at": 100,
                            "duration_seconds": 12,
                            "error": "workspace immutability violation",
                        }
                    ],
                }
            )
        )
        store.insert_job(
            {
                "id": "manifest-truth",
                "profile_id": "browser_123",
                "title": "Manifest truth",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Test agent cards.",
                "status": "failed",
                "phase": "failed",
                "config": {
                    "proposers": ["codex"],
                    "refiners": ["qwen"],
                    "aggregator": "opus",
                    "options": {
                        "model_overrides": {"qwen": "qwen/test"},
                    },
                },
                "created_at": 1,
            }
        )
        job = self.client.get("/api/jobs/manifest-truth").get_json()
        cards = {card["id"]: card for card in job["agents"]}
        self.assertEqual(cards["codex"]["status"], "failed")
        self.assertEqual(cards["codex"]["finished_at"], "1970-01-01T00:01:52+00:00")
        self.assertIn("immutability", cards["codex"]["summary"])
        self.assertEqual(cards["qwen"]["status"], "blocked")
        self.assertEqual(cards["qwen"]["model"], "qwen/test")
        self.assertEqual(cards["opus"]["status"], "blocked")
        self.assertEqual(
            job["recovery"], {"phase": "layer1", "agents": ["codex"]}
        )

    def test_completed_job_cards_do_not_leave_downstream_lanes_queued(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "completed-cards"
        session.mkdir(parents=True)
        (session / "manifest.json").write_text(
            json.dumps(
                {
                    "layer1": [{"agent_id": "codex", "success": True}],
                    "layer2": [{"agent_id": "qwen", "success": True}],
                    "layer3": [{"agent_id": "opus", "success": True}],
                }
            )
        )
        store.insert_job(
            {
                "id": "completed-cards",
                "profile_id": "browser_123",
                "title": "Completed cards",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Test completed card state.",
                "status": "completed",
                "phase": "complete",
                "config": {
                    "proposers": ["codex"],
                    "refiners": ["qwen"],
                    "aggregator": "opus",
                },
                "created_at": 1,
            }
        )
        job = self.client.get("/api/jobs/completed-cards").get_json()
        self.assertEqual(
            {card["id"]: card["status"] for card in job["agents"]},
            {"codex": "completed", "qwen": "completed", "opus": "completed"},
        )
        self.assertIsNone(job["recovery"])

    def test_sse_worker_error_does_not_use_browser_transport_event_name(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "sse-error"
        session.mkdir(parents=True)
        store.insert_job(
            {
                "id": "sse-error",
                "profile_id": "browser_123",
                "title": "SSE error",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Test SSE event names.",
                "status": "failed",
                "phase": "failed",
                "config": {},
                "created_at": 1,
            }
        )
        store.append_event("sse-error", "error", "A real worker failure")
        response = self.client.get("/api/jobs/sse-error/events", buffered=True)
        payload = response.get_data(as_text=True)
        self.assertIn("event: worker-error", payload)
        self.assertNotIn("\nevent: error\n", payload)
        api_source = (
            Path(__file__).parents[1] / "static" / "js" / "api.js"
        ).read_text()
        self.assertIn('typeof message?.data !== "string"', api_source)

    def test_targeted_redispatch_does_not_show_copied_failure_as_current(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "targeted-retry"
        session.mkdir(parents=True)
        stale = {
            "config": {
                "proposers": [{"name": "codex", "model": "gpt-5.6-terra"}]
            },
            "layer1": [
                {
                    "agent_id": "codex",
                    "success": False,
                    "error": "old failure",
                }
            ],
            "layer2": [],
        }
        (session / "manifest.json").write_text(json.dumps(stale))
        (session / "layer1-manifest.json").write_text(json.dumps(stale))
        store.insert_job(
            {
                "id": "targeted-retry",
                "profile_id": "browser_123",
                "title": "Targeted retry",
                "workspace": str(self.root),
                "session_dir": str(session),
                "goal": "Test copied checkpoint state.",
                "status": "running",
                "phase": "layer1",
                "config": {
                    "proposers": ["codex"],
                    "refiners": [],
                    "aggregator": None,
                    "redispatch": {"phase": "layer1", "agents": ["codex"]},
                },
                "created_at": 1,
            }
        )
        job = self.client.get("/api/jobs/targeted-retry").get_json()
        self.assertEqual(job["agents"][0]["status"], "running")
        self.assertNotIn("summary", job["agents"][0])

    def test_targeted_redispatch_drops_copied_final_artifacts(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "retry-source"
        session.mkdir(parents=True)
        (session / "scout-brief.json").write_text(
            json.dumps({
                "session_id": "retry-source",
                "repo_path": str(self.root),
                "frozen_spec": "Retry one failed lane.",
            }),
            encoding="utf-8",
        )
        checkpoint = {
            "session_id": "retry-source",
            "config": {"proposers": ["codex"]},
            "layer1": [],
            "layer2": [],
            "layer3": [],
            "summary": {},
        }
        for name in ("manifest.json", "layer1-manifest.json"):
            (session / name).write_text(json.dumps(checkpoint), encoding="utf-8")
        stale_names = (
            "decision-map.json",
            "final-plan.json",
            "final-plan.md",
            "report.html",
            "synthesis-input.md",
        )
        for name in stale_names:
            (session / name).write_text("stale source output", encoding="utf-8")
        store.insert_job({
            "id": "retry-source",
            "profile_id": "browser_123",
            "title": "Retry source",
            "workspace": str(self.root),
            "session_dir": str(session),
            "goal": "Retry one failed lane.",
            "status": "failed",
            "phase": "layer1",
            "config": {
                "proposers": ["codex"],
                "refiners": ["qwen"],
                "aggregator": "codex-sol",
            },
            "created_at": 1,
        })

        response = self.client.post(
            "/api/jobs/retry-source/redispatch",
            json={"phase": "layer1", "agents": ["codex"]},
        )
        self.assertEqual(response.status_code, 201)
        target = Path(response.get_json()["session_dir"])
        self.assertTrue((target / "manifest.json").is_file())
        self.assertTrue((target / "layer1-manifest.json").is_file())
        for name in stale_names:
            with self.subTest(name=name):
                self.assertFalse((target / name).exists())

    def test_failed_latest_layer3_hides_and_blocks_stale_final_artifacts(self):
        store = self.app.extensions["moa_store"]
        session = self.root / ".moa" / "failed-layer3-final"
        session.mkdir(parents=True)
        manifest = {
            "session_id": "failed-layer3-final",
            "finished_at": 200.0,
            "config": {},
            "layer1": [],
            "layer2": [],
            "layer3": [
                {"agent_id": "codex-sol", "success": True},
                {"agent_id": "codex-sol", "success": False},
            ],
            "summary": {},
        }
        (session / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (session / "final-plan.md").write_text(
            "# Stale final plan", encoding="utf-8"
        )
        (session / "final-plan.json").write_text(
            json.dumps({"version": 1}), encoding="utf-8"
        )
        store.insert_job({
            "id": "failed-layer3-final",
            "profile_id": "browser_123",
            "title": "Failed synthesis retry",
            "workspace": str(self.root),
            "session_dir": str(session),
            "goal": "Do not expose stale final output.",
            "status": "failed",
            "phase": "layer3",
            "config": {},
            "created_at": 1,
        })

        job = self.client.get("/api/jobs/failed-layer3-final").get_json()
        self.assertNotIn("final_plan", job["artifacts"])
        for filename in ("final-plan.md", "final-plan.json"):
            response = self.client.get(
                f"/api/jobs/failed-layer3-final/artifacts/{filename}"
            )
            self.assertEqual(response.status_code, 404, filename)

    def test_restart_reconciles_an_orphaned_active_job(self):
        store = self.app.extensions["moa_store"]
        store.insert_job(
            {
                "id": "orphaned",
                "title": "Interrupted run",
                "workspace": str(self.root),
                "session_dir": str(self.root / ".moa" / "orphaned"),
                "goal": "Test restart recovery",
                "status": "running",
                "config": {},
                "created_at": 1,
            }
        )
        self.assertEqual(store.reconcile_interrupted_jobs(), ["orphaned"])
        job = store.get_job("orphaned")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["phase"], "interrupted")

    def test_non_worker_app_does_not_reconcile_shared_active_jobs(self):
        store = self.app.extensions["moa_store"]
        store.insert_job(
            {
                "id": "shared-active",
                "title": "Owned by another worker",
                "workspace": str(self.root),
                "session_dir": str(self.root / ".moa" / "shared-active"),
                "goal": "Remain active",
                "status": "running",
                "config": {},
                "created_at": 1,
            }
        )
        create_app(
            {
                "TESTING": True,
                "START_WORKER": False,
                "DATABASE": str(self.root / "webui.sqlite3"),
                "UPLOAD_DIR": str(self.root / "data" / "uploads"),
                "GITHUB_WORKSPACE_DIR": str(
                    self.root / "data" / "workspaces" / "github"
                ),
                "BRIEF_WORKSPACE_DIR": str(
                    self.root / "data" / "workspaces" / "brief"
                ),
                "WORKSPACE_ROOTS": [self.root],
            }
        )
        self.assertEqual(store.get_job("shared-active")["status"], "running")

    def test_history_import(self):
        session = self.root / ".moa" / "historical"
        session.mkdir(parents=True)
        (session / "scout-brief.json").write_text(
            json.dumps(
                {
                    "session_id": "historical",
                    "repo_path": str(self.root),
                    "frozen_spec": "An old run",
                }
            )
        )
        (session / "manifest.json").write_text(
            json.dumps({"layer1": [{"success": True}], "layer2": []})
        )
        response = self.client.post(
            "/api/history/import", json={"workspace": str(self.root)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["imported"], ["historical"])
        job = self.client.get("/api/jobs/historical").get_json()
        self.assertEqual(job["status"], "completed")


class PromptCoachTest(unittest.TestCase):
    @patch("harness.webui.prompt_coach.agy.run")
    @patch("harness.webui.prompt_coach.codex.run")
    def test_gemini_pro_is_used_only_after_luna_fails(
        self, codex_run, agy_run
    ):
        codex_run.return_value = SimpleNamespace(
            success=False, payload=None, error_message="temporary failure"
        )
        agy_run.return_value = SimpleNamespace(
            success=True,
            payload={
                "optimized_prompt": "A stronger brief.",
                "changes": [],
                "assumptions": [],
                "remaining_risks": [],
            },
            error_message=None,
        )
        payload, model = prompt_coach._run(
            "Return the structured result.", prompt_coach.FINALIZE_SCHEMA
        )
        self.assertEqual(payload["optimized_prompt"], "A stronger brief.")
        self.assertEqual(model["model"], "gemini-3.1-pro-high")
        self.assertTrue(model["fallback"])
        self.assertEqual(codex_run.call_args.kwargs["model"], "gpt-5.6-luna")
        agy_run.assert_called_once()

    @patch("harness.webui.prompt_coach.agy.run")
    @patch("harness.webui.prompt_coach.codex.run")
    def test_luna_success_does_not_call_fallback(self, codex_run, agy_run):
        codex_run.return_value = SimpleNamespace(
            success=True,
            payload={
                "optimized_prompt": "A stronger brief.",
                "changes": [],
                "assumptions": [],
                "remaining_risks": [],
            },
            error_message=None,
        )
        _, model = prompt_coach._run(
            "Return the structured result.", prompt_coach.FINALIZE_SCHEMA
        )
        self.assertEqual(model["model"], "gpt-5.6-luna")
        self.assertFalse(model["fallback"])
        agy_run.assert_not_called()

    @patch("harness.webui.prompt_coach.agy.run")
    @patch("harness.webui.prompt_coach.codex.run")
    def test_schema_invalid_luna_response_uses_validated_fallback(
        self, codex_run, agy_run
    ):
        codex_run.return_value = SimpleNamespace(
            success=True,
            payload={"optimized_prompt": "Missing required arrays."},
            error_message=None,
        )
        agy_run.return_value = SimpleNamespace(
            success=True,
            payload={
                "optimized_prompt": "A complete stronger brief.",
                "changes": [],
                "assumptions": [],
                "remaining_risks": [],
            },
            error_message=None,
        )
        payload, model = prompt_coach._run(
            "Return the structured result.", prompt_coach.FINALIZE_SCHEMA
        )
        self.assertEqual(payload["optimized_prompt"], "A complete stronger brief.")
        self.assertTrue(model["fallback"])
        agy_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
