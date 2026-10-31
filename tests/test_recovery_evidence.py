"""Recovery evidence storage contract tests."""
import json
from unittest.mock import Mock

import pytest

from repo_mgmt.recovery_evidence import evidence_key, write_evidence


def test_scoped_key_and_receipt():
    sha = "a" * 40
    key = "b" * 64
    client = Mock()
    path = write_evidence(
        client, repo="RAMS", kind="receipts", incident_key=key,
        head_sha=sha, record={"state": "validated"},
    )
    assert path.startswith(f"autonomous-recovery/RAMS/receipts/{key}/{sha}-")\n    assert path.endswith(".json")
    call = client.put_object.call_args
    assert call.args[0] == "hive-repositories"
    assert call.args[1] == path
    assert call.kwargs["content_type"] == "application/json"
    payload = json.loads(call.args[2])
    assert payload["record"] == {"state": "validated"}
    assert payload["head_sha"] == sha


@pytest.mark.parametrize("repo,kind", [
    ("../RAMS", "audit"), ("RAMS", "../audit"), ("..", "receipts"),
    ("RAMS", "unknown"),
])
def test_path_traversal_rejected(repo, kind):
    with pytest.raises(ValueError):
        evidence_key(repo, kind, "b" * 64, "a" * 40)


def test_large_and_nonfinite_payloads_rejected():
    client = Mock()
    args = dict(r2_client=client, repo="RAMS", kind="audit",
                incident_key="b" * 64, head_sha="a" * 40)
    with pytest.raises(ValueError):
        write_evidence(**args, record={"payload": "x" * 70000})
    with pytest.raises(ValueError):
        write_evidence(**args, record={"nan": float("nan")})
    client.put_object.assert_not_called()


def test_upload_failure_propagates():
    client = Mock()
    client.put_object.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError, match="upload failed"):
        write_evidence(client, repo="RAMS", kind="audit",
                       incident_key="b" * 64, head_sha="a" * 40,
                       record={"state": "failed"})


def test_each_receipt_has_unique_immutable_path():
    client = Mock()
    args = dict(r2_client=client, repo="RAMS", kind="audit",
                incident_key="b" * 64, head_sha="a" * 40,
                record={"state": "retry"})
    first = write_evidence(**args)
    second = write_evidence(**args)
    assert first != second
    assert first.endswith(".json") and second.endswith(".json")
