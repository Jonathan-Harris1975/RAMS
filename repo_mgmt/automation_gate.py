"""Phase 4C autonomous engineering gate for RAMS live patching.

The gate makes `writing-plans`, `systematic-debugging`, and `executing-plans`
auto-PR safe by requiring a bounded plan, path-scoped diff, validation evidence,
and no protected/high-risk operations before a live task can be committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

PROTECTED_PREFIXES = (
    ".github/workflows/",
    "blog/posts/",
    "transcripts/",
    "podcast/episodes/",
    "podcast/TT-",
)
PROTECTED_EXACT = {
    "data/podcast-episodes.json",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
}
ALLOWED_OPERATIONS = {"replace", "insert_after"}
MAX_FILES = 8
MAX_CHANGES = 12
MAX_REPLACE_CHARS = 18_000


@dataclass
class AutomationGateDecision:
    """Result from the Phase 4C engineering automation gate."""

    ok: bool
    decision: str
    phase: str = "4C"
    skills: list[str] = field(
        default_factory=lambda: ["writing-plans", "systematic-debugging", "executing-plans"]
    )
    defects: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_report(self) -> dict[str, Any]:
        """Return a serialisable report block for task metadata."""
        return {
            "ok": self.ok,
            "decision": self.decision,
            "phase": self.phase,
            "skills": list(self.skills),
            "defects": list(self.defects),
            "evidence": dict(self.evidence),
        }


def _normalise_path(path: str) -> str:
    cleaned = str(path or "").strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    posix = PurePosixPath(cleaned)
    if not cleaned or posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError(f"unsafe path: {path!r}")
    return posix.as_posix()


def _is_protected(path: str) -> bool:
    return path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def evaluate_phase4c_auto_pr_gate(
    *,
    task: dict[str, Any],
    patch_doc: dict[str, Any],
    modified_files: list[str],
    validation: Any | None,
    baseline_validation: Any | None = None,
) -> AutomationGateDecision:
    """Decide whether a RAMS task may be committed without manual review."""
    defects: list[str] = []
    changes = list(patch_doc.get("changes") or [])
    files: list[str] = []

    if task.get("classification") != "code_fix":
        defects.append("Only code_fix tasks may enter the engineering auto-PR lane.")
    if not changes:
        defects.append("No executable AnchorPatch/v1 changes were produced.")
    if len(changes) > MAX_CHANGES:
        defects.append(f"Patch has {len(changes)} changes; maximum is {MAX_CHANGES}.")

    for index, change in enumerate(changes, start=1):
        if not isinstance(change, dict):
            defects.append(f"Change {index} is not an object.")
            continue
        operation = str(change.get("operation", ""))
        if operation not in ALLOWED_OPERATIONS:
            defects.append(f"Change {index} uses disallowed operation: {operation or '<missing>'}.")
        try:
            file_path = _normalise_path(str(change.get("file", "")))
            files.append(file_path)
            if _is_protected(file_path):
                defects.append(f"Protected path cannot be auto-PR'd: {file_path}")
        except ValueError as exc:
            defects.append(str(exc))
        for key in ("anchorBefore", "find", "replace", "rationale"):
            if key != "replace" or operation in ALLOWED_OPERATIONS:
                if change.get(key) in (None, ""):
                    defects.append(f"Change {index} is missing required {key} text.")
        replace_text = str(change.get("replace") or "")
        if len(replace_text) > MAX_REPLACE_CHARS:
            defects.append(f"Change {index} replace text is too large for autonomous PR.")

    unique_files = sorted(set(files or modified_files))
    if len(unique_files) > MAX_FILES:
        defects.append(f"Patch touches {len(unique_files)} files; maximum is {MAX_FILES}.")
    for path in unique_files:
        if _is_protected(path):
            defects.append(f"Modified protected path cannot be auto-PR'd: {path}")

    validation_passed = bool(getattr(validation, "passed", False))
    if validation is None:
        defects.append("Post-patch validation did not run.")
    elif not validation_passed:
        defects.append("Post-patch validation failed.")

    baseline_passed = True if baseline_validation is None else bool(getattr(baseline_validation, "passed", False))
    if baseline_validation is not None and not baseline_passed:
        defects.append("Clean-repo baseline validation failed before patching.")

    evidence = {
        "patchProtocol": patch_doc.get("patchProtocol"),
        "changeCount": len(changes),
        "fileCount": len(unique_files),
        "files": unique_files,
        "validationPassed": validation_passed,
        "baselineValidationPassed": baseline_passed,
        "maxFiles": MAX_FILES,
        "maxChanges": MAX_CHANGES,
    }
    ok = not defects
    return AutomationGateDecision(
        ok=ok,
        decision="auto_pr_allowed" if ok else "manual_review",
        defects=defects,
        evidence=evidence,
    )
