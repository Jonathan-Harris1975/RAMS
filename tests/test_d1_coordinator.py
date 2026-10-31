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
