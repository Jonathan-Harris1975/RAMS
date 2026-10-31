"""Bounded, secret-free incident report tests."""
from unittest.mock import Mock

import pytest

from repo_mgmt.recovery_report import recovery_summary


def test_summary_filters_repository_and_reports_attempts():
    d1 = Mock()
    d1.query.side_effect = [[
        {"incident_key": "a" * 64, "attempt_key": "b" * 64,
         "head_sha": "c" * 40, "state": "repairing",
         "attempts": 3, "lease_until": 100, "updated_at": 50,
         "owner": "private-identity"},
        {"incident_key": "d" * 64, "attempt_key": "e" * 64,
         "head_sha": "f" * 40, "state": "resolved",
         "attempts": 1, "lease_until": 0, "updated_at": 40},
    ], [{"event": "claim_acquired", "total": 3}]]
    result = recovery_summary(d1, "Jonathan-Harris1975/RAMS", now=101)
    assert result["active"] == 1
    assert result["resolved"] == 1
    assert result["retry_attempts"] == 2
    assert result["expired_leases"] == 1
    assert result["retry_exhausted"] == 1
    assert result["event_totals"] == {"claim_acquired": 3}
    assert "owner" not in result["incidents"][0]
    sql, params = d1.query.call_args_list[0].args
    assert "WHERE repository=?" in sql
    assert params == ["Jonathan-Harris1975/RAMS", 50]
    assert d1.query.call_args_list[1].args[1] == ["Jonathan-Harris1975/RAMS"]


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
    d1.query.return_value = [{"incident_key": "a" * 64, "attempt_key": "b" * 64,
                              "head_sha": "c" * 40, "state": "repairing",
                              "lease_until": 100, "updated_at": 50, "attempts": attempts}]
    with pytest.raises(ValueError, match="retry count"):
        recovery_summary(d1, "owner/RAMS")


@pytest.mark.parametrize("events", [[{"event": "claim_acquired", "total": True}],
                                    [{"event": "bad event", "total": 1}],
                                    [{"event": "claim_acquired", "total": -1}]])
def test_malformed_event_totals_fail_closed(events):
    d1 = Mock()
    d1.query.side_effect = [[{"incident_key": "a" * 64, "attempt_key": "b" * 64,
                              "head_sha": "c" * 40, "state": "repairing",
                              "lease_until": 100, "updated_at": 50, "attempts": 1}], events]
    with pytest.raises(ValueError, match="invalid D1 recovery event"):
        recovery_summary(d1, "owner/RAMS")


@pytest.mark.parametrize("field,value", [
    ("incident_key", "not-a-key"), ("attempt_key", "not-a-key"),
    ("head_sha", "invalid"), ("state", "invented"),
    ("lease_until", -1), ("updated_at", True),
])
def test_invalid_incident_metadata_is_never_reported(field, value):
    d1 = Mock()
    row = {"incident_key": "a" * 64, "attempt_key": "b" * 64,
           "head_sha": "c" * 40, "state": "repairing",
           "attempts": 1, "lease_until": 100, "updated_at": 50}
    row[field] = value
    d1.query.side_effect = [[row], []]
    with pytest.raises(ValueError, match="invalid D1 incident"):
        recovery_summary(d1, "owner/RAMS")


def test_active_lease_is_not_reported_as_expired():
    d1 = Mock()
    d1.query.side_effect = [[{"incident_key": "a" * 64,
        "attempt_key": "b" * 64, "head_sha": "c" * 40,
        "state": "repairing", "attempts": 1,
        "lease_until": 500, "updated_at": 100}], []]
    result = recovery_summary(d1, "owner/RAMS", now=200)
    assert result["expired_leases"] == 0
    assert result["retry_exhausted"] == 0


@pytest.mark.parametrize("now", [-1, True, "200"])
def test_invalid_report_timestamp_fails_before_query(now):
    d1 = Mock()
    with pytest.raises(ValueError, match="timestamp"):
        recovery_summary(d1, "owner/RAMS", now=now)
    d1.query.assert_not_called()


def test_accepted_handoff_is_active_but_not_an_expired_worker():
    d1 = Mock()
    d1.query.side_effect = [[{
        "incident_key": "a" * 64, "attempt_key": "b" * 64,
        "head_sha": "c" * 40, "state": "handed_off",
        "attempts": 2, "lease_until": 100, "updated_at": 99,
    }], [{"event": "repair_handoff_accepted", "total": 1}]]
    report = recovery_summary(d1, "owner/RAMS", now=500)
    assert report["active"] == 1
    assert report["handed_off"] == 1
    assert report["expired_leases"] == 0
    assert report["retry_exhausted"] == 0
