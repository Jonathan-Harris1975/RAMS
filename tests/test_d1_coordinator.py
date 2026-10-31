"""D1 coordinator HTTP and fail-closed contract tests."""
import io
import json
from unittest.mock import patch

import pytest

from repo_mgmt.d1_coordinator import D1Coordinator, D1CoordinationError


ACCOUNT = "a" * 32
DATABASE = "759c49e9-0d72-4967-8a32-1e8b6dd6a1f8"


def test_query_uses_parameterised_post_and_parses_result():
    client = D1Coordinator(ACCOUNT, DATABASE, "scoped-token")
    result = {"success": True, "result": [{"success": True, "results": [{"ok": 1}]}]}
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as send:
        assert client.query("SELECT ? AS ok", ["1"]) == [{"ok": 1}]
    request = send.call_args.args[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data)["params"] == ["1"]
    assert request.get_header("Authorization") == "Bearer scoped-token"


def test_query_rejects_failure_without_exposing_token():
    client = D1Coordinator(ACCOUNT, DATABASE, "private-token")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"success":false,"result":[]}')):
        with pytest.raises(D1CoordinationError) as error:
            client.query("SELECT 1")
    assert "private-token" not in str(error.value)


def test_invalid_identifiers_and_multistatement_rejected():
    with pytest.raises(ValueError):
        D1Coordinator("invalid", DATABASE, "token")
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with pytest.raises(ValueError):
        client.query("DELETE FROM incidents; DELETE FROM incident_events")


def test_claim_is_single_atomic_upsert_and_fenced_release():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=[{"incident_key": "a" * 64}]) as query:
        assert client.acquire("a" * 64, "b" * 64, "owner/RAMS", "c" * 40,
                              "worker-1", now=100)
    sql, params = query.call_args.args
    assert "ON CONFLICT(incident_key) DO UPDATE" in sql
    assert "repository=excluded.repository" in sql
    assert "RETURNING incident_key" in sql
    assert "incidents.attempts<?" in sql
    assert params[-2:] == [100, 3]

    with patch.object(client, "query", return_value=[]) as query:
        assert not client.finish("a" * 64, "b" * 64, "stale-owner",
                                 resolved=True, now=200)
    assert "AND owner=? AND (lease_until>? OR state='handed_off')" in query.call_args.args[0]


def test_claim_rejects_invalid_identity():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with pytest.raises(ValueError):
        client.acquire("../escape", "b" * 64, "owner/RAMS", "c" * 40,
                       "worker", now=100)


def test_lease_renewal_requires_exact_owner_and_live_lease():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=[{"incident_key": "a" * 64}]) as query:
        assert client.renew("a" * 64, "b" * 64, "worker", now=100,
                            lease_seconds=60)
    assert "AND owner=? AND lease_until>?" in query.call_args.args[0]
    assert query.call_args.args[1] == [160, 100, "a" * 64,
                                       "b" * 64, "worker", 100]
    with patch.object(client, "query", return_value=[]):
        assert not client.renew("a" * 64, "b" * 64, "stale", now=101)


def test_durable_audit_event_is_parameterised():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=[{"id": 12}]) as query:
        assert client.record_event("a" * 64, "b" * 64, "worker",
                                   "claim_acquired", now=123)
    sql, params = query.call_args.args
    assert "INSERT INTO incident_events" in sql
    assert params == ["a" * 64, "b" * 64, "claim_acquired", "worker", 123]
    with pytest.raises(ValueError):
        client.record_event("a" * 64, "b" * 64, "worker",
                            "'); DELETE FROM incidents;--", now=123)


@pytest.mark.parametrize("owner", ["", " ", "worker\\nspoof", "x" * 129])
def test_lease_operations_reject_invalid_owner(owner):
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query") as query:
        with pytest.raises(ValueError):
            client.acquire("a" * 64, "b" * 64, "owner/RAMS",
                           "c" * 40, owner, now=100)
        with pytest.raises(ValueError):
            client.renew("a" * 64, "b" * 64, owner, now=100)
        with pytest.raises(ValueError):
            client.finish("a" * 64, "b" * 64, owner, resolved=False, now=100)
        with pytest.raises(ValueError):
            client.record_event("a" * 64, "b" * 64, owner,
                                "lease_renewed", now=100)
        query.assert_not_called()


@pytest.mark.parametrize("payload", [
    [], None, "unexpected", {"success": True, "result": [None]},
    {"success": True, "result": [{"success": True, "results": [None]}]},
    {"success": True, "result": [{"success": True, "results": "invalid"}]},
])
def test_query_rejects_malformed_response_shapes(payload):
    client = D1Coordinator(ACCOUNT, DATABASE, "private-token")
    body = json.dumps(payload).encode()
    with patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
        with pytest.raises(D1CoordinationError):
            client.query("SELECT 1")


@pytest.mark.parametrize("operation", ["acquire", "renew", "finish"])
@pytest.mark.parametrize("rows", [[{"incident_key": "f" * 64}],
                                   [{"incident_key": "a" * 64}] * 2])
def test_mutations_reject_wrong_or_ambiguous_acknowledgement(operation, rows):
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=rows):
        with pytest.raises(D1CoordinationError):
            if operation == "acquire":
                client.acquire("a" * 64, "b" * 64, "owner/RAMS", "c" * 40,
                               "worker", now=100)
            elif operation == "renew":
                client.renew("a" * 64, "b" * 64, "worker", now=100)
            else:
                client.finish("a" * 64, "b" * 64, "worker", resolved=False, now=100)


def test_accepted_handoff_is_atomically_fenced_against_redelivery():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=[{"incident_key": "a" * 64}]) as query:
        assert client.mark_handoff("a" * 64, "b" * 64, "worker", now=100)
    sql, params = query.call_args.args
    assert "state='handed_off'" in sql
    assert "AND owner=? AND lease_until>?" in sql
    assert params == [100, "a" * 64, "b" * 64, "worker", 100]
    with patch.object(client, "query", return_value=[]) as query:
        assert not client.mark_handoff("a" * 64, "b" * 64, "stale", now=101)
    with patch.object(client, "query", return_value=[]) as query:
        assert not client.acquire("a" * 64, "b" * 64, "owner/RAMS", "c" * 40,
                                  "worker-2", now=500)
    assert "incidents.state IN ('resolved', 'handed_off')" in query.call_args.args[0]


def test_terminal_failure_reconciliation_is_fenced_to_exact_handoff():
    client = D1Coordinator(ACCOUNT, DATABASE, "token")
    with patch.object(client, "query", return_value=[{"incident_key": "a" * 64}]) as query:
        assert client.reconcile_failed_handoff("a" * 64, "b" * 64,
                                               "worker", now=500)
    sql, params = query.call_args.args
    assert "state='handed_off'" in sql
    assert "AND owner=?" in sql
    assert params == [500, "a" * 64, "b" * 64, "worker"]
    with patch.object(client, "query", return_value=[]):
        assert not client.reconcile_failed_handoff("a" * 64, "b" * 64,
                                                    "stale", now=501)
