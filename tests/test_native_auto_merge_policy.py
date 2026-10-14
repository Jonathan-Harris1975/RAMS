from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


def test_admitted_pr_is_left_for_mergify_without_enabling_native_auto_merge(monkeypatch) -> None:
    graphql_calls = []
    monkeypatch.setattr(native_auto_merge, "merge_request", lambda _node: None)
    monkeypatch.setattr(native_auto_merge, "request", lambda *_args, **_kwargs: _pr(["autonomy:admitted"]))
    monkeypatch.setattr(native_auto_merge, "admission_check", lambda *_args: (True, True))
    monkeypatch.setattr(native_auto_merge, "graphql", lambda query, *_args: graphql_calls.append(query))

    native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": [{"name": "autonomy:admitted"}]})

    assert graphql_calls == []


def test_existing_native_request_for_admitted_pr_is_disabled(monkeypatch) -> None:
    graphql_calls = []
    monkeypatch.setattr(native_auto_merge, "merge_request", lambda _node: {"enabledAt": "now"})
    monkeypatch.setattr(native_auto_merge, "request", lambda *_args, **_kwargs: _pr(["autonomy:admitted"]))
    monkeypatch.setattr(native_auto_merge, "admission_check", lambda *_args: (True, True))
    monkeypatch.setattr(native_auto_merge, "graphql", lambda query, *_args: graphql_calls.append(query))

    native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": [{"name": "autonomy:admitted"}]})

    assert len(graphql_calls) == 1
    assert "disablePullRequestAutoMerge" in graphql_calls[0]


def test_active_native_request_with_exact_admission_evidence_is_disabled(monkeypatch) -> None:
    graphql_calls = []
    monkeypatch.setattr(native_auto_merge, "merge_request", lambda _node: {"enabledAt": "now"})
    monkeypatch.setattr(native_auto_merge, "request", lambda *_args, **_kwargs: _pr([]))
    monkeypatch.setattr(native_auto_merge, "admission_check", lambda *_args: (False, True))
    monkeypatch.setattr(native_auto_merge, "graphql", lambda query, *_args: graphql_calls.append(query))

    native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": []})

    assert len(graphql_calls) == 1
    assert "disablePullRequestAutoMerge" in graphql_calls[0]


def test_unrelated_pr_without_native_request_is_ignored(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(native_auto_merge, "merge_request", lambda _node: None)
    monkeypatch.setattr(native_auto_merge, "request", lambda *args, **kwargs: calls.append((args, kwargs)))

    native_auto_merge.reconcile({"number": 12, "node_id": "PR_node", "labels": []})

    assert calls == []
