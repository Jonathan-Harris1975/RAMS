"""Release admission cannot be inferred from stale or partial CI evidence."""
import pytest
from repo_mgmt.recovery_validation import REQUIRED, verified_release_checks, verified_merge, verified_provider_outcome

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


def test_merged_commit_may_differ_from_source_sha():
    assert verified_merge(SHA, {"merged": True, "source_head_sha": SHA,
                                "merge_commit_sha": "b" * 40}) == "b" * 40


@pytest.mark.parametrize("merge", [None, {},
    {"merged": False, "source_head_sha": SHA, "merge_commit_sha": "b" * 40},
    {"merged": True, "source_head_sha": "c" * 40, "merge_commit_sha": "b" * 40},
    {"merged": True, "source_head_sha": SHA, "merge_commit_sha": "invalid"}])
def test_unverified_merge_cannot_prove_production_release(merge):
    with pytest.raises(ValueError):
        verified_merge(SHA, merge)


def test_authenticated_terminal_task_failure():
    assert verified_provider_outcome({"task_id": "kilo-42", "status": "failed"},
                                     expected_task_id="kilo-42") == {
        "task_id": "kilo-42", "status": "failed"}


@pytest.mark.parametrize("outcome", [None, {},
    {"task_id": "different", "status": "failed"},
    {"task_id": "kilo-42", "status": "running"},
    {"task_id": "kilo-42", "status": "queued"},
    {"task_id": "kilo-42", "status": "unknown"}])
def test_unverified_provider_outcomes_never_release_repair(outcome):
    with pytest.raises(ValueError):
        verified_provider_outcome(outcome, expected_task_id="kilo-42")
