import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.slack.repositories import RepositoryDirectory, RepositoryPolicy
from harness.webui.github import GitHubWorkspaceError, list_repositories
from harness.webui.launch import LaunchError
from harness.slack.tests import test_slack as slack_tests


class RepositoryLookupTest(unittest.TestCase):
    def setUp(self):
        self.rows = [{"name": name, "nameWithOwner": "example-org/" + name}
                     for name in ("sample-service", "other-service", "Portal", "docs", "api", "moa-x", ".github")]
        self.listing = Mock(return_value=self.rows)
        self.clock = Mock(return_value=100)
        self.policy = RepositoryPolicy("example-org")
        self.directory = RepositoryDirectory(self.policy, listing=self.listing, now=self.clock)

    def test_short_names_punctuation_case_and_repo_context(self):
        for brief in ("Review sample-service.", "Look into `SAMPLE-SERVICE` for performance", "Audit the repo sample-service"):
            self.assertEqual(self.directory.resolve(brief), "example-org/sample-service")
        self.assertEqual(self.directory.resolve("Look at Portal"), "example-org/Portal")
        self.assertEqual(self.directory.resolve("Review repo docs"), "example-org/docs")
        self.assertEqual(self.directory.resolve("Review repo .github"), "example-org/.github")
        self.assertEqual(self.listing.call_count, 1)

    def test_explicit_url_pointer_and_slack_markup(self):
        for brief in ("Review example-org/sample-service", "Review repo example-org/sample-service", "<https://github.com/example-org/sample-service.git|sample-service>"):
            self.assertEqual(self.directory.resolve(brief), "example-org/sample-service")
        self.listing.assert_not_called()

    def test_plain_planning_words_do_not_select_a_repo(self):
        for brief in ("Plan a deployment", "Review API latency and write docs", "Use MoA-X to plan a deployment"):
            self.assertIsNone(self.directory.resolve(brief))

    def test_task_only_and_no_repository_bypass_name_discovery(self):
        for brief in ("Plan retries with no repository or files", "Without a repo, compare queues", "Task-only: review sample-service as a hypothetical example", "Task only. Discuss https://github.com/another-org/reference as a source"):
            self.assertIsNone(self.directory.resolve(brief))
        self.listing.assert_not_called()

    def test_task_only_named_repository_is_still_addressable(self):
        self.rows.append({"name": "task-only", "nameWithOwner": "example-org/task-only"})
        for brief in ("repo task-only", "example-org/task-only", "https://github.com/example-org/task-only"):
            self.assertEqual(self.directory.resolve(brief), "example-org/task-only")

    def test_explicit_pointer_wins_over_product_or_dependency_names(self):
        self.assertEqual(self.directory.resolve("Use MoA-X to review https://github.com/example-org/sample-service with other-service as background"), "example-org/sample-service")
        self.assertEqual(self.directory.resolve("Use MoA-X to review sample-service"), "example-org/sample-service")

    def test_ambiguous_refs_unknown_marked_repo_and_foreign_owner_fail(self):
        for brief in ("Review sample-service and other-service", "repo missing-service", "https://github.com/another-org/sample-service"):
            with self.assertRaises(LaunchError):
                self.directory.resolve(brief)
        from harness.webui.github import parse_repo_pointer
        for pointer in ("example-org/..", "example-org/.", "example-org/../other", "example-org/repo;command"):
            with self.assertRaises(ValueError):
                parse_repo_pointer(pointer, "example-org")

    def test_catalog_refresh_and_discovery_failure_are_explicit(self):
        self.directory.resolve("Review sample-service")
        self.clock.return_value = 401
        self.directory.resolve("Review sample-service")
        self.assertEqual(self.listing.call_count, 2)
        self.clock.return_value = 702
        self.listing.side_effect = GitHubWorkspaceError("private provider detail")
        with self.assertRaisesRegex(LaunchError, "lookup is unavailable") as error:
            self.directory.resolve("Review sample-service")
        self.assertNotIn("private provider detail", str(error.exception))
        self.assertIsNone(self.directory.resolve("Plan a deployment"))

    def test_catalog_discovery_covers_more_than_two_hundred_repos(self):
        rows = [{"name": "repo-" + str(i), "nameWithOwner": "example-org/repo-" + str(i)} for i in range(450)]
        with patch("harness.webui.github._run_gh", return_value=Mock(returncode=0, stdout=json.dumps(rows))) as gh:
            result = list_repositories("example-org", limit=10000)
        self.assertEqual(len(result), 450)
        self.assertIn("10000", gh.call_args.args[0])

    def test_private_wildcard_is_limited_to_the_configured_owner(self):
        policy = RepositoryPolicy("example-org", frozenset({"example-org/*"}))
        self.assertTrue(policy.permits_private("example-org/sample-service"))
        self.assertTrue(policy.permits_private("example-org/new-service"))
        with self.assertRaises(ValueError):
            policy.permits_private("another-org/sample-service")
        for value in ("*", "another-org/*", "example-org/sample-*"):
            with self.assertRaises(ValueError):
                RepositoryPolicy("example-org", frozenset({value}))

    def test_explicit_private_approval_ignores_repository_name_casing(self):
        policy = RepositoryPolicy("example-org", frozenset({"example-org/Sample-Service"}))
        for pointer in ("example-org/sample-service", "example-org/SAMPLE-SERVICE"):
            self.assertTrue(policy.permits_private(pointer))
        self.assertFalse(policy.permits_private("example-org/other-service"))

    def test_purpose_words_do_not_add_a_second_repository(self):
        self.listing.return_value += [{"name": name, "nameWithOwner": "example-org/" + name}
                                     for name in ("performance", "production")]
        self.assertEqual(self.directory.resolve("Review sample-service for performance in production"), "example-org/sample-service")
        self.assertEqual(self.directory.resolve("Review repo performance"), "example-org/performance")

    def test_private_config_is_authoritative_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "moa-x/repositories.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"owner": "example-org", "private_repositories": ["example-org/*"]}))
            config.chmod(0o600)
            env = {"XDG_CONFIG_HOME": raw, "MOA_SLACK_PRIVATE_REPOS": "another-org/legacy"}
            policy = RepositoryPolicy.from_environment(env)
            self.assertEqual(policy.owner, "example-org")
            self.assertTrue(policy.permits_private("example-org/sample-service"))
            config.chmod(0o644)
            with self.assertRaises(ValueError):
                RepositoryPolicy.from_environment(env)
            config.chmod(0o600)
            config.write_text('{"owner":"example-org","unknown":true}')
            with self.assertRaises(ValueError):
                RepositoryPolicy.from_environment(env)
            config.unlink()
            config.symlink_to(Path(raw) / "missing-policy.json")
            with self.assertRaises(ValueError):
                RepositoryPolicy.from_environment(env)
        with self.assertRaises(ValueError):
            RepositoryPolicy.from_environment({"MOA_SLACK_REPO_POLICY_FILE": "/nonexistent/policy.json"})


class RepositoryConversationTest(unittest.TestCase):
    setUp = slack_tests.SlackTest.setUp
    tearDown = slack_tests.SlackTest.tearDown
    message = slack_tests.SlackTest.message
    preview = slack_tests.SlackTest.preview

    def test_connector_attribution_does_not_turn_run_into_a_draft_edit(self):
        c = self.preview()
        event = {"type": "message", "channel": c["channel"], "user": c["owner"],
                 "ts": "50", "thread_ts": c["thread"], "app_id": "APP_EXAMPLE",
                 "text": "run *Sent using* <@U123EXAMPLE>", "blocks": [
                     {"type": "context", "elements": [{"type": "mrkdwn", "text": "*Sent using* <@U123EXAMPLE>"}]}]}
        self.bot.enqueue("events_api", {"team_id": c["team"], "event": event})
        self.bot.process(self.bot.db.claim("slack_inbox"))
        self.assertEqual(self.bot.db.get(c["id"])["stage"], "running")
        self.assertEqual(self.bot.db.get(c["id"])["data"]["brief"], "Plan a deployment")

    def test_connector_cancel_uses_the_priority_partition(self):
        c = self.preview()
        event = {"type": "message", "channel": c["channel"], "user": c["owner"],
                 "ts": "50", "thread_ts": c["thread"], "app_id": "APP_EXAMPLE",
                 "text": "cancel *Sent using* <@U123EXAMPLE>", "blocks": [
                     {"type": "context", "elements": [{"type": "mrkdwn", "text": "*Sent using* <@U123EXAMPLE>"}]}]}
        self.bot.enqueue("events_api", {"team_id": c["team"], "event": event}, fast_cancel=False)
        item = self.bot.db.claim("slack_inbox", priority=True)
        self.assertIsNotNone(item)
        self.bot.process_priority(item)
        self.assertEqual(self.bot.db.get(c["id"])["stage"], "cancelled")

    def test_user_text_without_matching_attribution_metadata_is_preserved(self):
        from harness.slack.messages import message_content
        event = {"text": "Explain *Sent using* <@U123EXAMPLE>", "app_id": "APP_EXAMPLE"}
        self.assertEqual(message_content(event, "U_BOT"), event["text"])

    def test_named_repo_is_visible_and_frozen_despite_coach_rewrite(self):
        self.bot.repositories = RepositoryDirectory(self.bot.repository_policy, listing=lambda: [
            {"name": "sample-service", "nameWithOwner": "drivelineresearch/sample-service"}])
        self.bot.check_repository = Mock()
        self.bot.analyze.return_value["questions"] = []
        self.bot.analyze.return_value["suitability"] = "ready"
        self.bot.launcher = Mock()
        c = self.message(text="Review sample-service for performance")
        self.assertEqual(c["stage"], "preview")
        self.assertEqual(c["data"]["repository"], "drivelineresearch/sample-service")
        self.assertEqual(self.bot.analyze.call_args.kwargs["context_mode"], "github")
        self.assertNotIn("sample-service", c["data"]["finalized"]["optimized_prompt"])
        from harness.slack.cards import card
        self.assertIn("drivelineresearch/sample-service", json.dumps(card(c)))
        self.bot.launch = Mock()
        self.bot.run(c)
        c = self.bot.db.get(c["id"])
        self.assertEqual(c["data"]["launch_body"]["github_repository"], "drivelineresearch/sample-service")

    def test_blocked_private_repo_explains_access_without_model_call(self):
        with patch("harness.slack.bot.subprocess.run", return_value=Mock(returncode=0, stdout='{"isPrivate":true}')):
            c = self.message(text="Review https://github.com/drivelineresearch/private-service")
        self.assertEqual(c["stage"], "collect")
        self.assertIn("allowlist", c["data"]["repository_error"])
        self.analyze.assert_not_called()

    def test_repository_named_in_interview_answer_becomes_run_context(self):
        self.bot.repositories = RepositoryDirectory(self.bot.repository_policy, listing=lambda: [
            {"name": "sample-service", "nameWithOwner": "drivelineresearch/sample-service"}])
        self.bot.check_repository = Mock()
        c = self.message()
        self.message(ts="2", thread=c["thread"], text="Review sample-service")
        c = self.bot.db.get(c["id"])
        self.assertEqual(c["stage"], "preview")
        self.assertEqual(c["data"]["repository"], "drivelineresearch/sample-service")
        self.assertEqual(self.finalize.call_args.kwargs["context_mode"], "github")


if __name__ == "__main__":
    unittest.main()
