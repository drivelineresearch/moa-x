"""Offline tests for the OpenCode V2 transport and read-only contract."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adapters import opencode as oc


class OpenCodeV2Test(unittest.TestCase):
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
            def source_workspace(repo, scratch):
                yield repo
            def popen(command, **kwargs):
                captured.update(command=command, **kwargs)
                captured['policy'] = json.loads((Path(kwargs['cwd']) / '.opencode/opencode.json').read_text())
                return process
            with patch.object(oc, '_cli_major_version', return_value=2), patch.object(oc, '_isolated_git_workspace', source_workspace), patch.object(oc.subprocess, 'Popen', side_effect=popen):
                result = oc.run(prompt='Return fixture JSON.', repo_path=source, model='opencode-go/glm-5.3', reasoning_effort='high')
            self.assertTrue(result.success)
            self.assertEqual(result.payload, payload)
            self.assertEqual(captured['env']['PWD'], str(Path(captured['cwd']).resolve()))
            self.assertIn('--standalone', captured['command'])
            self.assertIn('--format', captured['command'])
            self.assertIn('opencode-go/glm-5.3#high', captured['command'])
            self.assertNotIn('--dir', captured['command'])
            self.assertNotIn('--variant', captured['command'])
            self.assertNotIn('OPENCODE_CONFIG', captured['env'])
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
        version = SimpleNamespace(returncode=0, stdout='opencode v3.0.0')
        with patch.object(oc.subprocess, 'run', return_value=version):
            with self.assertRaises(RuntimeError):
                oc._cli_major_version()


if __name__ == '__main__':
    unittest.main()
