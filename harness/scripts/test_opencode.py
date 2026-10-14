"""Offline tests for the OpenCode V2 transport and read-only contract."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adapters import opencode as oc
from adapters.failures import classify_failure


class OpenCodeV2Test(unittest.TestCase):
    def test_policy_error_survives_clean_exit_and_is_not_retried(self):
        cause = "This Go model trains on request data. Allow paid endpoints that train on request data in your workspace's Privacy settings to use it."
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"type": "error", "error": {
            "type": "provider.invalid-request", "status": 400, "message": cause,
        }}), "")
        with tempfile.TemporaryDirectory() as raw:
            @contextmanager
            def source_workspace(repo, scratch, **unused):
                yield repo
            with patch.object(oc, '_cli_major_version', return_value=2), patch.object(oc, '_isolated_git_workspace', source_workspace), patch.object(oc.subprocess, 'Popen', return_value=process):
                result = oc.run(prompt="Read the fixture.", repo_path=Path(raw), model="opencode-go/muse-spark-1.3-contributor")
        self.assertFalse(result.success)
        self.assertIn(cause, result.error_message)
        self.assertIn("HTTP 400", result.error_message)
        self.assertFalse(result.transient_empty)
        failure = classify_failure(result.error_message, result.raw_stderr)
        self.assertEqual(failure.kind, "configuration")
        self.assertFalse(failure.retryable)

    def test_ndjson_excludes_tool_payloads_and_preserves_typed_errors(self):
        output = '\n'.join(json.dumps(item) for item in [
            {"type": "tool_use", "part": {"text": '{"agent_id":"wrong"}'}},
            {"type": "text", "part": {"text": '{"agent_id":"correct"}'}},
            {"type": "error", "error": {"type": "provider.no-route", "message": "Model unavailable"}},
        ])
        text, error = oc._v2_response(output)
        self.assertEqual(json.loads(text)["agent_id"], "correct")
        self.assertIn("provider.no-route", error)
        self.assertFalse(oc._diagnose_failure('', error)[1])

    def test_unstructured_error_event_keeps_retryable_transport_cause(self):
        output = '\n'.join(json.dumps(item) for item in [
            {"type": "text", "part": "unexpected wrapper"},
            {"type": "error", "error": "HTTP 503 upstream error"},
        ])
        text, error = oc._v2_response(output)
        self.assertEqual(text, "")
        self.assertTrue(classify_failure(error).retryable)

    def test_disabled_model_access_is_not_transient(self):
        message,transient=oc._diagnose_failure('', 'provider.auth: Upstream request failed: Model access is disabled')
        self.assertFalse(transient);self.assertIn('auth',message.lower())
    def test_usage_errors_do_not_redispatch(self):
        self.assertFalse(oc._diagnose_failure('Usage: opencode run', 'Unrecognized flag: --dir')[1])

    def test_policy_denies_mutations_delegation_and_unknown_tools(self):
        config = oc._v2_config_for_model('opencode-go/qwen3.8-max', allow_tools=True, allow_webfetch=True)
        rules = config['agents']['moa-reader']['permissions']
        def effect(action, resource='fixture.py'):
            found = 'ask'
            import fnmatch
            for rule in rules:
                if fnmatch.fnmatchcase(action, rule['action']) and fnmatch.fnmatchcase(resource, rule['resource']):
                    found = rule['effect']
            return found
        for action in ['edit', 'shell', 'subagent', 'question', 'rea_tool', 'execute']:
            self.assertEqual(effect(action), 'deny')
        self.assertEqual(effect('read'), 'allow')
        self.assertEqual(effect('read', 'config/.env.production'), 'deny')
        self.assertEqual(config['agents']['moa-reader']['steps'], oc._QWEN_MAX_AGENT_STEPS)
        self.assertEqual(config['websearch'], {'provider': 'random'})
        self.assertEqual(config['providers']['opencode-go']['models']['qwen3.8-max']['settings']['thinking'],
                         {'type': 'enabled', 'budgetTokens': 8192})

    def test_slack_research_budget_is_bounded(self):
        with patch.dict(oc.os.environ, {'MOA_RESTRICT_WORKSPACE':'1'}):
            cfg = oc._v2_config_for_model('opencode-go/grok-4.7', allow_tools=True, allow_webfetch=True)
        self.assertEqual(cfg['agents']['moa-reader']['steps'],20)
    def test_repair_policy_denies_every_tool(self):
        cfg = oc._v2_config_for_model('opencode-go/glm-5.3', allow_tools=False, allow_webfetch=False)
        self.assertEqual(cfg['agents']['moa-reader']['permissions'], [{'action':'*', 'resource':'*', 'effect':'deny'}])
        self.assertIs(cfg['websearch'], False)

    def test_non_git_snapshot_leaves_source_untouched(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / 'source';source.mkdir()
            scratch = Path(raw) / 'scratch';scratch.mkdir()
            (source / 'file.txt').write_text('original')
            with oc._v2_workspace(source, scratch, allow_tools=True) as snapshot:
                self.assertNotEqual(snapshot, source)
                (snapshot / 'file.txt').write_text('changed')
            self.assertEqual((source / 'file.txt').read_text(), 'original')

    def test_v2_invocation_sets_pwd_and_private_policy(self):
        captured = {}
        payload = {'agent_id': 'smoke', 'result': 42}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'type':'text','part':{'text':json.dumps(payload)}}), '')
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw);(source / 'fixture.txt').write_text('42')
            @contextmanager
            def source_workspace(repo, scratch, **unused):
                yield repo
            def popen(command, **kwargs):
                captured.update(command=command, **kwargs)
                captured['policy'] = json.loads((Path(kwargs['cwd']) / '.opencode/opencode.json').read_text())
                captured['prompt'] = Path(command[command.index('--file') + 1]).read_text()
                return process
            with patch.dict(oc.os.environ, {'SLACK_BOT_TOKEN': 'test-secret'}), patch.object(oc, '_cli_major_version', return_value=2), patch.object(oc, '_isolated_git_workspace', source_workspace), patch.object(oc.subprocess, 'Popen', side_effect=popen):
                result = oc.run(prompt='Inspect '+str(source)+' and return fixture JSON.', repo_path=source, model='opencode-go/glm-5.3', reasoning_effort='high')
            self.assertTrue(result.success)
            self.assertEqual(result.payload, payload)
            self.assertEqual(captured['env']['PWD'], str(Path(captured['cwd']).resolve()))
            self.assertIn('--standalone', captured['command'])
            self.assertIn('--format', captured['command'])
            self.assertIn('opencode-go/glm-5.3#high', captured['command'])
            self.assertNotIn('--dir', captured['command'])
            self.assertNotIn('--variant', captured['command'])
            self.assertNotIn('OPENCODE_CONFIG', captured['env'])
            self.assertNotIn('SLACK_BOT_TOKEN', captured['env'])
            self.assertNotIn(str(source), captured['prompt'])
            self.assertIn(str(captured['cwd']), captured['prompt'])
            self.assertNotEqual(captured['env']['XDG_CONFIG_HOME'], str(Path.home() / '.config'))
            self.assertFalse((source / '.opencode').exists())

    def test_catalog_visibility_gates_authenticated_go_routes(self):
        auth = SimpleNamespace(returncode=0, stdout='OpenCode Go', stderr='')
        models = SimpleNamespace(returncode=0, stdout='opencode-go/grok-4.7\n', stderr='')
        with patch.object(oc.shutil, 'which', return_value='/usr/bin/opencode'), patch.object(oc, '_cli_major_version', return_value=2), patch.object(oc.subprocess, 'run', side_effect=[auth, models]):
            status = oc.check_models_available(['opencode-go/grok-4.5', 'opencode-go/grok-4.7'])
        self.assertFalse(status['opencode-go/grok-4.5'][0])
        self.assertTrue(status['opencode-go/grok-4.7'][0])

    def test_unknown_major_version_fails_closed(self):
        version = SimpleNamespace(returncode=0, stdout='opencode v3.0.0', stderr='')
        with patch.object(oc.subprocess, 'run', return_value=version):
            with self.assertRaises(RuntimeError):
                oc._cli_major_version()

    def test_restricted_runs_reject_v1_before_launch(self):
        with patch.dict(oc.os.environ, {"MOA_RESTRICT_WORKSPACE": "1"}), patch.object(oc, "_cli_major_version", return_value=1), patch.object(oc.subprocess, "Popen") as launch:
            result = oc.run(prompt="Inspect the repo", repo_path=Path("."), model="opencode-go/grok-4.7")
        self.assertFalse(result.success)
        self.assertIn("require OpenCode v2", result.error_message)
        launch.assert_not_called()

    def test_version_catalog_probe_errors_are_unavailable(self):
        auth = SimpleNamespace(returncode=0, stdout='OpenCode Go', stderr='')
        with patch.object(oc.shutil, 'which', return_value='/usr/bin/opencode'), patch.object(oc.subprocess, 'run', return_value=auth), patch.object(oc, '_cli_major_version', side_effect=RuntimeError('unsupported')):
            self.assertFalse(oc.check_model_available('opencode-go/grok-4.7')[0])


if __name__ == '__main__':
    unittest.main()
