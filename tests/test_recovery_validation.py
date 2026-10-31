"""Release admission cannot be inferred from stale or partial CI evidence."""
import pytest
from repo_mgmt.recovery_validation import REQUIRED, verified_release_checks

SHA = "a" * 40


def checks():
    return [{"name": name, "head_sha": SHA, "status": "completed",
             "conclusion": "success"} for name in sorted(REQUIRED)]


def test_all_exact_sha_checks_are_required():
    assert set(verified_release_checks(SHA, checks())) == REQUIRED


@pytest.mark.parametrize("mutate", [
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(head_sha="b" * 40),
    lambda rows: rows[0].update(conclusion="failure"),
    lambda rows: rows[0].update(status="in_progress"),
    lambda rows: rows.append(rows[0].copy()),
])
def test_stale_red_missing_pending_or_duplicate_checks_rejected(mutate):
    rows = checks()
    mutate(rows)
    with pytest.raises(ValueError):
        verified_release_checks(SHA, rows)


def test_untrusted_nonlist_payload_rejected():
    with pytest.raises(ValueError):
        verified_release_checks(SHA, {"CI": "success"})
