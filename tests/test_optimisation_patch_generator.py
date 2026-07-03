"""Tests for repo_mgmt.optimisation.patch_generator — controlled patch emission."""

from __future__ import annotations

import pytest

from repo_mgmt.optimisation.models import OptimisationAction
from repo_mgmt.optimisation.patch_generator import (
    PatchEvidence,
    PatchGenerator,
    PatchGeneratorError,
    RollbackPackage,
)
from repo_mgmt.optimisation.policy import load_policy


def _action(**overrides) -> OptimisationAction:
    defaults = dict(
        action_id="act-1",
        signature="sig-1",
        pipeline="mobile-ux",
        category="prompts",
        signal="drift",
        description="prompt drift detected across 6 cycles",
        supporting_audit_ids=["audit-1", "audit-2", "audit-3", "audit-4", "audit-5", "audit-6"],
        supporting_cycles=6,
        confidence_score=99.0,
        tier="patch_candidate",
    )
    defaults.update(overrides)
    return OptimisationAction(**defaults)


def _patch_doc(**overrides) -> dict:
    doc = {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": "prompts/system_prompt.txt",
                "operation": "replace",
                "anchorBefore": "You are a helpful assistant.",
                "find": "You are a helpful assistant.",
                "replace": "You are a precise, evidence-grounded assistant.",
                "rationale": "reduce drift",
            }
        ],
        "reason": "confidence 99 patch candidate",
    }
    doc.update(overrides)
    return doc


def _passing_evidence() -> PatchEvidence:
    return PatchEvidence(
        tests_passed=True,
        lint_passed=True,
        regression_passed=True,
        test_command="pytest",
        lint_command="ruff check",
        regression_command="pytest -m regression",
    )


def _rollback_package() -> RollbackPackage:
    return RollbackPackage(
        action_id="act-1",
        reverse_patch={"patchProtocol": "AnchorPatch/v1", "changes": [], "reason": "revert"},
    )


def test_complete_patch_candidate_builds_successfully() -> None:
    generator = PatchGenerator(load_policy())
    package = generator.build(
        action=_action(),
        patch_doc=_patch_doc(),
        evidence=_passing_evidence(),
        acceptance_criteria=["prompt no longer drifts on the eval set"],
        rollback_package=_rollback_package(),
    )
    assert package.status == "pending_gate_review"
    assert package.patch_doc["patchProtocol"] == "AnchorPatch/v1"


def test_non_patch_candidate_tier_is_rejected() -> None:
    generator = PatchGenerator(load_policy())
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(tier="recommend"),
            patch_doc=_patch_doc(),
            evidence=_passing_evidence(),
            acceptance_criteria=["x"],
            rollback_package=_rollback_package(),
        )


def test_malformed_patch_doc_is_rejected() -> None:
    generator = PatchGenerator(load_policy())
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(),
            patch_doc={"not": "a valid anchor patch"},
            evidence=_passing_evidence(),
            acceptance_criteria=["x"],
            rollback_package=_rollback_package(),
        )


@pytest.mark.parametrize(
    "field", ["tests_passed", "lint_passed", "regression_passed"]
)
def test_missing_required_evidence_is_rejected(field: str) -> None:
    generator = PatchGenerator(load_policy())
    kwargs = dict(
        tests_passed=True,
        lint_passed=True,
        regression_passed=True,
        test_command="pytest",
        lint_command="ruff check",
        regression_command="pytest -m regression",
    )
    kwargs[field] = False
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(),
            patch_doc=_patch_doc(),
            evidence=PatchEvidence(**kwargs),
            acceptance_criteria=["x"],
            rollback_package=_rollback_package(),
        )


def test_empty_acceptance_criteria_is_rejected() -> None:
    generator = PatchGenerator(load_policy())
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(),
            patch_doc=_patch_doc(),
            evidence=_passing_evidence(),
            acceptance_criteria=[],
            rollback_package=_rollback_package(),
        )


def test_empty_rollback_package_is_rejected() -> None:
    generator = PatchGenerator(load_policy())
    empty_rollback = RollbackPackage(action_id="act-1", reverse_patch={})
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(),
            patch_doc=_patch_doc(),
            evidence=_passing_evidence(),
            acceptance_criteria=["x"],
            rollback_package=empty_rollback,
        )


def test_too_many_files_is_rejected() -> None:
    generator = PatchGenerator(load_policy())
    changes = [
        {
            "file": f"prompts/prompt_{i}.txt",
            "operation": "replace",
            "anchorBefore": "old",
            "find": "old",
            "replace": "new",
            "rationale": "bulk change",
        }
        for i in range(20)
    ]
    with pytest.raises(PatchGeneratorError):
        generator.build(
            action=_action(),
            patch_doc=_patch_doc(changes=changes),
            evidence=_passing_evidence(),
            acceptance_criteria=["x"],
            rollback_package=_rollback_package(),
        )


def test_route_through_gate_rejection_forces_manual_review() -> None:
    generator = PatchGenerator(load_policy())
    package = generator.build(
        action=_action(),
        patch_doc=_patch_doc(),
        evidence=_passing_evidence(),
        acceptance_criteria=["x"],
        rollback_package=_rollback_package(),
    )
    generator.route_through_gate(package, gate_decision_ok=False)
    assert package.status == "manual_review"


def test_route_through_gate_approval_marks_auto_pr_eligible() -> None:
    generator = PatchGenerator(load_policy())
    package = generator.build(
        action=_action(),
        patch_doc=_patch_doc(),
        evidence=_passing_evidence(),
        acceptance_criteria=["x"],
        rollback_package=_rollback_package(),
    )
    generator.route_through_gate(package, gate_decision_ok=True)
    assert package.status == "auto_pr_eligible"
