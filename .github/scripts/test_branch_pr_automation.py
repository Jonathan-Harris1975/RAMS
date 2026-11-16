"""Regression tests for PR-only branch automation.

The branch controller may create/reuse PRs and recover missed branch signals,
but it must never review, approve, queue, auto-merge or merge them. GitHub native auto-merge is the merge authority after trusted admission.
"""
from __future__ import annotations

import inspect
import io
import os
import urllib.error
import unittest
from unittest.mock import patch

os.environ.setdefault("GH_TOKEN", "unit-test")
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")

import branch_pr_automation as m  # noqa: E402


class BranchPrAutomationTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            "REPO": "owner/repo",
            "DEFAULT_BRANCH": "main",
            "REPAIR_APP_LOGIN": "repair[bot]",
        }.items():
            self.enterContext(patch.object(m, name, value))
        self.enterContext(patch.object(m, "log"))

    def pr(self, number=7, branch="codex/change", author="repair[bot]", repo="owner/repo"):
        return {
            "number": number,
            "user": {"login": author},
            "head": {"ref": branch, "sha": "a" * 40, "repo": {"full_name": repo}},
            "base": {"ref": "main"},
            "merged": False,
            "merged_at": None,
        }

    def test_approved_branch_namespaces_include_codex(self):
        for branch in [
            "fix/bug",
            "feat/change",
            "chore/cleanup",
            "ci/check",
            "work/task",
            "codex/implementation",
        ]:
            self.assertTrue(m.allowed_branch(branch), branch)

    def test_unmanaged_and_unsafe_branches_are_rejected(self):
        for branch in [
            "main",
            "autonomy/repair-1",
            "renovate/pkg",
            "dependabot/npm_and_yarn/x",
            "merge-queue",
            "codex/",
            "codex/a..b",
            "codex/a//b",
            "random/change",
        ]:
            self.assertFalse(m.allowed_branch(branch), branch)

    def test_exact_open_pr_requires_same_repo_source_and_target(self):
        wanted = self.pr()
        others = [
            self.pr(number=8, repo="fork/repo"),
            {**self.pr(number=9), "base": {"ref": "other"}},
            self.pr(number=10, branch="fix/other"),
        ]
        self.assertEqual(m.exact_open_pr([*others, wanted], "codex/change"), wanted)

    def test_duplicate_open_prs_fail_closed(self):
        with self.assertRaises(RuntimeError):
            m.exact_open_pr([self.pr(7), self.pr(8)], "codex/change")

    def test_existing_human_pr_is_reused_without_adoption(self):
        existing = self.pr(author="human")
        with (
            patch.object(m, "list_open_prs", return_value=[existing]),
            patch.object(m, "add_label") as add_label,
            patch.object(m, "post") as post,
        ):
            m.create_or_reuse_pr(("codex/change", "a" * 40))
        add_label.assert_not_called()
        post.assert_not_called()

    def test_existing_repair_app_pr_gets_management_label(self):
        existing = self.pr()
        with (
            patch.object(m, "list_open_prs", return_value=[existing]),
            patch.object(m, "add_label") as add_label,
            patch.object(m, "post") as post,
        ):
            m.create_or_reuse_pr(("codex/change", "a" * 40))
        add_label.assert_called_once_with(7)
        post.assert_not_called()

    def test_new_candidate_creates_pr_and_labels_it(self):
        with (
            patch.object(m, "list_open_prs", return_value=[]),
            patch.object(m, "pr_metadata", return_value=("Title", "Body")),
            patch.object(m, "post", return_value={"number": 12}) as post,
            patch.object(m, "add_label") as add_label,
        ):
            m.create_or_reuse_pr(("codex/change", "a" * 40))
        post.assert_called_once()
        payload = post.call_args.args[1]
        self.assertEqual(payload["head"], "codex/change")
        self.assertEqual(payload["base"], "main")
        add_label.assert_called_once_with(12)

    def test_validation_race_reuses_existing_pr(self):
        existing = self.pr()
        with (
            patch.object(m, "list_open_prs", side_effect=[[], [existing]]),
            patch.object(m, "pr_metadata", return_value=("Title", "Body")),
            patch.object(m, "post", side_effect=m.ApiError(422, "already exists")),
            patch.object(m, "add_label") as add_label,
        ):
            m.create_or_reuse_pr(("codex/change", "a" * 40))
        add_label.assert_called_once_with(7)

    def test_recovery_does_not_recreate_deliberately_closed_pr(self):
        branch = {"name": "codex/change", "commit": {"sha": "a" * 40}}
        closed = self.pr()
        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
            patch.object(m, "get", side_effect=[[branch], [closed]]) as get,
            patch.object(m, "list_open_prs", return_value=[]),
            patch.object(m, "create_or_reuse_pr") as create,
        ):
            m.recover_branch_signals()
        create.assert_not_called()
        self.assertEqual(get.call_count, 2)

    def test_transient_compare_http_500_retries_then_succeeds(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return b'{"ahead_by": 1}'

        error = urllib.error.HTTPError("https://api.github.com/test", 500, "Server Error", {}, io.BytesIO(b"comparison temporarily unavailable"))
        with (
            patch.object(m.urllib.request, "urlopen", side_effect=[error, Response()]) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            response = m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(response["ahead_by"], 1)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_transient_rate_limit_honors_bounded_retry_after(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return b'{"ahead_by": 1}'

        error = urllib.error.HTTPError(
            "https://api.github.com/test", 429, "Too Many Requests",
            {"Retry-After": "90", "X-RateLimit-Remaining": "1"},
            io.BytesIO(b"rate limited"),
        )
        with (
            patch.object(m.urllib.request, "urlopen", side_effect=[error, Response()]) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            response = m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(response["ahead_by"], 1)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(60)

    def test_timeout_retries_then_succeeds(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return b'{"ahead_by": 1}'

        with (
            patch.object(m.urllib.request, "urlopen", side_effect=[TimeoutError("read timed out"), Response()]) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            response = m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(response["ahead_by"], 1)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_transient_transport_error_retries_then_succeeds(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self):
                return b'{"ahead_by": 1}'

        with (
            patch.object(m.urllib.request, "urlopen", side_effect=[urllib.error.URLError("temporary connection failure"), Response()]) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            response = m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(response["ahead_by"], 1)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_primary_rate_limit_does_not_spin(self):
        err = urllib.error.HTTPError("https://api.github.com/test", 429, "Too Many Requests", {"X-RateLimit-Remaining":"0"}, io.BytesIO(b"rate limited"))
        with patch.object(m.urllib.request, "urlopen", side_effect=err) as opened, patch.object(m.time, "sleep") as sleep:
            with self.assertRaises(m.ApiError) as caught:
                m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(opened.call_count, 1)
        sleep.assert_not_called()

    def test_exhausted_transport_errors_use_api_error_contract(self):
        errors = [urllib.error.URLError("temporary connection failure") for _ in range(4)]
        with patch.object(m.urllib.request, "urlopen", side_effect=errors) as opened, patch.object(m.time, "sleep") as sleep:
            with self.assertRaises(m.ApiError) as caught:
                m.get("/repos/owner/repo/compare/main...fix/change")
        self.assertEqual(caught.exception.status, 0)
        self.assertEqual(opened.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_bad_get_response_not_retried(self):
        error = urllib.error.HTTPError("https://api.github.com/test", 404, "Not Found", {}, io.BytesIO(b"not found"))
        with (
            patch.object(m.urllib.request, "urlopen", side_effect=error) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            with self.assertRaises(m.ApiError) as raised:
                m.get("/repos/owner/repo/branches/absent")
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_mutating_post_is_never_retried(self):
        error = urllib.error.HTTPError("https://api.github.com/test", 500, "Server Error", {}, io.BytesIO(b"failed"))
        with (
            patch.object(m.urllib.request, "urlopen", side_effect=error) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            with self.assertRaises(m.ApiError):
                m.post("/repos/owner/repo/pulls", {"title": "test"})
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_repeated_transient_errors_are_bounded(self):
        def error():
            return urllib.error.HTTPError("https://api.github.com/test", 503, "Unavailable", {}, io.BytesIO(b"temporary"))
        with (
            patch.object(m.urllib.request, "urlopen", side_effect=[error() for _ in range(4)]) as urlopen,
            patch.object(m.time, "sleep") as sleep,
        ):
            with self.assertRaises(m.ApiError) as raised:
                m.get("/repos/owner/repo/compare/main...fix/branch")
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_controller_contains_no_review_or_merge_authority(self):
        source = inspect.getsource(m)
        for forbidden in [
            "enablePullRequestAutoMerge",
            "disablePullRequestAutoMerge",
            "gh\", \"pr\", \"merge",
            "submitPullRequestReview",
            "/reviews",
        ]:
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
