"""Offline regressions for provider recovery, budgets and attempt provenance."""
from __future__ import annotations
import contextlib
import io
import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_moa
from adapters import agy, remaining_timeout, drain_after_timeout
from adapters.failures import classify_failure
from test_offline import _make_valid_proposer, _make_valid_broadcast_refiner


def output(payload=None, *, error=None, stderr="", incomplete=False):
    return SimpleNamespace(success=error is None, payload=payload,
                           error_message=error, raw_stderr=stderr, raw_stdout="",
                           duration_seconds=.1, transient_empty=incomplete)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / ".moa" / "fixture"
        (self.session / "layer1").mkdir(parents=True)
        self.valid = _make_valid_proposer("fixture")

    def run_lane(self, **extra):
        args = dict(harness="codex", layer=1, role="proposer", prompt="fixture",
                    schema_path=run_moa.PROPOSER_SCHEMA_PATH, repo_path=self.root,
                    session_dir=self.session, timeout=900, model="gpt-6.1-sol",
                    reasoning_effort="xhigh", agent_id="fixture")
        args.update(extra)
        return run_moa._run_with_recovery(**args)

    def test_capacity_recovers_same_model_and_keeps_both_logs(self):
        first = output(error="codex exited with code 1", stderr="ERROR: 429 Too Many Requests\nERROR: Selected model is at capacity.")
        with patch.object(run_moa.codex_adapter, "run", side_effect=[first, output(self.valid)]) as call, patch.object(run_moa, "retry_sleep") as sleep:
            result = self.run_lane()
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.attempts[0]["failure_kind"], "capacity")
        self.assertEqual(result.attempts[0]["log_path"], "layer1/fixture-proposer.log")
        self.assertEqual(result.log_path, "layer1/fixture-proposer.retry-2.log")
        self.assertTrue(all(c.kwargs["model"] == "gpt-6.1-sol" for c in call.call_args_list))
        self.assertGreaterEqual(sleep.call_args.args[0], 5)

    def test_short_deadline_still_invokes_the_initial_provider(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(self.valid)) as call:
            result = self.run_lane(timeout=5)
        self.assertTrue(result.success)
        self.assertEqual(call.call_count, 1)
        self.assertGreater(call.call_args.kwargs["timeout_seconds"], 0)
        self.assertLessEqual(call.call_args.kwargs["timeout_seconds"], 5)

    def test_short_custom_budget_keeps_most_time_for_research(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(self.valid)) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 1]):
            self.assertTrue(self.run_lane(timeout=300).success)
        self.assertEqual(call.call_args.kwargs["timeout_seconds"], 240)

    def test_interrupted_http_200_stream_retries_with_remaining_budget(self):
        message = "opencode provider error: provider.invalid-output (HTTP 200): OpenAI Chat stream ended without finish_reason"
        with patch.object(run_moa.opencode_adapter, "run", side_effect=[output(error=message), output(self.valid)]) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 1096, 1101, 1150]), patch.object(run_moa, "retry_sleep"):
            result = self.run_lane(harness="opencode", model="opencode-go/glm-5.3", timeout=2700)
        self.assertTrue(result.success)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result.attempts[0]["failure_kind"], "incomplete")
        self.assertEqual(call.call_args_list[0].kwargs["timeout_seconds"], 2460)
        self.assertEqual(call.call_args_list[1].kwargs["timeout_seconds"], 1359)
        self.assertEqual(result.duration_seconds, 1150)
        for message in ("provider.invalid-output (HTTP 200): unexpected shape", "HTTP 403 stream ended without finish_reason", "insufficient_quota stream ended without finish_reason"):
            self.assertFalse(classify_failure(message).retryable)

    def test_long_research_reserves_the_full_repair_window(self):
        invalid = json.loads(json.dumps(self.valid))
        invalid["plan"][0].pop("risks")
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(invalid), output(self.valid)]) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 2460, 2500]):
            result = self.run_lane(timeout=2700)
        self.assertTrue(result.success)
        self.assertEqual([c.kwargs["timeout_seconds"] for c in call.call_args_list], [2460, 240])
        self.assertEqual([a["timeout_seconds"] for a in result.attempts], [2460, 240])

    def test_research_timeout_does_not_spend_the_repair_reserve_on_research(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="timeout after 2460s")) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 2460, 2465]), patch.object(run_moa, "retry_sleep") as sleep:
            result = self.run_lane(timeout=2700)
        self.assertFalse(result.success)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result.stop_reason, "deadline")
        sleep.assert_not_called()

    def test_unsupported_verification_keeps_other_checks_and_original_audit(self):
        (self.root / "short.py").write_text("first\nsecond\n")
        for status in ("verified", "contradicted"):
            for source, reason in ((None, "missing_source"), ("short.py:1-3", "line_out_of_range"), ("missing.py:1", "unreadable"), ("short.py:1,2", "invalid_line_range"), ("short.py:2-1", "invalid_line_range")):
                with self.subTest(status=status, source=source):
                    payload = _make_valid_broadcast_refiner("fixture")
                    payload["verifications"][0]["source_url"] = "https://example.org/checked"
                    original = {**payload["verifications"][0], "status": status, "source_url": source}
                    payload["verifications"].append(dict(original))
                    with patch.object(run_moa.opencode_adapter, "run", return_value=output(payload)) as call:
                        result = self.run_lane(harness="opencode", layer=2, role="refiner-broadcast", schema_path=run_moa.REFINER_SCHEMA_PATH)
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(call.call_count, 1)
                    self.assertEqual(result.payload["verifications"][0]["status"], "verified")
                    self.assertEqual(result.payload["verifications"][2]["status"], "unverified")
                    self.assertEqual(result.verification_warnings, [{"verification_index": 2, "reason": reason, "original_verification": original}])
                    self.assertEqual(result.attempts[0]["verification_warnings"], result.verification_warnings)
                    self.assertEqual(json.loads((self.session / result.json_path).read_text()), result.payload)
                    self.assertEqual(payload["verifications"][2], original)
                    if source == "short.py:1-3":
                        self.assertEqual(result.payload["verifications"][2]["source_url"], source)
                    manifest_path = run_moa.write_manifest(
                        session_dir=self.session, scout_brief={"session_id": "fixture"},
                        layer1=[], layer2=[result], started_at=0, finished_at=1,
                    )
                    restored = run_moa.load_layer_results_from_manifest(manifest_path, "layer2", self.session)
                    self.assertEqual(restored[0].verification_warnings, result.verification_warnings)
                    decision_map = run_moa.decision_map_module.build_decision_map(self.session)
                    checks = [v for v in decision_map["verifications"] if v["index"] == 2]
                    self.assertEqual(checks[0]["declared_status"], status)
                    self.assertEqual(checks[0]["status"], "unverified")
                    self.assertTrue(any(reason in w for w in decision_map["warnings"]))
                    self.assertEqual(run_moa._validate_against_schema(decision_map, run_moa._load_schema(run_moa.DECISION_MAP_SCHEMA_PATH)), [])

    def test_receipts_without_a_repository_are_not_mislabeled_or_safety_checked(self):
        payload = _make_valid_broadcast_refiner("fixture")
        payload["verifications"][0]["source_url"] = "safe.py:1-3"
        candidate, warnings = run_moa._downgrade_unsupported_verifications(payload, None)
        self.assertEqual(candidate, payload)
        self.assertEqual(warnings, [])
        payload["verifications"][0]["source_url"] = "safe.py:1,3"
        candidate, warnings = run_moa._downgrade_unsupported_verifications(payload, None)
        self.assertEqual(candidate, payload)
        self.assertEqual(warnings, [])
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(candidate))

    def test_downgrade_must_still_meet_the_full_output_schema(self):
        payload = _make_valid_broadcast_refiner("fixture")
        payload["verifications"][0]["source_url"] = None
        schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
        schema["properties"]["verifications"]["items"]["properties"]["actual_finding"]["maxLength"] = 80
        self.assertEqual(run_moa._validate_against_schema(payload, schema), [])
        candidate, warnings = run_moa._downgrade_unsupported_verifications(payload, self.root, schema)
        self.assertEqual(candidate, payload)
        self.assertEqual(warnings, [])

    def test_stale_audits_cannot_attach_to_a_replacement_review(self):
        payload = _make_valid_broadcast_refiner("fixture")
        payload["verifications"][0]["source_url"] = "https://example.org/checked"
        path = self.session / "layer2/review.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(payload))
        audit = {"verification_index": 0, "reason": "missing_source", "original_verification": {"status": "contradicted"}}
        old = {"agent_id": "fixture", "success": True, "json_path": "layer2/review.json", "verification_warnings": [audit]}
        new = {**old, "verification_warnings": None}
        failed = {**old, "success": False}
        manifest_path = self.session / "manifest.json"
        manifest_path.write_text(json.dumps({"layer2": [old, new]}))
        decision_map = run_moa.decision_map_module.build_decision_map(self.session)
        check = next(v for v in decision_map["verifications"] if v["index"] == 0)
        self.assertEqual(check["declared_status"], "verified")
        self.assertFalse(any("declared contradicted" in w for w in decision_map["warnings"]))
        manifest_path.write_text(json.dumps({"layer2": [old, new, failed]}))
        decision_map = run_moa.decision_map_module.build_decision_map(self.session)
        self.assertEqual(decision_map["verifications"], [])

    def test_downgrade_is_transactional_and_does_not_accept_unsafe_receipts(self):
        (self.root / "safe.py").write_text("receipt\n")
        (self.root / "binary.py").write_bytes(b"binary\0")
        if sys.platform != "win32":
            (self.root / "linked.py").symlink_to(self.root / "safe.py")
        for source in ("../outside.py:1", ".env:1", "safe.py:not-a-line", "javascript:alert(1)", "binary.py:1,2", "linked.py:1,2"):
            if source.startswith("linked") and sys.platform == "win32":
                continue
            with self.subTest(source=source):
                payload = _make_valid_broadcast_refiner("fixture")
                payload["verifications"][0]["source_url"] = None
                payload["verifications"].append({**payload["verifications"][0], "source_url": source})
                candidate, warnings = run_moa._downgrade_unsupported_verifications(payload, self.root)
                self.assertEqual(candidate, payload)
                self.assertEqual(warnings, [])
                self.assertTrue(run_moa._validate_refiner_verification_cross_fields(candidate, self.root))

    def test_absolute_receipt_normalization_preserves_symlink_rejection(self):
        if sys.platform == "win32":
            self.skipTest("POSIX symlink test")
        (self.root / "safe.py").write_text("receipt\n")
        (self.root / "linked.py").symlink_to(self.root / "safe.py")
        payload = _make_valid_broadcast_refiner("fixture")
        payload["verifications"][0]["source_url"] = str(self.root / "linked.py") + ":1"
        self.assertEqual(run_moa._normalize_refiner_verification_sources(payload, self.root), 1)
        self.assertEqual(payload["verifications"][0]["source_url"], "linked.py:1")
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, self.root))
        candidate, warnings = run_moa._downgrade_unsupported_verifications(payload, self.root)
        self.assertEqual(candidate, payload)
        self.assertEqual(warnings, [])

    def test_checkout_root_alias_does_not_hide_internal_symlinks(self):
        if sys.platform == "win32":
            self.skipTest("POSIX symlink test")
        actual = self.root / "actual"
        actual.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        (actual / "safe.py").write_text("receipt\n")
        (actual / "linked.py").symlink_to(actual / "safe.py")
        payload = _make_valid_broadcast_refiner("fixture")
        record = payload["verifications"][0]
        for prefix in (actual, alias):
            record["source_url"] = str(prefix / "safe.py") + ":1"
            run_moa._normalize_refiner_verification_sources(payload, alias)
            self.assertEqual(record["source_url"], "safe.py:1")
            self.assertEqual(run_moa._validate_refiner_verification_cross_fields(payload, alias), [])
        record["source_url"] = str(alias / "linked.py") + ":1"
        run_moa._normalize_refiner_verification_sources(payload, alias)
        self.assertEqual(record["source_url"], "linked.py:1")
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, alias))

    def test_real_timeout_cleanup_is_bounded_and_keeps_partial_stdout(self):
        if sys.platform == "win32":self.skipTest("POSIX process-group test")
        started = time.monotonic()
        proc = subprocess.Popen([sys.executable, "-u", "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('captured receipt',flush=True);time.sleep(30)"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                proc.communicate(timeout=remaining_timeout(started, 1, reserve_cleanup=True))
            stdout, _ = drain_after_timeout(proc, raised.exception, started, 1)
            self.assertLess(time.monotonic() - started, 1.75)
            self.assertIn("captured receipt", stdout)
        finally:
            proc.kill();proc.communicate(timeout=2)

    def test_git_isolation_probe_is_clipped_to_the_invocation_deadline(self):
        probe = SimpleNamespace(returncode=1, stdout="", stderr="")
        with patch.object(agy.time, "monotonic", return_value=15), patch.object(agy.subprocess, "run", return_value=probe) as call:
            with agy._isolated_git_workspace(self.root, self.session, deadline=20) as workspace:
                self.assertEqual(workspace, self.root)
        self.assertEqual(call.call_args.kwargs["timeout"], 5)

    def test_independent_checkout_never_registers_a_worktree_in_source(self):
        source = self.root / "source";source.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        (source / "tracked.txt").write_text("original")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "fixture"], cwd=source, check=True)
        (source / "tracked.txt").write_text("dirty source")
        (source / "untracked.txt").write_text("untracked source")
        before = subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=source)
        with patch.dict(run_moa.os.environ, {"GIT_DIR": str(source / ".git"), "GIT_WORK_TREE": str(source), "GIT_INDEX_FILE": str(source / ".git/index")}), agy._isolated_git_workspace(source, self.session, deadline=time.monotonic()+10) as snapshot:
            self.assertTrue((snapshot / ".git").is_dir())
            self.assertEqual((snapshot / "tracked.txt").read_text(), "dirty source")
            self.assertEqual((snapshot / "untracked.txt").read_text(), "untracked source")
            self.assertEqual(subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=source), before)
            (snapshot / "tracked.txt").write_text("simulated model mutation")
        self.assertEqual((source / "tracked.txt").read_text(), "dirty source")
        self.assertEqual(subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=source), before)

    def test_adapter_setup_exception_is_logged_and_never_retried(self):
        for failure in (OSError("cannot write prompt file"), subprocess.TimeoutExpired("git rev-parse", 15)):
            with self.subTest(failure=type(failure).__name__), patch.object(run_moa, "_invoke_adapter", side_effect=failure) as call:
                result = self.run_lane()
                self.assertFalse(result.success)
                self.assertEqual(result.failure_kind, "configuration")
                self.assertEqual(result.stop_reason, "permanent_failure")
                self.assertEqual(call.call_count, 1)
                self.assertEqual(len(result.attempts), 1)
                self.assertTrue((self.session / result.log_path).is_file())

    def test_snapshot_preserves_clean_repository_filtered_bytes(self):
        import shlex
        source = self.root / "filtered";source.mkdir()
        def git(*args):return subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        git("init", "-q")
        (source / ".gitattributes").write_text("tracked.txt filter=fixture\n")
        command = shlex.quote(sys.executable) + " -c " + shlex.quote('import sys;sys.stdout.write(sys.stdin.read().replace("visible", "stored"))')
        git("config", "filter.fixture.clean", command)
        (source / "tracked.txt").write_text("visible bytes\r\n")
        git("add", ".");git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "filtered")
        self.assertEqual(git("diff", "HEAD").stdout, b"")
        with agy._isolated_git_workspace(source, self.session, deadline=time.monotonic()+10) as snapshot:
            self.assertEqual((snapshot / "tracked.txt").read_bytes(), (source / "tracked.txt").read_bytes())

    def test_capacity_exhaustion_is_three_calls_total(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="HTTP 429 model at capacity")) as call, patch.object(run_moa, "retry_sleep"):
            result = self.run_lane()
        self.assertFalse(result.success)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(result.attempt, 3)
        self.assertFalse(result.transient_empty)

    def test_adapter_exception_preserves_a_receipt_already_written(self):
        def failed_adapter(harness, **kwargs):
            kwargs["log_file"].write_text("Original provider stdout/stderr receipt\n")
            raise ValueError("unexpected output wrapper")
        with patch.object(run_moa, "_invoke_adapter", side_effect=failed_adapter) as call:
            result = self.run_lane()
        self.assertFalse(result.success)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result.failure_kind, "configuration")
        log = (self.session / result.log_path).read_text()
        self.assertTrue(log.startswith("Original provider stdout/stderr receipt\n"))
        self.assertIn("ADAPTER EXCEPTION", log)
        self.assertIn("unexpected output wrapper", log)
        self.assertIn("adapter failure", result.error)

    def test_permanent_failures_never_retry_even_if_incomplete(self):
        for error in ("403 Model access is disabled", "401 invalid token", "429 insufficient_quota", "429 monthly spending limit; quota exhausted", "unknown model", "binary not found", "OSError launching codex"):
            with self.subTest(error=error), patch.object(run_moa.codex_adapter, "run", return_value=output(error=error, incomplete=True)) as call, patch.object(run_moa, "retry_sleep") as sleep:
                result = self.run_lane()
                self.assertFalse(result.success)
                self.assertEqual(call.call_count, 1)
                sleep.assert_not_called()

    def test_per_minute_quota_is_capacity_and_provider_retry_delays_parse(self):
        self.assertTrue(classify_failure("HTTP 429 Quota exceeded for quota metric per minute").retryable)
        self.assertTrue(classify_failure("HTTP 429 quota exceeded").retryable)
        self.assertFalse(classify_failure("quota exhausted").retryable)
        for error in ('HTTP 429 Please retry in 37.2s', 'RESOURCE_EXHAUSTED "retryDelay": "37s"'):
            self.assertGreaterEqual(classify_failure(error).retry_after, 37)
        self.assertFalse(classify_failure('HTTP 429 insufficient_quota').retryable)
        self.assertEqual(classify_failure('HTTP 429 retry in 2 minutes').retry_after, 120)
        self.assertEqual(classify_failure('HTTP 429 Retry-After: 500ms').retry_after, .5)
        self.assertEqual(classify_failure('HTTP 429 try again in 2m').retry_after, 120)
        with patch('adapters.failures.time.time', return_value=1792000000):
            from email.utils import formatdate
            hint=formatdate(1792000030, usegmt=True)
            self.assertEqual(classify_failure('HTTP 429 Retry-After: ' + hint).retry_after, 30)
        self.assertTrue(classify_failure('HTTP 429 at capacity', 'read: permission denied at line 403').retryable)
        self.assertFalse(classify_failure('HTTP 403 forbidden').retryable)
        self.assertEqual(classify_failure('codex exited with code 1', 'echoed prompt: invalid API key, insufficient_quota\nERROR: Selected model is at capacity.').kind, 'capacity')
        self.assertTrue(classify_failure('could not extract JSON', 'WebFetch HTTP 403 forbidden', incomplete=True).retryable)

    def test_repair_does_not_recombine_known_citations_or_reauthor_summary(self):
        repaired = json.loads(json.dumps(self.valid))
        repaired["plan"][0]["evidence"][0]["claim"] = repaired["plan"][0]["evidence"][1]["claim"]
        self.assertTrue(run_moa._repair_evidence_errors(self.valid, repaired))
        repaired = json.loads(json.dumps(self.valid));repaired["summary"]='A new claim absent from the original research.'
        self.assertTrue(run_moa._repair_evidence_errors(self.valid, repaired))

    def test_agy_error_envelope_preserves_auth_cause_without_retry(self):
        envelope = json.dumps({"is_error": True, "result": "HTTP 403 Model access is disabled"})
        process=Mock(returncode=0);process.communicate.return_value=(envelope, "")
        mirror=self.root/'mirror-error';mirror.mkdir()
        with patch.object(agy, "_isolated_git_workspace", return_value=contextlib.nullcontext(mirror)), patch.object(agy.subprocess, "Popen", return_value=process):
            result=agy.run(prompt="fixture",repo_path=self.root,model="gemini-4-argon-high",log_file=self.session/'layer1/fixture.log')
        self.assertFalse(result.success)
        self.assertEqual(classify_failure(result.error_message).kind, "auth")

    def test_only_diagnostics_drive_retry_not_prompt_or_model_output(self):
        value = output(error="codex exited with code 1")
        value.raw_stdout = 'A repository document says "HTTP 429 model at capacity".'
        with patch.object(run_moa.codex_adapter, "run", return_value=value) as call:
            self.assertFalse(self.run_lane().success)
        self.assertEqual(call.call_count, 1)

    def test_native_argon_shape_failure_gets_one_tool_free_repair(self):
        invalid = json.loads(json.dumps(self.valid))
        item = invalid["plan"][0]
        item["title"] = item.pop("why")
        item["details"] = "Required plan structure omitted."
        item.pop("files_touched"); item.pop("risks")
        with patch.object(run_moa.agy_adapter, "run", return_value=output(invalid)) as first, patch.object(run_moa.claude_adapter, "run", return_value=output(self.valid)) as repair:
            result = self.run_lane(harness="agy", model="gemini-4-argon-high")
        self.assertTrue(result.success)
        self.assertEqual(first.call_count, 1)
        self.assertEqual(repair.call_count, 1)
        self.assertFalse(repair.call_args.kwargs["allow_research"])
        self.assertEqual(repair.call_args.kwargs["repo_path"], self.session)
        self.assertEqual(repair.call_args.kwargs["timeout_seconds"], 240)
        self.assertEqual(result.attempts[1]["kind"], "repair")
        self.assertEqual(result.attempts[1]["model"], "claude-opus-5-5")

    def test_repair_cannot_invent_a_missing_code_line_or_url(self):
        for key, value in (("line", 12345), ("url", "https://example.com/invented")):
            invalid = json.loads(json.dumps(self.valid))
            repaired = json.loads(json.dumps(self.valid))
            if key == "line":
                invalid["plan"][0]["evidence"][0]["line"] = None
                repaired["plan"][0]["evidence"][0]["line"] = value
            else:
                invalid["plan"][0].pop("risks")
                repaired["plan"][0]["evidence"][1]["url"] = value
            with self.subTest(key=key), patch.object(run_moa.codex_adapter, "run", side_effect=[output(invalid), output(repaired)]) as call:
                result = self.run_lane()
                self.assertFalse(result.success)
                self.assertEqual(call.call_count, 2)
                self.assertIn("unsupplied", result.error)
                self.assertFalse((self.session / "layer1/fixture-proposer.json").exists())

    def test_failed_repair_never_restarts_research_or_repairs_recursively(self):
        invalid = json.loads(json.dumps(self.valid));invalid["plan"][0].pop("risks")
        with patch.object(run_moa.opencode_adapter, "run", return_value=output(invalid)) as call, patch.object(run_moa, "retry_sleep") as sleep:
            result = self.run_lane(harness="opencode", model="opencode-go/grok-4.7")
        self.assertFalse(result.success)
        self.assertEqual(call.call_count, 2)
        self.assertIn("bounded schema repair failed", result.error)
        sleep.assert_not_called()
        self.assertFalse((self.session / "layer1/fixture-proposer.json").exists())

    def test_incomplete_output_retries_then_repairs_inside_same_budget(self):
        invalid = json.loads(json.dumps(self.valid));invalid["plan"][0].pop("risks")
        with patch.object(run_moa.opencode_adapter, "run", side_effect=[output(error="could not extract JSON payload", incomplete=True), output(invalid), output(self.valid)]) as call, patch.object(run_moa, "retry_sleep"):
            result = self.run_lane(harness="opencode", model="opencode-go/grok-4.7")
        self.assertTrue(result.success)
        self.assertEqual(call.call_count, 3)
        self.assertEqual([x["kind"] for x in result.attempts], ["research", "research", "repair"])

    def test_retry_after_is_respected(self):
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(error="HTTP 429 Retry-After: 30"), output(self.valid)]), patch.object(run_moa, "retry_sleep") as sleep:
            self.assertTrue(self.run_lane().success)
        self.assertEqual(sleep.call_args.args[0], 30)

    def test_retry_after_beyond_deadline_does_not_sleep_or_invoke(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="HTTP 429 Retry-After: 900")) as call, patch.object(run_moa, "retry_sleep") as sleep:
            self.assertFalse(self.run_lane(timeout=50).success)
        self.assertEqual(call.call_count, 1);sleep.assert_not_called()

    def test_deadline_exhausted_by_first_call_does_not_restart_timeout(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="timeout after 20s")) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 51, 51]), patch.object(run_moa, "retry_sleep") as sleep:
            result = self.run_lane(timeout=50)
        self.assertFalse(result.success);self.assertEqual(call.call_count, 1);sleep.assert_not_called()

    def test_schema_repair_timeout_is_clipped_to_remaining_deadline(self):
        invalid = json.loads(json.dumps(self.valid));invalid["plan"][0].pop("risks")
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(invalid), output(self.valid)]) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 880, 885]):
            result = self.run_lane()
        self.assertTrue(result.success)
        self.assertEqual(call.call_args_list[1].kwargs["timeout_seconds"], 20)
        self.assertFalse(call.call_args_list[1].kwargs["allow_tools"])

    def test_workspace_mutation_is_terminal_and_cannot_be_repaired(self):
        before = run_moa.WorkspaceSnapshot("before", (), ())
        after = run_moa.WorkspaceSnapshot("after", ("changed.py",), (("changed.py", "changed"),))
        with patch.object(run_moa.codex_adapter, "run", return_value=output(self.valid)) as call, patch.object(run_moa, "_workspace_snapshot", side_effect=[before, after]):
            result = self.run_lane()
        self.assertFalse(result.success)
        self.assertEqual(result.failure_kind, "workspace_mutation")
        self.assertEqual(call.call_count, 1)
        self.assertFalse((self.session / "layer1/fixture-proposer.json").exists())

    def test_retired_families_cannot_return_through_custom_aliases(self):
        for model in ("qwen-token-plan/qwen3.8-max", "opencode-go/qwen3.8-flash", "openrouter/moonshotai/kimi-k3", "opencode-go/mimo-v2.6-pro", "opencode-go/hy4-preview", "opencode-go/deepseek-v4-pro"):
            self.assertTrue(run_moa.harness_config.provider_is_retired("custom-route", model))
            self.assertFalse(run_moa.harness_config.provider_allows_role("custom-route", "proposer", model))
        self.assertFalse(run_moa.harness_config.provider_is_retired("minimax", "opencode-go/minimax-m3"))

    def test_unverified_missing_receipt_does_not_invalidate_other_checks(self):
        payload = _make_valid_broadcast_refiner("fixture")
        record = payload["verifications"][0]
        record.update(status="unverified", source_url="missing.py:9999", actual_finding="The cited file could not be found; this claim is unverified.")
        self.assertEqual(run_moa._validate_refiner_verification_cross_fields(payload, self.root), [])
        self.assertEqual(record["status"], "unverified")
        record["status"] = "verified"
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, self.root))
        record["status"] = "contradicted"
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, self.root))
        record.update(status="unverified", source_url="../outside.py:1")
        self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, self.root))

    def test_unverified_receipts_keep_file_safety_checks(self):
        payload = _make_valid_broadcast_refiner("fixture")
        record = payload["verifications"][0]
        record.update(status="unverified", actual_finding="The cited receipt could not be inspected.")
        (self.root / "short.py").write_text("one line\n")
        record["source_url"] = "short.py:9999"
        self.assertEqual(run_moa._validate_refiner_verification_cross_fields(payload, self.root), [])
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private.py"
            target.write_text("outside repository\n")
            (self.root / "linked.py").symlink_to(target)
            (self.root / "linked-dir").symlink_to(outside, target_is_directory=True)
            (self.root / "binary.py").write_bytes(b"\x00binary")
            (self.root / "directory.py").mkdir()
            for locator in ("linked.py:1", "linked-dir/private.py:1", "binary.py:1", "directory.py:1"):
                with self.subTest(locator=locator):
                    record["source_url"] = locator
                    self.assertTrue(run_moa._validate_refiner_verification_cross_fields(payload, self.root))

    def test_existing_attempt_logs_are_preserved_across_manual_redispatch(self):
        old = self.session / "layer1/fixture-proposer.log";old.write_text("original failed provider receipt")
        with patch.object(run_moa.codex_adapter, "run", return_value=output(self.valid)) as call:
            result = self.run_lane()
        self.assertTrue(result.success)
        self.assertEqual(old.read_text(), "original failed provider receipt")
        self.assertNotEqual(call.call_args.kwargs["log_file"], old)
        prior = run_moa.LayerResult(agent_id="fixture", layer=1, role="proposer", attempt=2,
                                   attempts=[{"attempt": 1, "error": "capacity"}, {"attempt": 2, "error": "capacity"}])
        run_moa.mark_redispatch_attempts([result], [prior])
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(result.attempt, 3)

    def test_atomic_checkpoint_and_payload_are_complete_json(self):
        target=self.session/'checkpoint.json';target.write_text('{"prior":true}')
        run_moa._write_text_atomic(target, json.dumps(self.valid))
        self.assertEqual(json.loads(target.read_text()), self.valid)
        self.assertEqual(list(self.session.glob('checkpoint.json.*')), [])

    def test_atomic_replacement_closes_the_temporary_file_first(self):
        opened = []
        factory = run_moa.tempfile.NamedTemporaryFile
        replace = run_moa.os.replace
        def track(*args, **kwargs):
            file = factory(*args, **kwargs);opened.append(file);return file
        def windows_replace(source, target):
            self.assertTrue(opened[-1].closed)
            replace(source, target)
        with patch.object(run_moa.tempfile, "NamedTemporaryFile", side_effect=track), patch.object(run_moa.os, "replace", side_effect=windows_replace):
            run_moa._write_text_atomic(self.session / "closed.json", '{"complete":true}')
        self.assertEqual(json.loads((self.session / "closed.json").read_text()), {"complete": True})

    def test_aggregator_capacity_recovers_and_validates_lineage(self):
        (self.session / "synthesis-input.md").write_text("# Synthesis\nfixture")
        payload = {"final_plan_markdown": "# Final Plan\n" + "A bounded documentation plan. " * 10,
                   "lineage": {"version": 1, "title": "Small documentation change", "summary": "Improve docs.", "confidence": {"level": "high", "rationale": "Reviewers agree."}, "steps": [], "rejected_inputs": []}}
        provider = run_moa.harness_config.ResolvedProvider("fixture", "codex", "gpt-6.1-sol", 900)
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(error="HTTP 503 overloaded"), output(payload)]) as call, patch.object(run_moa, "retry_sleep"):
            result = run_moa.run_layer3(provider=provider, repo_path=self.root, session_dir=self.session, timeout=900, codex_effort="xhigh", layer1=[], layer2=[])
        self.assertTrue(result.success);self.assertEqual(call.call_count, 2)
        self.assertEqual((self.session / "final-plan.md").read_text(), payload["final_plan_markdown"])

    def test_synthesis_resolves_route_effort_and_retains_prior_attempt(self):
        scout = self.session / "scout-brief.json"
        scout.write_text(
            json.dumps(
                {
                    "session_id": "fixture",
                    "repo_path": str(self.root),
                    "frozen_spec": "Compare a small implementation plan.",
                }
            )
        )
        (self.session / "synthesis-input.md").write_text("# Synthesis\nfixture")
        prior = run_moa.LayerResult(
            agent_id="codex-sol",
            layer=3,
            role="aggregator",
            error="timeout after 1200s",
            duration_seconds=1200,
            log_path="layer3/prior.log",
        )
        for flag, expected in (
            ([], "medium"),
            (["--aggregator-effort", "high"], "high"),
        ):
            with self.subTest(expected=expected):
                manifest = {
                    "layer1": [],
                    "layer2": [],
                    "layer3": [
                        {
                            k: v
                            for k, v in run_moa.asdict(prior).items()
                            if k != "payload"
                        }
                    ],
                    "summary": {"timeout_failures": ["codex-sol"]},
                    "config": {},
                }
                (self.session / "manifest.json").write_text(json.dumps(manifest))
                result = run_moa.LayerResult(
                    agent_id="codex-sol",
                    layer=3,
                    role="aggregator",
                    error="fixture stop",
                    attempts=[{"attempt": 1, "error": "fixture stop"}],
                )
                argv = [
                    "run_moa.py",
                    "--scout-brief",
                    str(scout),
                    "--repo",
                    str(self.root),
                    "--phase",
                    "layer3",
                    "--aggregator-provider",
                    "codex-sol",
                    "--no-report",
                    *flag,
                ]
                with (
                    patch.object(sys, "argv", argv),
                    patch.dict(
                        run_moa.os.environ,
                        {"MOA_CODEX_SOL_EFFORT": "medium", "MOA_AGGREGATOR_EFFORT": ""},
                    ),
                    patch.object(
                        run_moa.codex_adapter,
                        "check_available",
                        return_value=(True, "fixture"),
                    ),
                    patch.object(run_moa, "run_layer3", return_value=result) as call,
                    patch.object(
                        run_moa, "_global_lock", return_value=contextlib.nullcontext()
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(run_moa.main(), 5)
                self.assertEqual(call.call_args.kwargs["codex_effort"], expected)
                updated = json.loads((self.session / "manifest.json").read_text())
                self.assertEqual(
                    updated["config"]["aggregator"]["reasoning_effort"], expected
                )
                self.assertEqual(updated["summary"]["timeout_failures"], [])
                self.assertEqual(
                    updated["layer3"][0]["attempts"][0]["log_path"], "layer3/prior.log"
                )
                self.assertEqual(updated["layer3"][0]["attempt"], 2)

    def test_cli_synthesis_refuses_mutated_research(self):
        scout = self.session / "scout-brief.json"
        scout.write_text(json.dumps({"session_id": "fixture", "repo_path": str(self.root), "frozen_spec": "Retain trustworthy research."}))
        (self.session / "synthesis-input.md").write_text("Retained input.")
        (self.session / "manifest.json").write_text(json.dumps({"summary": {"workspace_mutation_failures": ["fixture"]}, "layer1": [], "layer2": []}))
        argv = ["run_moa.py", "--scout-brief", str(scout), "--repo", str(self.root), "--phase", "layer3"]
        with patch.object(sys, "argv", argv), patch.object(run_moa, "_global_lock", return_value=contextlib.nullcontext()), patch.object(run_moa, "run_layer3") as call, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_moa.main(), 4)
        call.assert_not_called()

    def test_synthesis_preserves_workspace_mutation_warnings(self):
        manifest = {
            "layer1": [
                {
                    "agent_id": "fixture",
                    "success": True,
                    "attempts": [{"failure_kind": "workspace_mutation"}],
                }
            ],
            "layer2": [],
            "summary": {
                "workspace_mutation_failures": ["prior-aggregator"],
                "timeout_failures": ["prior-aggregator"],
            },
        }
        (self.session / "manifest.json").write_text(json.dumps(manifest))
        provider = run_moa.harness_config.ResolvedProvider(
            "fixture-aggregator", "codex", "gpt-6.1-sol", 900
        )
        result = run_moa.LayerResult(
            agent_id=provider.name, layer=3, role="aggregator", success=True
        )
        with patch.object(run_moa, "_refresh_decision_map"):
            run_moa.update_manifest_layer3(self.session, result, provider, "medium")
        summary = json.loads((self.session / "manifest.json").read_text())["summary"]
        self.assertEqual(
            summary["workspace_mutation_failures"], ["fixture", "prior-aggregator"]
        )
        self.assertEqual(summary["timeout_failures"], [])

    def test_synthesis_keeps_default_prior_log_and_ignores_stale_payload(self):
        folder = self.session / "layer3"
        folder.mkdir()
        prior_log = folder / "codex-sol-aggregator.log"
        prior_log.write_text("Original aggregation receipt.")
        prior_json = folder / "codex-sol-aggregator.json"
        payload = {
            "final_plan_markdown": "# Final Plan\n" + "A bounded plan. " * 20,
            "lineage": {
                "version": 1,
                "title": "Small change",
                "summary": "Retain evidence.",
                "confidence": {"level": "high", "rationale": "Independent review."},
                "steps": [],
                "rejected_inputs": [],
            },
        }
        prior_json.write_text(json.dumps(payload))
        (self.session / "synthesis-input.md").write_text("Retained research.")
        provider = run_moa.harness_config.ResolvedProvider(
            "codex-sol", "codex", "gpt-6.1-sol", 900
        )
        for succeeds in (False, True):

            def invoke(**kwargs):
                kwargs["log_file"].write_text("Replacement call receipt.")
                return (
                    output(payload)
                    if succeeds
                    else output(error="HTTP 403 Model access is disabled")
                )

            with (
                self.subTest(succeeds=succeeds),
                patch.object(run_moa.codex_adapter, "run", side_effect=invoke),
            ):
                result = run_moa.run_layer3(
                    provider=provider,
                    repo_path=self.root,
                    session_dir=self.session,
                    timeout=900,
                    codex_effort="medium",
                    layer1=[],
                    layer2=[],
                )
            self.assertEqual(result.success, succeeds)
            self.assertEqual(prior_log.read_text(), "Original aggregation receipt.")
            self.assertNotEqual(self.session / result.log_path, prior_log)
            self.assertEqual(
                (self.session / result.log_path).read_text(),
                "Replacement call receipt.",
            )
            if not succeeds:
                self.assertIsNone(result.payload)
                self.assertIsNone(result.json_path)

    def test_native_agy_schema_file_is_within_private_mirror(self):
        mirror = self.root / "mirror";mirror.mkdir()
        process = Mock(returncode=0);process.communicate.return_value=(json.dumps(self.valid), "")
        with patch.object(agy, "_isolated_git_workspace", return_value=contextlib.nullcontext(mirror)), patch.object(agy.subprocess, "Popen", return_value=process) as popen:
            result = agy.run(prompt="fixture", repo_path=self.root, model="gemini-4-argon-high", log_file=self.session / "layer1/fixture-proposer.log", schema_path=run_moa.PROPOSER_SCHEMA_PATH)
        self.assertTrue(result.success)
        command = popen.call_args.args[0]
        schema_file = Path(command[command.index("--json-schema") + 1])
        self.assertTrue(schema_file.is_relative_to(mirror))
        self.assertEqual(json.loads(schema_file.read_text()), json.loads(run_moa.PROPOSER_SCHEMA_PATH.read_text()))
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--output-format") + 1], "json")

    def test_agy_native_envelopes_and_invalid_roots_are_preserved(self):
        for value in (self.valid, {"structured_output": self.valid}, {"result": json.dumps(self.valid)}):
            self.assertEqual(agy._extract_payload(json.dumps(value), required_keys=set(self.valid)), self.valid)
        invalid = json.loads(json.dumps(self.valid));invalid.pop("open_questions")
        self.assertEqual(agy._extract_payload(json.dumps(invalid), required_keys=set(self.valid)), invalid)
        self.assertIsNone(agy._extract_payload(json.dumps({"evidence": [self.valid["plan"][0]["evidence"][0]]}), required_keys=set(self.valid)))
        self.assertIsNone(agy._extract_payload(json.dumps({"is_error": True, "structured_output": self.valid})))
        reviewer = _make_valid_broadcast_refiner("fixture")
        required = set(reviewer);reviewer.pop("missing_steps")
        self.assertEqual(agy._extract_payload(json.dumps(reviewer), required_keys=required), reviewer)

    def test_cancel_signal_interrupts_real_backoff_without_launching_next_call(self):
        if sys.platform == "win32":self.skipTest("POSIX process groups")
        # Start a real orchestrator recovery loop with a fake provider returning
        # capacity failures. SIGTERM during sleep must exit before another call.
        script = '''import sys\nsys.path.insert(0, sys.argv[1])\nimport run_moa\nfrom pathlib import Path\nfrom types import SimpleNamespace\ndef invoke(*a,**kw):\n print('CALLED',flush=True)\n return SimpleNamespace(success=False,payload=None,error_message='HTTP 429 at capacity',raw_stderr='',duration_seconds=0,transient_empty=False)\nrun_moa._invoke_adapter=invoke\nrun_moa._run_with_recovery(harness='codex',layer=1,role='proposer',prompt='',schema_path=run_moa.PROPOSER_SCHEMA_PATH,repo_path=Path(sys.argv[2]),session_dir=Path(sys.argv[2]),timeout=900,model='gpt-6.1-sol',agent_id='fixture')\n'''
        process = subprocess.Popen([sys.executable, "-u", "-c", script, str(Path(run_moa.__file__).parent), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "CALLED")
            self.assertIn("retrying capacity", process.stdout.readline())
            start = time.monotonic();process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(process.returncode, -signal.SIGTERM)
            self.assertNotIn("CALLED", stdout)
        finally:
            if process.poll() is None:process.kill();process.communicate()


if __name__ == "__main__":unittest.main()
