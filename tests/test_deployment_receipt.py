"""No autonomous incident may close on CI or unverified deployment metadata."""
import pytest

from repo_mgmt.deployment_receipt import verified_deployment


SHA = "a" * 40
GOOD = {"sha": SHA, "environment": "production", "status": "success",
        "provider": "koyeb", "deployment_id": "deployment-123"}


def test_verified_exact_sha_produces_bounded_receipt():
    assert verified_deployment(SHA, GOOD) == GOOD


@pytest.mark.parametrize("override", [
    {"sha": "b" * 40}, {"sha": "short"}, {"environment": "staging"},
    {"status": "pending"}, {"status": "failure"}, {"provider": ""},
    {"deployment_id": "../unsafe"}, {"deployment_id": None},
])
def test_unverified_production_release_never_produces_receipt(override):
    with pytest.raises(ValueError):
        verified_deployment(SHA, {**GOOD, **override})


def test_missing_deployment_is_not_success():
    with pytest.raises(ValueError):
        verified_deployment(SHA, None)
