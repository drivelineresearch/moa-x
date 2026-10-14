"""Offline regressions for provider recovery, budgets and attempt provenance."""
from __future__ import annotations
import contextlib
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
        with patch.object(run_moa.codex_adapter, "run", side_effect=[first, output(self.valid)]) as call, patch.object(run_moa.time, "sleep") as sleep:
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
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="HTTP 429 model at capacity")) as call, patch.object(run_moa.time, "sleep"):
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
            with self.subTest(error=error), patch.object(run_moa.codex_adapter, "run", return_value=output(error=error, incomplete=True)) as call, patch.object(run_moa.time, "sleep") as sleep:
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
        with patch.object(run_moa.opencode_adapter, "run", return_value=output(invalid)) as call, patch.object(run_moa.time, "sleep") as sleep:
            result = self.run_lane(harness="opencode", model="opencode-go/grok-4.7")
        self.assertFalse(result.success)
        self.assertEqual(call.call_count, 2)
        self.assertIn("bounded schema repair failed", result.error)
        sleep.assert_not_called()
        self.assertFalse((self.session / "layer1/fixture-proposer.json").exists())

    def test_incomplete_output_retries_then_repairs_inside_same_budget(self):
        invalid = json.loads(json.dumps(self.valid));invalid["plan"][0].pop("risks")
        with patch.object(run_moa.opencode_adapter, "run", side_effect=[output(error="could not extract JSON payload", incomplete=True), output(invalid), output(self.valid)]) as call, patch.object(run_moa.time, "sleep"):
            result = self.run_lane(harness="opencode", model="opencode-go/grok-4.7")
        self.assertTrue(result.success)
        self.assertEqual(call.call_count, 3)
        self.assertEqual([x["kind"] for x in result.attempts], ["research", "research", "repair"])

    def test_retry_after_is_respected(self):
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(error="HTTP 429 Retry-After: 30"), output(self.valid)]), patch.object(run_moa.time, "sleep") as sleep:
            self.assertTrue(self.run_lane().success)
        self.assertEqual(sleep.call_args.args[0], 30)

    def test_retry_after_beyond_deadline_does_not_sleep_or_invoke(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="HTTP 429 Retry-After: 900")) as call, patch.object(run_moa.time, "sleep") as sleep:
            self.assertFalse(self.run_lane(timeout=50).success)
        self.assertEqual(call.call_count, 1);sleep.assert_not_called()

    def test_deadline_exhausted_by_first_call_does_not_restart_timeout(self):
        with patch.object(run_moa.codex_adapter, "run", return_value=output(error="timeout after 20s")) as call, patch.object(run_moa.time, "monotonic", side_effect=[0, 0, 51, 51]), patch.object(run_moa.time, "sleep") as sleep:
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
        with patch.object(run_moa.codex_adapter, "run", side_effect=[output(error="HTTP 503 overloaded"), output(payload)]) as call, patch.object(run_moa.time, "sleep"):
            result = run_moa.run_layer3(provider=provider, repo_path=self.root, session_dir=self.session, timeout=900, codex_effort="xhigh", layer1=[], layer2=[])
        self.assertTrue(result.success);self.assertEqual(call.call_count, 2)
        self.assertEqual((self.session / "final-plan.md").read_text(), payload["final_plan_markdown"])

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
