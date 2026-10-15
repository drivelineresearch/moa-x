#!/usr/bin/env python3
"""Offline unit tests for deterministic Layer 0 repository reconnaissance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scout import build_scout  # noqa: E402


class ScoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _init_git_repo(self, repo: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "init",
            ],
            cwd=repo,
            check=True,
        )

    def test_git_repo_selects_relevant_code_interfaces_and_tests_safely(self) -> None:
        repo = self.root / "repo"
        outside = self.root / "outside"
        (repo / "billing").mkdir(parents=True)
        (repo / "tests").mkdir(parents=True)
        outside.mkdir(parents=True)
        (outside / " leaked.py").write_text(
            "OUTSIDE_SECRET_TOKEN = 'leak'\n", encoding="utf-8"
        )

        (repo / "billing" / "interfaces.py").write_text(
            "class RefundPolicy:\n    def max_refund_cents(self) -> int:\n        return 5000\n",
            encoding="utf-8",
        )
        (repo / "billing" / "processor.py").write_text(
            "from billing.interfaces import RefundPolicy\n\n"
            "def compute_idempotent_refund(amount: int, policy: RefundPolicy) -> int:\n"
            "    return min(amount, policy.max_refund_cents())\n",
            encoding="utf-8",
        )
        (repo / "tests" / "test_processor.py").write_text(
            "from billing.processor import compute_idempotent_refund\n\n"
            "def test_compute_idempotent_refund() -> None:\n"
            "    assert compute_idempotent_refund(100, None) == 100\n",
            encoding="utf-8",
        )
        (repo / "billing" / "unrelated.py").write_text(
            "def render_marketing_banner() -> str:\n    return 'hello'\n",
            encoding="utf-8",
        )
        (repo / ".env").write_text(
            "SECRET_KEY=env_super_secret_value\n", encoding="utf-8"
        )
        (repo / "secrets").mkdir()
        (repo / "secrets" / "creds.txt").write_text(
            "cred_super_secret_value\n", encoding="utf-8"
        )
        (repo / "node_modules" / "pkg").mkdir(parents=True)
        (repo / "node_modules" / "pkg" / "index.js").write_text(
            "export const x = 1;\n", encoding="utf-8"
        )
        (repo / "billing" / "blob.bin").write_bytes(b"\x00\x01\x02binary_payload")
        if sys.platform != "win32":
            (repo / "billing" / "escape_link.py").symlink_to(outside / " leaked.py")
            (repo / "linked_dir").symlink_to(outside, target_is_directory=True)

        self._init_git_repo(repo)
        before_status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo
        )

        result = build_scout(
            repo,
            "Fix compute_idempotent_refund in billing/processor.py to enforce RefundPolicy limits",
            depth="balanced",
        )
        after_status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo
        )
        self.assertEqual(before_status, after_status)

        focus = result["focus_files"]
        self.assertIn("billing/processor.py", focus)
        self.assertIn("billing/interfaces.py", focus)
        self.assertIn("tests/test_processor.py", focus)
        self.assertNotIn("billing/unrelated.py", focus)

        serialized = json.dumps(result)
        for forbidden in (
            "env_super_secret_value",
            "cred_super_secret_value",
            "OUTSIDE_SECRET_TOKEN",
            "node_modules",
            ".env",
        ):
            self.assertNotIn(
                forbidden,
                serialized.replace(
                    "(.git, .moa, .venv, node_modules, build/dist, .env, secrets)", ""
                ),
            )

        ctx = result["repository_context"]
        self.assertEqual(ctx["source"], "bounded_static_scout")
        self.assertTrue(ctx["untrusted_data"])
        by_path = {item["path"]: item for item in ctx["selected_files"]}
        self.assertIn(
            "compute_idempotent_refund", by_path["billing/processor.py"]["symbols"]
        )
        self.assertEqual(len(by_path["billing/processor.py"]["sha256"]), 64)
        self.assertTrue(
            any(
                ex.startswith("1:") or "\n3:" in ex
                for ex in by_path["billing/processor.py"]["excerpts"]
            )
        )

    def test_non_git_fallback_and_existing_preservation(self) -> None:
        other_git = self.root / "other_git"
        other_git.mkdir()
        (other_git / "wrong.py").write_text(
            "def wrong_repo(): pass\n", encoding="utf-8"
        )
        self._init_git_repo(other_git)

        non_git = self.root / "non_git"
        (non_git / "app").mkdir(parents=True)
        (non_git / "app" / "router.py").write_text(
            "def dispatch_webhook():\n    return True\n", encoding="utf-8"
        )
        (non_git / "app" / "test_router.py").write_text(
            "def test_dispatch_webhook():\n    assert True\n", encoding="utf-8"
        )

        existing = {
            "session_id": "20261014-fixture",
            "focus_files": ["app/custom.py"],
            "focus_topics": ["Keep existing topic"],
            "in_scope": ["Keep existing in_scope"],
            "out_of_scope": ["Keep existing out_of_scope"],
        }
        with patch.dict(
            os.environ,
            {"GIT_DIR": str(other_git / ".git"), "GIT_WORK_TREE": str(other_git)},
        ):
            result = build_scout(
                non_git, "Update dispatch_webhook in router", existing=existing
            )

        self.assertEqual(result["session_id"], "20261014-fixture")
        self.assertEqual(result["focus_files"][0], "app/custom.py")
        self.assertIn("app/router.py", result["focus_files"])
        self.assertIn("app/test_router.py", result["focus_files"])
        self.assertNotIn("wrong.py", result["focus_files"])
        self.assertIn("Keep existing topic", result["focus_topics"])
        self.assertIn("Keep existing in_scope", result["in_scope"])
        self.assertIn("Keep existing out_of_scope", result["out_of_scope"])

    def test_empty_workspace_and_depth_budgets(self) -> None:
        empty = self.root / "empty_brief"
        (empty / ".moa").mkdir(parents=True)
        (empty / ".moa" / "scout-brief.json").write_text("{}", encoding="utf-8")
        empty_res = build_scout(empty, "Design a new caching architecture")
        self.assertEqual(empty_res["focus_files"], [])
        self.assertEqual(empty_res["repository_context"]["inventory_count"], 0)
        self.assertEqual(empty_res["repository_context"]["selected_files"], [])

        with self.assertRaises(ValueError):
            build_scout(empty, "Goal", depth="invalid")

        large = self.root / "large"
        large.mkdir()
        for idx in range(25):
            (large / f"mod_{idx:02d}.py").write_text(
                f"def target_symbol_{idx}():\n"
                + ("    x = 'target_symbol content line'\n" * 80),
                encoding="utf-8",
            )
        q = build_scout(large, "Refactor target_symbol across modules", depth="quick")
        b = build_scout(
            large, "Refactor target_symbol across modules", depth="balanced"
        )
        t = build_scout(
            large, "Refactor target_symbol across modules", depth="thorough"
        )
        self.assertLess(len(q["focus_files"]), len(b["focus_files"]))
        self.assertLess(len(b["focus_files"]), len(t["focus_files"]))
        self.assertLessEqual(len(t["focus_files"]), 15)
        self.assertLessEqual(
            len(json.dumps(t["repository_context"]).encode("utf-8")), 24 * 1024
        )


if __name__ == "__main__":
    unittest.main()
