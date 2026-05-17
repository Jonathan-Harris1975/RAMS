from types import SimpleNamespace

from repo_mgmt.automation_gate import evaluate_phase4c_auto_pr_gate


def _patch(file="assets/css/site.css", operation="replace"):
    return {
        "patchProtocol": "AnchorPatch/v1",
        "changes": [
            {
                "file": file,
                "operation": operation,
                "anchorBefore": ".hero {",
                "find": "color: red;",
                "replace": "color: blue;",
                "rationale": "Tight scoped CSS fix.",
            }
        ],
    }


def test_phase4c_gate_allows_bounded_validated_patch():
    decision = evaluate_phase4c_auto_pr_gate(
        task={"classification": "code_fix"},
        patch_doc=_patch(),
        modified_files=["assets/css/site.css"],
        validation=SimpleNamespace(passed=True),
    )

    assert decision.ok is True
    assert decision.decision == "auto_pr_allowed"
    assert decision.to_report()["phase"] == "4C"


def test_phase4c_gate_blocks_protected_paths():
    decision = evaluate_phase4c_auto_pr_gate(
        task={"classification": "code_fix"},
        patch_doc=_patch(".github/workflows/ci.yml"),
        modified_files=[".github/workflows/ci.yml"],
        validation=SimpleNamespace(passed=True),
    )

    assert decision.ok is False
    assert "Protected path" in " ".join(decision.defects)


def test_phase4c_gate_requires_validation():
    decision = evaluate_phase4c_auto_pr_gate(
        task={"classification": "code_fix"},
        patch_doc=_patch(),
        modified_files=["assets/css/site.css"],
        validation=None,
    )

    assert decision.ok is False
    assert "validation" in " ".join(decision.defects).lower()
