"""Bounded, secret-free incident report tests."""
from unittest.mock import Mock

import pytest

from repo_mgmt.recovery_report import recovery_summary


def test_summary_filters_repository_and_reports_attempts():
    d1 = Mock()
    d1.query.return_value = [
        {"incident_key": "a" * 64, "attempt_key": "b" * 64,
         "head_sha": "c" * 40, "state": "repairing",
         "attempts": 3, "lease_until": 100, "updated_at": 50,
         "owner": "private-identity"},
        {"incident_key": "d" * 64, "state": "resolved",
         "attempts": 1, "updated_at": 40},
    ]
    result = recovery_summary(d1, "Jonathan-Harris1975/RAMS")
    assert result["active"] == 1
    assert result["resolved"] == 1
    assert result["retry_attempts"] == 2
    assert "owner" not in result["incidents"][0]
    sql, params = d1.query.call_args.args
    assert "WHERE repository=?" in sql
    assert params == ["Jonathan-Harris1975/RAMS", 50]


@pytest.mark.parametrize("repository", ["../RAMS", "owner/../repo", "owner", ""])
def test_invalid_repository_never_queries(repository):
    d1 = Mock()
    with pytest.raises(ValueError):
        recovery_summary(d1, repository)
    d1.query.assert_not_called()


@pytest.mark.parametrize("limit", [0, 101, True, -1])
def test_invalid_limit_never_queries(limit):
    d1 = Mock()
    with pytest.raises(ValueError):
        recovery_summary(d1, "owner/RAMS", limit=limit)
    d1.query.assert_not_called()


@pytest.mark.parametrize("rows", [None, "invalid", [None], [{}] * 101])
def test_malformed_or_oversized_results_fail_closed(rows):
    d1 = Mock()
    d1.query.return_value = rows
    with pytest.raises(ValueError, match="invalid D1"):
        recovery_summary(d1, "owner/RAMS")


@pytest.mark.parametrize("attempts", [None, "3", True, -1, 1000001, 1.5])
def test_malformed_retry_counts_fail_closed(attempts):
    d1 = Mock()
    d1.query.return_value = [{"state": "repairing", "attempts": attempts}]
    with pytest.raises(ValueError, match="retry count"):
        recovery_summary(d1, "owner/RAMS")
