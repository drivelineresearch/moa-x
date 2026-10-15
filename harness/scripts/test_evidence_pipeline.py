"""Offline known-answer and adversarial cases for the v2 evidence pipeline."""

from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import run_moa
import decision_map
import evidence_pipeline as pipeline
import receipts
from test_offline import _make_valid_proposer


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "cache.py").write_text(
            "def expired(now, expires):\n    return now > expires\n"
        )
        (self.repo / ".gitignore").write_text(".moa/\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=self.repo,
            check=True,
        )
        self.session = self.repo / ".moa" / "fixture"
        self.session.mkdir(parents=True)
        self.scout = {
            "session_id": "fixture",
            "pipeline_version": 2,
            "repo_path": str(self.repo),
            "depth": "balanced",
            "frozen_spec": "Fix expiry boundary and preserve zero values.",
        }
        (self.session / "scout-brief.json").write_text(json.dumps(self.scout))
        self.evidence = {
            "type": "code",
            "file": "cache.py",
            "line": 2,
            "url": None,
            "snippet": "return now > expires",
            "claim": "Equality is not expired in the current implementation.",
        }
        payload = _make_valid_proposer("grok")
        payload["plan"][0]["evidence"] = [
            self.evidence,
            {**self.evidence, "claim": "An intentionally incorrect sibling assertion."},
        ]
        self.proposer = run_moa.LayerResult(
            agent_id="grok",
            layer=1,
            role="proposer",
            success=True,
            schema_valid=True,
            payload=payload,
            json_path="layer1/grok-proposer.json",
        )
        self.step = {
            "id": "fix-expiry",
            "title": "Expire at equality",
            "description": "Change the expiry predicate to include equality and test the exact boundary.",
            "files_touched": ["cache.py"],
            "decision": "revised",
            "adjudication": "Follow the primary source and the required contract.",
            "proposer_refs": [
                {
                    "agent_id": "grok",
                    "step_index": 0,
                    "relationship": "adapted",
                    "note": "Keep only the supported premise.",
                    "evidence_indices": [0],
                }
            ],
            "supporting_claims": [
                {"proposer": "grok", "claim_index_path": "plan[0].evidence[0]"}
            ],
            "refiner_refs": [],
            "counterargument": "Preserving legacy equality behavior would violate the requested contract.",
            "constraints": ["Preserve values before expiry."],
            "uncertainties": [],
            "validation": ["Test exact equality and before/after expiry."],
            "evidence": [],
        }
        self.lineage = {
            "version": 2,
            "title": "Correct expiry behavior",
            "summary": "Make the boundary behavior explicit and verified.",
            "outcome": "ready",
            "confidence": {
                "level": "high",
                "rationale": "Two independent reviewers checked the precise primary source.",
            },
            "steps": [self.step],
            "rejected_inputs": [],
            "open_questions": [],
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_map(self, *, external=False):
        manifest = {
            "session_id": "fixture",
            "layer2_mode": "broadcast",
            "config": {
                "repo_path": str(self.repo),
                "proposers": [
                    {
                        "name": "grok",
                        "model": "opencode-go/grok-4.7",
                        "harness": "opencode",
                    }
                ],
                "refiners": [
                    {
                        "name": "deepseek-flash",
                        "model": "opencode-go/deepseek-v4.1-flash",
                        "harness": "opencode",
                    },
                    {"name": "opus", "model": "claude-opus-5-5", "harness": "claude"},
                ],
            },
            "layer1": [],
            "layer2": [],
            "layer3": [],
        }
        for layer, row in [("layer1", self.proposer)]:
            p = self.session / row.json_path
            p.parent.mkdir(exist_ok=True)
            p.write_text(json.dumps(row.payload))
            manifest[layer].append(
                {
                    "agent_id": row.agent_id,
                    "success": True,
                    "schema_valid": True,
                    "json_path": row.json_path,
                    "layer": 1,
                    "role": "proposer",
                }
            )
        for name in ("deepseek-flash", "opus"):
            v = [
                {
                    "proposer": "grok",
                    "claim_index_path": "plan[0].evidence[0]",
                    "status": "verified",
                    "actual_finding": "The source uses strict greater-than, so equality remains unexpired.",
                    "source_url": "https://example.com/unfetched"
                    if external
                    else "cache.py:2",
                },
                {
                    "proposer": "grok",
                    "claim_index_path": "plan[0].evidence[1]",
                    "status": "contradicted",
                    "actual_finding": "The second assertion is contradicted by the source.",
                    "source_url": "cache.py:2",
                },
            ]
            p = self.session / "layer2" / (name + ".json")
            p.parent.mkdir(exist_ok=True)
            p.write_text(json.dumps({"agent_id": name, "verifications": v}))
            manifest["layer2"].append(
                {
                    "agent_id": name,
                    "success": True,
                    "schema_valid": True,
                    "json_path": str(p.relative_to(self.session)),
                    "layer": 2,
                    "role": "refiner-broadcast",
                }
            )
        (self.session / "manifest.json").write_text(json.dumps(manifest))
        (self.session / "final-plan.json").write_text(json.dumps(self.lineage))
        (self.session / "final-audit.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "findings": [{"step_id": "fix-expiry", "status": "supported"}],
                }
            )
        )
        p = decision_map.write_decision_map(
            self.session,
            capture_repository=True,
            validator=lambda d: run_moa._validate_against_schema(
                d, run_moa._load_schema(run_moa.DECISION_MAP_SCHEMA_PATH)
            ),
        )
        return json.loads(p.read_text())

    def test_claim_selection_excludes_contradicted_sibling(self):
        d = self.make_map()
        self.assertEqual(d["quality"]["critical_claims"], 1)
        self.assertEqual(d["quality"]["contradicted_critical_claims"], 0)
        self.assertEqual(d["quality"]["independent_verified_coverage"], 1)

    def test_same_file_is_one_source_even_with_two_reviewer_labs(self):
        d = self.make_map()
        c = next(c for c in d["claims"] if c["critical"])
        self.assertEqual(c["independent_sources"], 1)
        self.assertEqual(len(c["verified_reviewer_labs"]), 2)

    def test_unfetched_url_cannot_verify_a_claim(self):
        d = self.make_map(external=True)
        self.assertEqual(d["quality"]["verified_critical_claims"], 0)

    def test_wrong_code_quote_is_not_a_captured_receipt(self):
        self.proposer.payload["plan"][0]["evidence"][0]["snippet"] = (
            "return now >= expires"
        )
        d = self.make_map()
        self.assertIn("snippet_mismatch", {e["capture_status"] for e in d["evidence"]})

    def test_v2_schema_is_strict_and_v1_remains_supported(self):
        schema = run_moa._aggregation_output_schema(2)
        self.assertEqual(run_moa.lint_schema_openai_strict(schema), [])
        self.assertEqual(
            run_moa._validate_against_schema(
                {"final_plan_markdown": "", "lineage": self.lineage}, schema
            ),
            [],
        )

    def test_no_actionable_unreferenced_decision(self):
        self.step["proposer_refs"] = []
        self.step["supporting_claims"] = []
        self.assertTrue(pipeline.validate_decisions(self.lineage, [self.proposer], []))

    def test_out_of_range_claim_pointer_is_rejected(self):
        self.step["proposer_refs"][0]["evidence_indices"] = [99]
        self.assertTrue(pipeline.validate_decisions(self.lineage, [self.proposer], []))

    def test_ready_cannot_override_unanimous_rejection(self):
        review = SimpleNamespace(
            success=True, payload={"overall_verdict": "reject_all"}
        )
        self.assertTrue(
            pipeline.validate_decisions(self.lineage, [self.proposer], [review])
        )

    def test_rejection_is_a_valid_rendered_outcome(self):
        self.lineage.update(outcome="needs_revision", steps=[])
        self.assertEqual(
            pipeline.validate_decisions(self.lineage, [self.proposer], []), []
        )
        self.assertTrue(
            pipeline.render_plan(self.lineage).startswith("# Needs revision:")
        )

    def test_rendered_plan_uses_exact_decision_description(self):
        self.assertIn(self.step["description"], pipeline.render_plan(self.lineage))

    def test_deferred_unsupported_decision_is_explicit(self):
        self.step.update(decision="deferred", proposer_refs=[], supporting_claims=[])
        self.assertTrue(pipeline.validate_decisions(self.lineage, [self.proposer], []))
        self.lineage["outcome"] = "insufficient_evidence"
        self.assertEqual(
            pipeline.validate_decisions(self.lineage, [self.proposer], []), []
        )

    def test_aliases_restore_all_structured_reviewer_attribution(self):
        _, aliases = pipeline.anonymize([self.proposer], "opus")
        alias = aliases["grok"]
        payload = {
            "reviewing": [alias],
            **{
                f: [{"proposer": alias}]
                for f in [
                    "per_proposer_verdicts",
                    "verifications",
                    "disagreements",
                    "incorrect_steps",
                ]
            },
        }
        restored = pipeline.restore_reviewer_ids(payload, aliases)
        self.assertEqual(restored["reviewing"], ["grok"])
        for f in [
            "per_proposer_verdicts",
            "verifications",
            "disagreements",
            "incorrect_steps",
        ]:
            self.assertEqual(restored[f][0]["proposer"], "grok")

    def test_check_budget_increases_with_depth(self):
        limits = [
            pipeline.policy({"depth": x})["checks"]
            for x in ["quick", "balanced", "thorough"]
        ]
        self.assertEqual(limits, sorted(set(limits)))

    def test_review_queue_overlaps_essential_and_partitions_secondary(self):
        self.proposer.payload["plan"][0]["evidence"] = [
            {**self.evidence, "claim": f"Fact {i}"} for i in range(40)
        ]
        d = pipeline.verification_assignments(
            [self.proposer], ["opus", "deepseek-flash"], self.scout
        )
        a, b = d.values()
        self.assertEqual(a["essential"], b["essential"])
        self.assertFalse(
            {r["claim_index_path"] for r in a["secondary"]}
            & {r["claim_index_path"] for r in b["secondary"]}
        )

    def test_private_ip_is_rejected_without_connecting(self):
        with (
            patch.object(
                socket,
                "getaddrinfo",
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
                ],
            ),
            patch.object(socket, "create_connection") as connect,
        ):
            with self.assertRaises(ValueError):
                receipts.fetch_public("https://example.com/")
            connect.assert_not_called()

    def test_mixed_public_private_dns_is_rejected(self):
        rows = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ["93.184.216.34", "10.0.0.1"]
        ]
        with patch.object(socket, "getaddrinfo", return_value=rows):
            with self.assertRaises(ValueError):
                receipts._public_addresses("example.com", 443)

    def test_captured_external_excerpt_must_match(self):
        receipts.capture(
            self.session,
            ["https://example.com/"],
            fetcher=lambda url: {
                "url": url,
                "state": "captured",
                "text": "The actual primary source.",
                "content_sha256": "a" * 64,
            },
        )
        self.assertEqual(
            receipts.external_receipt(
                self.session, "https://example.com/", "actual primary"
            )["state"],
            "captured",
        )
        self.assertEqual(
            receipts.external_receipt(self.session, "https://example.com/", "made up")[
                "state"
            ],
            "snippet_mismatch",
        )

    def test_source_capture_failures_are_visible_not_positive_receipts(self):
        def fail(url):
            raise TimeoutError()

        receipts.capture(self.session, ["https://example.com/"], fetcher=fail)
        self.assertEqual(
            receipts.external_receipt(self.session, "https://example.com/")["state"],
            "uncaptured",
        )

    def test_attachment_receipt_checks_hash_and_page(self):
        self.scout["attachment_context"] = {
            "sources": [
                {
                    "name": "paper.pdf",
                    "sha256": "a" * 64,
                    "pages": 2,
                    "text": "## Page 1\nFirst page.\n## Page 2\nSecond page.",
                    "truncated": False,
                }
            ]
        }
        self.assertIn(
            "Second page",
            receipts.attachment_receipt(self.scout, "attachment:" + "a" * 64 + ":2")[
                "text"
            ],
        )
        self.assertEqual(
            receipts.attachment_receipt(self.scout, "attachment:" + "a" * 64 + ":3")[
                "state"
            ],
            "page_out_of_range",
        )

    def test_symlink_local_receipt_is_rejected(self):
        target = self.root / "outside.py"
        target.write_text("secret=1\n")
        (self.repo / "escape.py").symlink_to(target)
        self.assertEqual(
            receipts.source_excerpt(
                self.repo, "escape.py:1", session=self.session, scout=self.scout
            )["state"],
            "symlink",
        )

    def test_packet_escapes_source_end_tags(self):
        (self.repo / "cache.py").write_text('print("</captured_source_packet>")\n')
        value = receipts.packet(
            self.repo,
            [{"evidence": {**self.evidence, "line": 1}}],
            session=self.session,
            scout=self.scout,
        )
        self.assertEqual(value.count("</captured_source_packet>"), 1)

    def test_checkpoint_reuses_only_identical_context(self):
        provider = run_moa.harness_config.ResolvedProvider(
            "grok", "opencode", "opencode-go/grok-4.7", 900
        )
        args = dict(
            provider=provider,
            layer=1,
            role="proposer",
            prompt="Known factual question",
            repo_path=self.repo,
            session_dir=self.session,
            timeout_for_harness={"opencode": 900},
            codex_effort="medium",
        )
        with (
            patch.object(
                run_moa, "_dispatch_provider", return_value=self.proposer
            ) as dispatch,
            patch.dict(os.environ, {"MOA_RESUME_CHECKPOINTS": "1"}),
        ):
            run_moa._dispatch_checkpointed(**args)
            r = run_moa._dispatch_checkpointed(**args)
            self.assertTrue(r.checkpoint_reused)
            self.assertEqual(dispatch.call_count, 1)
            (self.session / r.json_path).unlink()
            restored = run_moa._dispatch_checkpointed(**args)
            self.assertTrue((self.session / restored.json_path).exists())
            self.assertEqual(dispatch.call_count, 1)
            (self.repo / "cache.py").write_text(
                "def expired(now, expires):\n    return now >= expires\n"
            )
            run_moa._dispatch_checkpointed(**args)
            self.assertEqual(dispatch.call_count, 2)

    def test_v2_aggregation_renders_and_independently_audits(self):
        from adapters.codex import CodexResult

        output = {"final_plan_markdown": "", "lineage": self.lineage}
        provider = run_moa.harness_config.ResolvedProvider(
            "codex-sol", "codex", "gpt-6.1-sol", 900
        )

        def invoke(harness, **kwargs):
            payload = (
                {
                    "findings": [
                        {
                            "step_id": "fix-expiry",
                            "status": "supported",
                            "finding": "The current strict-greater-than predicate misses exact equality; the proposed inclusive predicate matches the contract.",
                            "source_url": "cache.py:2",
                        }
                    ]
                }
                if "final-audit" in kwargs["schema_path"].name
                else copy.deepcopy(output)
            )
            return CodexResult(True, payload, "{}", "", 0, 0.01)

        (self.session / "synthesis-input.md").write_text(
            "A frozen synthetic source packet."
        )
        with patch.object(run_moa, "_invoke_adapter", side_effect=invoke):
            r = run_moa.run_layer3(
                provider=provider,
                repo_path=self.repo,
                session_dir=self.session,
                timeout=900,
                codex_effort="medium",
                layer1=[self.proposer],
                layer2=[
                    SimpleNamespace(
                        agent_id="deepseek-flash",
                        success=True,
                        payload={"overall_verdict": "accept"},
                    )
                ],
            )
        self.assertTrue(r.success, r.error)
        self.assertIn(
            self.step["description"], (self.session / "final-plan.md").read_text()
        )
        audit = json.loads((self.session / "final-audit.json").read_text())
        self.assertTrue(audit["success"])
        self.assertEqual(audit["lab_id"], "deepseek")
        self.assertEqual(audit["findings"][0]["status"], "supported")
        # A crash after final-file persistence must not purchase aggregation/audit again.
        (self.session / "final-plan.md").unlink()
        (self.session / "final-audit.md").unlink()
        with (
            patch.dict(os.environ, {"MOA_RESUME_CHECKPOINTS": "1"}),
            patch.object(run_moa, "_invoke_adapter") as native,
        ):
            retained = run_moa.run_layer3(
                provider=provider,
                repo_path=self.repo,
                session_dir=self.session,
                timeout=900,
                codex_effort="medium",
                layer1=[self.proposer],
                layer2=[
                    SimpleNamespace(
                        agent_id="deepseek-flash",
                        success=True,
                        payload={"overall_verdict": "accept"},
                    )
                ],
            )
            native.assert_not_called()
        self.assertTrue(retained.checkpoint_reused)
        self.assertTrue((self.session / "final-plan.md").exists())
        self.assertTrue((self.session / "final-audit.md").exists())

    def test_v2_unanimous_rejection_needs_no_fabricated_plan(self):
        from adapters.codex import CodexResult

        self.lineage.update(outcome="needs_revision", steps=[])
        provider = run_moa.harness_config.ResolvedProvider(
            "codex-sol", "codex", "gpt-6.1-sol", 900
        )
        output = {"final_plan_markdown": "", "lineage": self.lineage}
        (self.session / "synthesis-input.md").write_text(
            "All reviewers reject the proposals."
        )
        with patch.object(
            run_moa,
            "_invoke_adapter",
            return_value=CodexResult(True, output, "{}", "", 0, 0.01),
        ):
            r = run_moa.run_layer3(
                provider=provider,
                repo_path=self.repo,
                session_dir=self.session,
                timeout=900,
                codex_effort="medium",
                layer1=[self.proposer],
                layer2=[],
            )
        self.assertTrue(r.success, r.error)
        self.assertTrue(
            (self.session / "final-plan.md").read_text().startswith("# Needs revision:")
        )

    def test_worker_recovers_from_transient_storage_error(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness.webui.worker import JobWorker

        class Storage:
            calls = 0

            def claim_next_job(inner):
                inner.calls += 1
                if inner.calls == 1:
                    raise OSError("synthetic transient failure")
                worker._stop.set()
                return None

        store = Storage()
        worker = JobWorker(store, Path("/not-executed"), poll_seconds=0.001)
        with patch("harness.webui.worker.capture_operational_error"):
            worker._loop()
        self.assertEqual(store.calls, 2)
        self.assertIsNone(worker.status()["last_error"])

    def test_interrupted_v2_job_resumes_with_bounded_checkpoint_restarts(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness.webui.store import Store

        store = Store(self.root / "jobs.sqlite3")
        profile = store.upsert_profile("fixture-profile", "Fixture")
        job = {
            "id": "fixture-job",
            "profile_id": profile["id"],
            "title": "Fixture",
            "workspace": str(self.repo),
            "session_dir": str(self.session),
            "goal": "Known answer",
            "status": "running",
            "phase": "layer1",
            "progress": 0.1,
            "config": {},
            "created_at": 1,
        }
        store.insert_job(job)
        # insert_job always starts queued; emulate an interrupted owned run.
        store.update_job("fixture-job", status="running")
        store.reconcile_interrupted_jobs()
        row = store.get_job("fixture-job")
        self.assertEqual(row["status"], "queued")
        self.assertTrue(row["config"]["options"]["resume_checkpoints"])
        store.update_job("fixture-job", status="running")
        store.reconcile_interrupted_jobs()
        store.update_job("fixture-job", status="running")
        store.reconcile_interrupted_jobs()
        self.assertEqual(store.get_job("fixture-job")["phase"], "interrupted")

    def test_known_answer_fixture_has_exactly_two_seeded_defects_and_small_fix(self):
        import shutil, sys

        fixture = (
            Path(__file__).resolve().parents[1] / "evals/fixtures/cache-boundaries"
        )
        target = self.root / "known-answer"
        shutil.copytree(fixture, target)
        before = subprocess.run(
            [sys.executable, "-m", "unittest", "contract_examples"],
            cwd=target,
            capture_output=True,
            text=True,
        )
        self.assertEqual(before.returncode, 1)
        self.assertIn("failures=2", before.stderr)
        code = (
            (target / "cache.py")
            .read_text()
            .replace("if now > expires:", "if now >= expires:")
            .replace("        if not value:\n            return None\n", "")
        )
        (target / "cache.py").write_text(code)
        after = subprocess.run(
            [sys.executable, "-m", "unittest", "contract_examples"],
            cwd=target,
            capture_output=True,
            text=True,
        )
        self.assertEqual(after.returncode, 0, after.stderr)

    def test_usage_is_native_not_invented_when_absent(self):
        import usage

        self.assertIsNone(usage.extract('{"answer":"no usage receipt"}'))
        row = usage.extract(
            '{"type":"turn.completed","usage":{"input_tokens":42,"output_tokens":7,"cached_input_tokens":8}}'
        )
        self.assertEqual(row["input_tokens"], 42)
        self.assertEqual(row["output_tokens"], 7)
        self.assertIsNone(usage.extract('{"usage":{"input_tokens":999},"plan":[]}'))

    def test_shared_readiness_labels_and_stale_outcomes(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness.webui.run_status import (
            outcome_label,
            evidence_status,
            evidence_text,
        )

        self.make_map()
        self.assertEqual(outcome_label("completed", "ready"), "Ready for review")
        self.assertEqual(outcome_label("completed", "needs_revision"), "Needs revision")
        self.assertEqual(outcome_label("completed", None), "Readiness not recorded")
        self.assertIsNone(
            evidence_status(self.session, stale=True, status="completed")["outcome"]
        )
        self.assertIsNone(evidence_status(self.session, status="running")["outcome"])
        self.lineage["outcome"] = "needs_revision"
        self.make_map()
        (self.session / "final-audit.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "findings": [
                        {
                            "step_id": "fix-expiry",
                            "status": "contradicted",
                            "finding": "Incorrect final decision.",
                        }
                    ],
                }
            )
        )
        self.assertIn(
            "1 contradicted",
            evidence_text(evidence_status(self.session, status="completed")),
        )

    def test_bad_refiner_pointer_cannot_import_an_unadopted_claim(self):
        self.step["refiner_refs"] = [
            {
                "agent_id": "opus",
                "kind": "verification",
                "index": 1,
                "note": "This is contrary evidence that was discarded.",
            }
        ]
        d = self.make_map()
        self.assertEqual(d["quality"]["critical_claims"], 1)
        self.assertEqual(d["quality"]["contradicted_critical_claims"], 0)

    def test_clean_committed_tree_change_is_a_workspace_mutation(self):
        before = run_moa._workspace_snapshot(self.repo, self.session)
        (self.repo / "cache.py").write_text(
            "def expired(now, expires):\n    return now >= expires\n"
        )
        subprocess.run(["git", "add", "cache.py"], cwd=self.repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "Different source tree",
            ],
            cwd=self.repo,
            check=True,
        )
        after = run_moa._workspace_snapshot(self.repo, self.session)
        self.assertNotEqual(before.digest, after.digest)
        result = run_moa.LayerResult(
            agent_id="fixture", layer=1, role="proposer", success=True
        )
        checked = run_moa._apply_workspace_guard(result, before, after)
        self.assertFalse(checked.success)

    def test_multiline_code_quote_matches_bytes_without_display_line_numbers(self):
        text = "1: def expired(now, expires):\n2:     return now > expires"
        self.assertTrue(
            receipts.code_snippet_matches(
                "def expired(now, expires):\n    return now > expires", text
            )
        )
        self.assertFalse(receipts.code_snippet_matches("return now >= expires", text))

    def test_valid_independent_receipt_supports_claim_with_bad_proposer_quote(self):
        self.proposer.payload["plan"][0]["evidence"][0]["snippet"] = (
            "a nonexistent quotation"
        )
        d = self.make_map()
        self.assertEqual(d["quality"]["supported_critical_claims"], 1)
        self.assertIn("snippet_mismatch", {e["capture_status"] for e in d["evidence"]})

    def test_final_evidence_uses_same_quote_and_safety_rules(self):
        self.step["evidence"] = [
            {**self.evidence, "snippet": "invented source quote"},
            {**self.evidence, "file": ".env", "snippet": "secret"},
        ]
        d = self.make_map()
        bad = [e for e in d["evidence"] if e["capture_status"] != "captured"]
        self.assertTrue(any(e["capture_status"] == "snippet_mismatch" for e in bad))
        self.assertTrue(all(e["snippet"] is None for e in bad))

    def test_final_reusing_proposal_evidence_preserves_provenance(self):
        self.step["evidence"] = [copy.deepcopy(self.evidence)]
        d = self.make_map()
        self.assertEqual(len(d["evidence"]), 1)
        self.assertEqual(d["evidence"][0]["proposer_ids"], ["grok"])
        self.assertEqual(
            next(c for c in d["claims"] if c["critical"])["independent_sources"], 1
        )

    def test_final_external_receipt_is_same_source_as_proposal(self):
        item = {
            **self.evidence,
            "type": "external",
            "file": None,
            "line": None,
            "url": "https://example.com/source",
            "snippet": "precise text",
        }
        self.proposer.payload["plan"][0]["evidence"] = [item]
        self.step["evidence"] = [{**item, "snippet": "text"}]
        receipts.capture(
            self.session,
            [item["url"]],
            fetcher=lambda url: {
                "state": "captured",
                "url": url,
                "text": "precise text",
            },
        )
        d = self.make_map(external=True)
        self.assertEqual(len({e["source_key"] for e in d["evidence"]}), 1)

    def test_capture_preserves_priority_and_final_stage_allowance(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return {"state": "captured", "url": url, "text": "source"}

        receipts.capture(
            self.session,
            ["https://z.example/a", "https://a.example/b"],
            limit=1,
            fetcher=fetch,
        )
        self.assertEqual(seen, ["https://z.example/a"])
        receipts.capture(
            self.session, ["https://final.example/c"], limit=1, fetcher=fetch
        )
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(receipts.load(self.session)), 2)

    def test_packet_keeps_multiple_normalized_quotes_from_one_url(self):
        url = "https://example.com/paper"
        text = "first\nquote " + ("x " * 3000) + "second\nquote"
        receipts.capture(
            self.session,
            [url],
            fetcher=lambda url: {"state": "captured", "url": url, "text": text},
        )
        value = receipts.packet(
            self.repo,
            [
                {"evidence": {"type": "external", "url": url, "snippet": quote}}
                for quote in ("first quote", "second quote")
            ],
            session=self.session,
            scout=self.scout,
        )
        records = json.loads(value.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["snippet_matched"] for r in records))
        self.assertIn("second quote", records[1]["text"])

    def test_attachment_is_not_a_repository_path_and_quotes_are_checked(self):
        self.test_attachment_receipt_checks_hash_and_page()
        source = "attachment:" + "a" * 64 + ":2"
        self.assertIsNone(decision_map._local_verification_source_parts(source))
        self.assertEqual(
            receipts.attachment_receipt(self.scout, source, "First page")["state"],
            "snippet_mismatch",
        )
        from test_offline import _make_valid_broadcast_refiner

        payload = _make_valid_broadcast_refiner("opus")
        payload["verifications"][0]["source_url"] = source
        payload["verifications"][0]["status"] = "verified"
        result, warnings = run_moa._downgrade_unsupported_verifications(
            payload, self.repo, scout=self.scout
        )
        self.assertEqual(result["verifications"][0]["status"], "verified")
        self.assertFalse(warnings)

    def test_workspace_mutation_checkpoint_is_terminal_after_source_changes(self):
        provider = run_moa.harness_config.ResolvedProvider(
            "grok", "opencode", "opencode-go/grok-4.7", 900
        )
        args = dict(
            provider=provider,
            layer=1,
            role="proposer",
            prompt="Read only",
            repo_path=self.repo,
            session_dir=self.session,
            timeout_for_harness={"opencode": 900},
            codex_effort="medium",
        )
        result = copy.deepcopy(self.proposer)
        result.success = False
        result.workspace_mutations = ["cache.py"]
        with (
            patch.object(run_moa, "_dispatch_provider", return_value=result) as native,
            patch.dict(os.environ, {"MOA_RESUME_CHECKPOINTS": "1"}),
        ):
            run_moa._dispatch_checkpointed(**args)
            (self.repo / "cache.py").write_text("changed by failed lane\n")
            restored = run_moa._dispatch_checkpointed(**args)
        self.assertEqual(native.call_count, 1)
        self.assertFalse(restored.success)
        self.assertTrue(restored.checkpoint_reused)

    def test_auditor_falls_back_to_healthy_independent_lab(self):
        catalog = run_moa.harness_config.load_provider_catalog()
        aggregator = catalog["fable"]
        reviews = [
            SimpleNamespace(agent_id="deepseek-flash", success=False, payload=None),
            SimpleNamespace(
                agent_id="codex-sol",
                success=True,
                payload={"overall_verdict": "accept"},
            ),
            SimpleNamespace(
                agent_id="opus", success=True, payload={"overall_verdict": "accept"}
            ),
        ]
        self.assertEqual(
            run_moa._select_auditor(aggregator, reviews, catalog).name, "codex-sol"
        )
        self.assertIsNone(run_moa._select_auditor(aggregator, reviews[2:], catalog))

    def test_worker_storage_error_after_spawn_reaps_process(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness.webui.worker import JobWorker

        store = SimpleNamespace(
            update_job=lambda *a, **kw: (
                (_ for _ in ()).throw(OSError("database unavailable"))
                if "pid" in kw
                else None
            ),
            append_event=lambda *a, **kw: None,
        )
        worker = JobWorker(store, Path("fixture.py"))
        job = {
            "id": "fixture",
            "session_dir": str(self.session),
            "workspace": str(self.repo),
            "config": {},
        }
        processes = []
        popen = subprocess.Popen

        def spawn(*args, **kwargs):
            proc = popen(*args, **kwargs)
            processes.append(proc)
            return proc

        with (
            patch.object(
                worker,
                "_command",
                return_value=[sys.executable, "-c", "import time;time.sleep(60)"],
            ),
            patch.object(worker, "_environment", return_value=dict(os.environ)),
            patch("harness.webui.worker.subprocess.Popen", side_effect=spawn),
        ):
            with self.assertRaises(OSError):
                worker._run_phase(job, "layer1")
        self.assertIsNotNone(processes[0].poll())
        self.assertEqual(worker._processes, {})

    def test_worker_retries_failure_record_after_database_recovers(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness.webui.worker import JobWorker

        class Storage:
            claims = 0

            def claim_next_job(inner):
                inner.claims += 1
                if inner.claims == 1:
                    return {"id": "claimed-job"}
                worker._stop.set()
                return None

            def update_job(inner, job_id, **fields):
                self.assertEqual(job_id, "claimed-job")
                self.assertEqual(fields["status"], "failed")
                inner.repaired = True

        store = Storage()
        worker = JobWorker(store, Path("unused"), poll_seconds=0.001)
        with (
            patch.object(worker, "_run_job", side_effect=OSError("storage offline")),
            patch("harness.webui.worker.capture_operational_error"),
        ):
            worker._loop()
        self.assertTrue(store.repaired)


if __name__ == "__main__":
    unittest.main()
