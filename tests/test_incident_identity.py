"""Regression tests for deterministic repair incident identity."""
import pytest

from repo_mgmt.incident_identity import IncidentIdentity


def identity(**overrides):
    values = dict(
        repository="Jonathan-Harris1975/RAMS",
        category="dependency_audit",
        source="github_ci",
        fingerprint="pypdf:CVE-2026-119203",
        head_sha="a" * 40,
    )
    values.update(overrides)
    return IncidentIdentity(**values)


def test_same_failure_across_heads_reuses_incident_not_attempt():
    first = identity()
    second = identity(head_sha="b" * 40)
    assert first.incident_key == second.incident_key
    assert first.attempt_key != second.attempt_key


def test_repository_and_fingerprint_isolation():
    first = identity()
    assert first.incident_key != identity(repository="Jonathan-Harris1975/AIMS").incident_key
    assert first.incident_key != identity(fingerprint="pypdf:CVE-OTHER").incident_key


def test_sha_case_does_not_change_attempt():
    assert identity(head_sha="A" * 40).attempt_key == identity(head_sha="a" * 40).attempt_key


@pytest.mark.parametrize("changes", [
    {"repository": "../RAMS"},
    {"category": "Invalid category"},
    {"source": "ci!"},
    {"head_sha": "abc"},
    {"fingerprint": ""},
    {"fingerprint": "x" * 257},
])
def test_invalid_identity_rejected(changes):
    with pytest.raises(ValueError):
        identity(**changes)
