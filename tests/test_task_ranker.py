"""Tests for repo_mgmt.task_ranker — scoring, sorting, and queue capping."""

from __future__ import annotations

from repo_mgmt.task_ranker import rank


def _issue(
    task_id: str, severity: str, confidence: float, classification: str = "code_fix"
) -> dict:
    return {
        "taskId": task_id,
        "severity": severity,
        "confidence": confidence,
        "classification": classification,
    }


def test_score_uses_severity_times_confidence() -> None:
    issues = [
        _issue("a", "high", 0.5),  # 3 * 0.5 = 1.5
        _issue("b", "critical", 1.0),  # 4 * 1.0 = 4.0
        _issue("c", "medium", 1.0),  # 2 * 1.0 = 2.0
    ]
    result = rank(issues, max_code_fix=10)
    ids = [i["taskId"] for i in result.code_fix]
    assert ids == ["b", "c", "a"], f"Expected sorted by score desc, got {ids}"


def test_cap_at_max_code_fix() -> None:
    issues = [_issue(f"t{i}", "medium", 1.0) for i in range(10)]
    result = rank(issues, max_code_fix=3)
    assert len(result.code_fix) == 3


def test_future_guidance_not_capped() -> None:
    issues = [_issue(f"t{i}", "low", 1.0, "future_guidance") for i in range(10)]
    result = rank(issues, max_code_fix=3)
    assert len(result.future_guidance) == 10
    assert len(result.code_fix) == 0


def test_manual_review_queue() -> None:
    issues = [_issue("m1", "high", 1.0, "manual_review")]
    result = rank(issues)
    assert len(result.manual_review) == 1
    assert len(result.code_fix) == 0


def test_mixed_queues() -> None:
    issues = [
        _issue("cf", "high", 1.0, "code_fix"),
        _issue("fg", "low", 1.0, "future_guidance"),
        _issue("mr", "medium", 1.0, "manual_review"),
    ]
    result = rank(issues, max_code_fix=5)
    assert len(result.code_fix) == 1
    assert len(result.future_guidance) == 1
    assert len(result.manual_review) == 1


def test_unknown_severity_defaults_to_low() -> None:
    issues = [_issue("x", "unknown_sev", 2.0)]
    result = rank(issues, max_code_fix=5)
    # score = 1 * 2.0 = 2.0 — should not crash
    assert len(result.code_fix) == 1
