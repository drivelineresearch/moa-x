"""Real offline Git regressions for fresh, independent launch snapshots."""

import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from harness.webui import github
from harness.webui.launch import LaunchService, LaunchError
from harness.webui.store import Store


class GitHubSnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote"
        self.managed = self.root / "managed"
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.git("init", "-b", "main", str(self.remote))
        self.git("-C", str(self.remote), "config", "user.name", "Test")
        self.git("-C", str(self.remote), "config", "user.email", "test@example.invalid")
        self.first = self.advance("first")
        self.clone_patch = patch.object(github, "_run_gh", side_effect=self.clone_from_fixture)
        self.clone_patch.start()
        self.addCleanup(self.clone_patch.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def advance(self, text):
        (self.remote / "answer.txt").write_text(text)
        self.git("-C", str(self.remote), "add", "answer.txt")
        self.git("-C", str(self.remote), "commit", "-m", text)
        return self.git("-C", str(self.remote), "rev-parse", "HEAD")

    def clone_from_fixture(self, args, *, timeout):
        self.assertEqual(args[:3], ["repo", "clone", "example-org/sample"])
        self.assertEqual(args[4], "--")
        return subprocess.run(["git", "clone", self.remote.as_uri(), args[3], *args[5:]],
                              capture_output=True, text=True, timeout=timeout, env=self.env)

    def snapshot(self, **kwargs):
        return github.clone_repository("example-org/sample", self.managed,
                                       owner_allowlist="example-org", **kwargs)

    def test_remote_advance_never_reuses_or_changes_old_workspace(self):
        legacy = self.managed / "example-org/sample"
        legacy.mkdir(parents=True)
        (legacy / "local-work.txt").write_text("preserve local edits")
        old = self.snapshot()
        archive = Path(old["path"]) / ".moa/old/report.html"
        archive.parent.mkdir(parents=True)
        archive.write_text("preserve old report")
        second = self.advance("second")
        new = self.snapshot()
        self.assertEqual(old["commit"], self.first)
        self.assertEqual(new["commit"], second)
        self.assertEqual(new["git_ref"], "main")
        self.assertNotEqual(old["path"], new["path"])
        self.assertEqual((Path(old["path"]) / "answer.txt").read_text(), "first")
        self.assertEqual((Path(new["path"]) / "answer.txt").read_text(), "second")
        self.assertEqual(archive.read_text(), "preserve old report")
        self.assertEqual((legacy / "local-work.txt").read_text(), "preserve local edits")
        head = subprocess.run(["git", "-C", new["path"], "symbolic-ref", "-q", "HEAD"],
                              capture_output=True, env=self.env)
        self.assertEqual(head.returncode, 1, "Snapshot HEAD must be detached")
        self.assertEqual(Path(new["path"]).stat().st_mode & 0o777, 0o700)
        self.assertTrue(new["fetched_at"])

    def test_branches_and_tags_are_fetched_independently(self):
        self.git("-C", str(self.remote), "tag", "v1")
        self.git("-C", str(self.remote), "checkout", "-b", "feature/review")
        branch_commit = self.advance("branch")
        branch = self.snapshot(git_ref="feature/review")
        tag = self.snapshot(git_ref="v1")
        self.assertEqual(branch["commit"], branch_commit)
        self.assertEqual(tag["commit"], self.first)
        self.assertEqual(tag["git_ref"], "v1")

    def test_same_commit_concurrent_requests_get_distinct_trees(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(lambda _: self.snapshot(), range(2)))
        self.assertNotEqual(snapshots[0]["path"], snapshots[1]["path"])
        self.assertEqual({s["commit"] for s in snapshots}, {self.first})

    def test_failure_cleans_partial_clone_without_stale_fallback(self):
        old = self.snapshot()
        with patch.object(github, "_run_gh", return_value=subprocess.CompletedProcess(
            ["gh"], 1, "", "network unavailable"
        )):
            with self.assertRaisesRegex(github.GitHubWorkspaceError, "clone failed"):
                self.snapshot()
        parent = Path(old["path"]).parent
        self.assertEqual(list(parent.iterdir()), [Path(old["path"])])
        self.assertEqual((Path(old["path"]) / "answer.txt").read_text(), "first")

    def test_failed_pin_removes_unpublished_snapshot(self):
        with patch.object(github.subprocess, "run", return_value=subprocess.CompletedProcess(
            ["git"], 1, "", "pin failed"
        )), patch.object(github, "_run_gh", side_effect=lambda args, **kw: (
            (Path(args[3]) / ".git").mkdir() or subprocess.CompletedProcess(["gh"], 0, "", "")
        )):
            with self.assertRaisesRegex(github.GitHubWorkspaceError, "pin"):
                self.snapshot()
        self.assertEqual(list((self.managed / "snapshots/example-org/sample").iterdir()), [])

    def test_launches_record_fresh_commit_and_snapshot_failure_queues_nothing(self):
        store = Store(self.root / "jobs.sqlite3")
        profile = store.upsert_profile("test-owner", "Test")
        service = LaunchService(store, upload_dir=self.root / "uploads",
                                github_workspace_dir=self.managed,
                                brief_workspace_dir=self.root / "brief",
                                github_owner="example-org", workspace_roots=[self.root],
                                catalog=lambda: [])
        body = {"goal": "Review the repository.", "source_mode": "github",
                "github_repository": "example-org/sample", "proposers": ["codex-sol"]}
        old = service.launch(profile, body, job_id="first-job")
        new_commit = self.advance("latest")
        new = service.launch(profile, body, job_id="second-job")
        self.assertNotEqual(old["workspace"], new["workspace"])
        self.assertEqual(new["config"]["source"]["commit"], new_commit)
        scout = json.loads((Path(new["session_dir"]) / "scout-brief.json").read_text())
        self.assertEqual(scout["source"], new["config"]["source"])
        same = service.launch(profile, body, job_id="second-job")
        self.assertEqual(same["workspace"], new["workspace"], "Transport replay is idempotent")
        with patch.object(github, "_run_gh", side_effect=github.GitHubWorkspaceError("offline")):
            with self.assertRaises(LaunchError):
                service.launch(profile, body, job_id="failed-job")
        self.assertIsNone(store.get_job("failed-job"))


if __name__ == "__main__":
    unittest.main()
