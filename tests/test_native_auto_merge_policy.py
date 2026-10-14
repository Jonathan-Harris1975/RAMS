from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "native_auto_merge.py"
SPEC = importlib.util.spec_from_file_location("native_auto_merge", SCRIPT)
assert SPEC and SPEC.loader
native_auto_merge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_auto_merge)


def _pr(labels: list[str]) -> dict:
    return {
        "number": 12,
        "state": "open",
        "draft": False,
        "base": {"ref": "main"},
        "head": {"sha": "a" * 40},
        "labels": [{"name": label} for label in labels],
    }


class NativeAutoMergePolicyTests(unittest.TestCase):
    def test_admitted_pr_is_left_for_mergify_without_enabling_native_auto_merge(self) -> None:
        with (
            patch.object(native_auto_merge, "merge_request", return_value=None),
            patch.object(native_auto_merge, "request", return_value=_pr(["autonomy:admitted"])),
            patch.object(native_auto_merge, "admission_check", return_value=(True, True)),
            patch.object(native_auto_merge, "graphql") as graphql,
        ):
            native_auto_merge.reconcile({
                "number": 12,
                "node_id": "PR_node",
                "labels": [{"name": "autonomy:admitted"}],
            })
        graphql.assert_not_called()

    def test_existing_native_request_for_admitted_pr_is_disabled(self) -> None:
        with (
            patch.object(native_auto_merge, "merge_request", return_value={"enabledAt": "now"}),
            patch.object(native_auto_merge, "request", return_value=_pr(["autonomy:admitted"])),
            patch.object(native_auto_merge, "admission_check", return_value=(True, True)),
            patch.object(native_auto_merge, "graphql") as graphql,
        ):
            native_auto_merge.reconcile({
                "number": 12,
                "node_id": "PR_node",
                "labels": [{"name": "autonomy:admitted"}],
            })
        graphql.assert_called_once()
        self.assertIn("disablePullRequestAutoMerge", graphql.call_args.args[0])

    def test_native_request_with_exact_admission_evidence_is_disabled(self) -> None:
        with (
            patch.object(native_auto_merge, "merge_request", return_value={"enabledAt": "now"}),
            patch.object(native_auto_merge, "request", return_value=_pr([])),
            patch.object(native_auto_merge, "admission_check", return_value=(False, True)),
            patch.object(native_auto_merge, "graphql") as graphql,
        ):
            native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": []})
        graphql.assert_called_once()
        self.assertIn("disablePullRequestAutoMerge", graphql.call_args.args[0])

    def test_unrelated_pr_without_native_request_is_ignored(self) -> None:
        with (
            patch.object(native_auto_merge, "merge_request", return_value=None),
            patch.object(native_auto_merge, "request") as request,
        ):
            native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": []})
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
