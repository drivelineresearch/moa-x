#!/usr/bin/env python3
"""Offline behavioral checks for the optional native transports."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import run_moa
from adapters import native
from model_labs import route_lab_id


TREE = "\n".join("- id: " + name for name in (
    "agent-default-model", "tool-fs", "tool-fs-search", "tool-web", "tool-bash",
    "tool-subagent", "tool-plugin-manager", "tool-future-dangerous"))

FAKE = '''#!/usr/bin/env python3
import sys,json,os,time
from pathlib import Path
if '--dump-default-config' in sys.argv:
 print(os.environ['MOA_TEST_TREE']);sys.exit(0)
args=sys.argv[1:]
prompt=Path(args[args.index('--prompt-file')+1]).read_text() if '--prompt-file' in args else sys.stdin.read()
record={'args':args,'cwd':os.getcwd(),'prompt_length':len(prompt),'key_present':bool(os.environ.get('DEEPSEEK_API_KEY')),'had_readme':Path('README.md').exists()}
if '--patch' in args:record['overlay']=json.loads(Path(args[args.index('--patch')+1]).read_text())
Path(os.environ['MOA_TEST_RECORD']).write_text(json.dumps(record))
Path('README.md').write_text('changed only in snapshot')
if os.environ.get('MOA_TEST_CASE')=='timeout':time.sleep(30)
answer=json.dumps({'ok':True})
print(json.dumps({'type':'tool_result','text':'{"ok":false}'}))
if '--prompt-file' in args:
 print(json.dumps({'payload_type':'run.terminal.completed','payload':{'text':answer}}))
else:print(json.dumps({'type':'final','text':answer}))
if os.environ.get('MOA_TEST_CASE')=='nonzero':sys.exit(3)
'''


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("original")
        self.bin = self.root / "native-cli"
        self.bin.write_text(FAKE)
        self.bin.chmod(0o755)
        self.record = self.root / "record.json"
        self.env = patch.dict(os.environ, {
            "MOA_MUSE_BIN": str(self.bin), "MOA_DEEPSEEK_BIN": str(self.bin),
            "MOA_TEST_RECORD": str(self.record), "MOA_TEST_TREE": TREE,
            "DEEPSEEK_API_KEY": "offline-fixture", "MOA_TEST_CASE": "",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def call(self, kind, **kwargs):
        return native.run(kind, prompt="x" * 200_000, repo_path=self.repo,
                          model="muse-spark-1.3" if kind == "muse" else "deepseek-flash",
                          log_file=self.root / "capture.log", **kwargs)

    def test_optional_routes_keep_defaults_and_lab_identity(self):
        for name, harness, lab in [("muse-native", "muse", "meta"),
                                   ("deepseek-native", "deepseek", "deepseek")]:
            p = config.resolve_provider(name, user_providers={})
            self.assertEqual(p.harness, harness)
            self.assertEqual(route_lab_id(name, p.model), lab)
            self.assertFalse(config.provider_is_retired(name, p.model))
        self.assertEqual(config._DEFAULT_REFINERS, ["deepseek-flash", "opus"])

    def test_tool_and_partial_events_cannot_become_results(self):
        for kind, text in [("muse", json.dumps({"payload_type": "run.output.delta", "payload": {"text": '{"ok":true}'}})),
                           ("deepseek", json.dumps({"type": "tool_result", "text": '{"ok":true}'}))]:
            self.assertEqual(native.terminal_answer(kind, text)[0], "")
            self.assertIsNotNone(native.terminal_answer(kind, text)[1])

    def test_terminal_failure_overrides_preceding_answer(self):
        text = '\n'.join(json.dumps(e) for e in [
            {"payload_type": "run.terminal.completed", "payload": {"text": '{"ok":true}'}},
            {"payload_type": "run.terminal.failed", "payload": {"reason": "unauthorized"}}])
        self.assertEqual(native.terminal_answer("muse", text), ("", "unauthorized"))

    def test_deepseek_disables_future_tools_and_repairs_are_tool_free(self):
        for tools in [True, False]:
            rows = {r["id"]: r for r in native.dsh_overlay(TREE, "deepseek-flash", "max", tools, True)}
            for name in ["tool-bash", "tool-subagent", "tool-plugin-manager", "tool-future-dangerous"]:
                self.assertTrue(rows[name]["disabled"])
            for name in ["tool-fs", "tool-fs-search", "tool-web"]:
                self.assertEqual(rows[name]["disabled"], not tools)
            self.assertFalse(rows['tool-fs']['config']['write'])
            self.assertFalse(rows['tool-fs']['config']['edit'])
            self.assertTrue(rows['plan-mode']['disabled'])
        with self.assertRaises(RuntimeError):
            native.dsh_overlay("- id: unknown", "deepseek-flash", None, True, True)

    def test_both_transports_isolate_source_and_large_prompts(self):
        for kind in ["muse", "deepseek"]:
            with self.subTest(kind=kind):
                result = self.call(kind, reasoning_effort="high")
                self.assertTrue(result.success, result.error_message)
                self.assertEqual(result.payload, {"ok": True})
                record = json.loads(self.record.read_text())
                self.assertNotEqual(Path(record['cwd']), self.repo)
                self.assertEqual((self.repo / 'README.md').read_text(), 'original')
                self.assertGreater(record['prompt_length'], 200_000)
                self.assertNotIn('offline-fixture', json.dumps(record['args']))
                self.assertIn('=== STDOUT ===', (self.root / 'capture.log').read_text())
                if kind == 'muse':
                    self.assertIn('--disable-shell', record['args'])
                    self.assertIn('--disable-write', record['args'])
                    self.assertIn('agents.execution_capacity=1', record['args'])
                    self.assertIn('run.workflow_trigger_mode="off"', record['args'])

    def test_muse_repair_withholds_tools_and_web(self):
        result = self.call('muse', allow_tools=False, allow_webfetch=False)
        self.assertTrue(result.success, result.error_message)
        args = json.loads(self.record.read_text())['args']
        self.assertFalse(json.loads(self.record.read_text())['had_readme'])
        self.assertEqual(args[args.index('--preset') + 1], 'empty')
        self.assertIn('--disable-web-tools', args)

    def test_nonzero_exit_never_accepts_a_valid_answer(self):
        with patch.dict(os.environ, {'MOA_TEST_CASE': 'nonzero'}):
            for kind in ['muse', 'deepseek']:
                result = self.call(kind)
                self.assertFalse(result.success)
                self.assertEqual(result.exit_code, 3)

    def test_timeout_stops_subprocess_and_retains_capture(self):
        with patch.dict(os.environ, {'MOA_TEST_CASE': 'timeout'}):
            result = self.call('muse', timeout_seconds=.4)
            self.assertFalse(result.success)
            self.assertIn('timed out', result.error_message)
            self.assertLess(result.duration_seconds, 2)
            self.assertTrue((self.root / 'capture.log').exists())

    def test_native_repair_dispatch_uses_shared_recovery_boundary(self):
        for name in ['muse', 'deepseek']:
            with patch.object(getattr(run_moa, name + '_adapter'), 'run') as invoke:
                run_moa._invoke_adapter(name, prompt='{}', schema_path=Path('schema.json'),
                    repo_path=self.repo, session_dir=self.root, timeout=10,
                    model='fixture', effort='high', log_file=self.root/'log', repair=True)
                self.assertFalse(invoke.call_args.kwargs['allow_tools'])
                self.assertFalse(invoke.call_args.kwargs['allow_webfetch'])

    def test_key_file_is_literal_and_empty_environment_wins(self):
        (self.root / '.env').write_text('DEEPSEEK_API_KEY="$(do-not-execute)"\n')
        with patch.dict(os.environ, {'DSH_HOME':str(self.root)}):
            os.environ.pop('DEEPSEEK_API_KEY')
            self.assertEqual(native.deepseek_key(), '$(do-not-execute)')
            os.environ['DEEPSEEK_API_KEY'] = ''
            self.assertEqual(native.deepseek_key(), '')

    def test_muse_browser_refresh_is_preserved_without_overwriting_new_login(self):
        source = self.root/'auth'
        source.mkdir()
        path = source/'auth.json'
        private = self.root/'private'
        private.mkdir()
        path.write_text('{"token":"before"}')
        with patch.object(native, 'muse_config_home', return_value=source), patch.dict(os.environ, {'META_API_KEY':''}):
            with native.muse_auth(private, native.time.monotonic()+1):
                (private/'auth.json').write_text('{"token":"refreshed"}')
            self.assertEqual(json.loads(path.read_text())['token'], 'refreshed')
            with native.muse_auth(private, native.time.monotonic()+1):
                path.write_text('{"token":"new external login"}')
                (private/'auth.json').write_text('{"token":"stale copy"}')
            self.assertEqual(json.loads(path.read_text())['token'], 'new external login')


if __name__ == '__main__':
    unittest.main()
