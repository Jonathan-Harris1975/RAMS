"""
Patch Generator for the RAMS Optimisation Subsystem.

Handles ``patch_candidate``-tier actions only (confidence >= 98, and only
for categories whose policy ceiling allows it). This is the one place the
optimisation subsystem touches code, and it is deliberately the most
constrained module in the package:

  * It never writes to the repository itself. It produces an AnchorPatch/v1
    document plus a PatchPackage bundle and hands both to the *existing*
    infrastructure (repo_mgmt.patch_protocol for schema validation,
    repo_mgmt.validation_runner for tests/lint/regression, and
    repo_mgmt.automation_gate for the Phase 4C auto-PR decision).
  * A patch package is only "complete" once it carries tests, lint,
    regression evidence, explicit acceptance criteria, and a rollback
    package -- all required by policy
    (repo_mgmt.optimisation.policy.PatchGeneratorPolicy). Missing any of
    these raises PatchGeneratorError rather than emitting a partial patch.
  * If the automation gate rejects the patch, it is routed to manual review,
    never force-applied.

In other words: reaching "patch candidate" confidence earns a *proposal*
that flows through the same controls a human-authored patch would, not a
bypass around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repo_mgmt.optimisation.models import OptimisationAction
from repo_mgmt.optimisation.policy import OptimisationPolicy
from repo_mgmt.patch_protocol import PatchSchemaError, validate_patch


class PatchGeneratorError(Exception):
    """Raised when a patch candidate cannot be assembled to policy requirements."""


@dataclass
class AcceptanceCriteria:
    """Explicit, human-checkable criteria the patch claims to satisfy."""

    criteria: list[str]

    def __post_init__(self) -> None:
        if not self.criteria:
            raise PatchGeneratorError("acceptance criteria must be non-empty")


@dataclass
class PatchEvidence:
    """Evidence a patch candidate must carry before it can be routed anywhere."""

    tests_passed: bool
    lint_passed: bool
    regression_passed: bool
    test_command: str
    lint_command: str
    regression_command: str
    output_tail: str = ""


@dataclass
class RollbackPackage:
    """Everything needed to revert this exact patch if it is later reverted."""

    action_id: str
    reverse_patch: dict[str, Any]  # an AnchorPatch/v1 doc that undoes `reverse_patch`
    original_file_hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class PatchPackage:
    """A complete, gate-ready patch candidate. Not yet applied to any repo."""

    action_id: str
    pipeline: str
    patch_doc: dict[str, Any]
    evidence: PatchEvidence
    acceptance_criteria: AcceptanceCriteria
    rollback_package: RollbackPackage
    confidence_score: float
    status: str = "pending_gate_review"  # pending_gate_review | manual_review | auto_pr_eligible


class PatchGenerator:
    """Assembles policy-complete patch candidates for patch_candidate-tier actions."""

    def __init__(self, policy: OptimisationPolicy) -> None:
        self._policy = policy

    def build(
        self,
        *,
        action: OptimisationAction,
        patch_doc: dict[str, Any],
        evidence: PatchEvidence,
        acceptance_criteria: list[str],
        rollback_package: RollbackPackage,
    ) -> PatchPackage:
        """Validate and assemble a patch candidate. Raises if requirements are unmet.

        This performs no I/O and applies nothing; it is pure assembly plus
        validation against both AnchorPatch/v1 schema and the externally
        configured patch-generator policy.
        """
        cfg = self._policy.patch_generator

        if action.tier != "patch_candidate":
            raise PatchGeneratorError(
                f"action {action.action_id} has tier {action.tier!r}, not patch_candidate; "
                "the Patch Generator only handles patch-candidate-tier actions"
            )

        try:
            validated_doc = validate_patch(patch_doc)
        except PatchSchemaError as exc:
            raise PatchGeneratorError(f"patch does not conform to AnchorPatch/v1: {exc}") from exc

        changes = validated_doc.get("changes", [])
        if len(changes) > cfg.max_changes:
            raise PatchGeneratorError(
                f"patch has {len(changes)} changes; policy max is {cfg.max_changes}"
            )
        files = {change["file"] for change in changes}
        if len(files) > cfg.max_files:
            raise PatchGeneratorError(
                f"patch touches {len(files)} files; policy max is {cfg.max_files}"
            )

        if cfg.require_tests and not evidence.tests_passed:
            raise PatchGeneratorError("policy requires passing tests; evidence.tests_passed is False")
        if cfg.require_lint and not evidence.lint_passed:
            raise PatchGeneratorError("policy requires passing lint; evidence.lint_passed is False")
        if cfg.require_regression and not evidence.regression_passed:
            raise PatchGeneratorError(
                "policy requires passing regression checks; evidence.regression_passed is False"
            )
        if cfg.require_acceptance_criteria:
            AcceptanceCriteria(acceptance_criteria)  # raises if empty
        if cfg.require_rollback_package:
            if not rollback_package.reverse_patch:
                raise PatchGeneratorError("policy requires a non-empty rollback package")

        return PatchPackage(
            action_id=action.action_id,
            pipeline=action.pipeline,
            patch_doc=validated_doc,
            evidence=evidence,
            acceptance_criteria=AcceptanceCriteria(acceptance_criteria),
            rollback_package=rollback_package,
            confidence_score=action.confidence_score,
        )

    def route_through_gate(self, package: PatchPackage, gate_decision_ok: bool) -> PatchPackage:
        """Record the outcome of the existing Phase 4C automation gate.

        The gate decision itself must come from
        ``repo_mgmt.automation_gate.evaluate_phase4c_auto_pr_gate`` (or an
        equivalent human review) -- this method only records the outcome,
        it does not evaluate the gate itself, so the Patch Generator can
        never mark its own homework.
        """
        package.status = "auto_pr_eligible" if gate_decision_ok else "manual_review"
        return package
