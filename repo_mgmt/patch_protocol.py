"""
AnchorPatch/v1 protocol for the Repo Management Suite.

Defines the patch schema, validation rules, and path-safety helpers used
by both the patch applier and the normaliser pipeline guard.

Schema example:
  {
    "patchProtocol": "AnchorPatch/v1",
    "changes": [
      {
        "file": "repo-relative path",
        "operation": "replace | insert_after | delete",
        "anchorBefore": "unique string confirming file position",
        "find": "exact text to match (required for replace/insert_after; optional for delete)",
        "replace": "replacement text (required for replace/insert_after)",
        "rationale": "reason"
      }
    ]
  }

Validation rules:
  - patchProtocol must equal "AnchorPatch/v1"
  - changes must be a JSON array
  - file must be a non-empty repo-relative string
  - operation must be one of: replace, insert_after, delete
  - replace  requires non-empty find and replace
  - insert_after requires non-empty find and replace
  - delete:  find is OPTIONAL
      - If find is empty  → whole file is deleted
      - If find non-empty → that exact text is removed from the file
"""

from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = "AnchorPatch/v1"

_VALID_OPERATIONS = frozenset(["replace", "insert_after", "delete"])


# ── Custom exceptions ──────────────────────────────────────────────────────


class PathTraversalError(Exception):
    """Raised when a patch path resolves outside the target repository root."""


class ProtectedPathError(Exception):
    """Raised when a patch targets a path that is protected for this pipeline."""


class PatchSchemaError(Exception):
    """Raised when an AnchorPatch/v1 document fails schema validation."""


# ── Schema validation ──────────────────────────────────────────────────────


def validate_patch(doc: Any) -> dict[str, Any]:
    """
    Validate an AnchorPatch/v1 document and return it normalised.

    Args:
        doc: Parsed JSON object (expected to be a dict).

    Returns:
        The validated patch dict.

    Raises:
        PatchSchemaError: If the document does not conform to AnchorPatch/v1.
    """
    if not isinstance(doc, dict):
        raise PatchSchemaError(
            f"AnchorPatch/v1 document must be a JSON object, got {type(doc).__name__}"
        )

    protocol = doc.get("patchProtocol")
    if protocol != PROTOCOL_VERSION:
        raise PatchSchemaError(
            f"patchProtocol must equal {PROTOCOL_VERSION!r}, got {protocol!r}"
        )

    changes = doc.get("changes")
    if not isinstance(changes, list):
        raise PatchSchemaError("'changes' must be a JSON array")

    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise PatchSchemaError(f"changes[{i}] must be a JSON object")

        file_path = change.get("file")
        if not file_path or not isinstance(file_path, str):
            raise PatchSchemaError(f"changes[{i}].file must be a non-empty string")

        operation = change.get("operation")
        if operation not in _VALID_OPERATIONS:
            raise PatchSchemaError(
                f"changes[{i}].operation must be one of "
                f"{sorted(_VALID_OPERATIONS)}, got {operation!r}"
            )

        if operation in ("replace", "insert_after"):
            # find is required and must be non-empty
            if not change.get("find") or not isinstance(change["find"], str):
                raise PatchSchemaError(
                    f"changes[{i}].find must be a non-empty string "
                    f"for operation={operation!r}"
                )
            # replace field required
            if "replace" not in change:
                raise PatchSchemaError(
                    f"changes[{i}].replace is required for operation={operation!r}"
                )

        # delete: find is optional — empty find means delete the whole file

    return doc


# ── Path safety helper ─────────────────────────────────────────────────────


def is_protected(path: str, protected: frozenset[str]) -> bool:
    """
    Return True if *path* matches any entry in the *protected* set.

    Matching rules (forward-slash normalised, case-sensitive):
    - Exact match: path == entry
    - Prefix match: path starts with entry (directory prefix)

    Args:
        path: Repo-relative path string (forward-slash separated).
        protected: Frozenset of protected path prefixes or exact file names.

    Returns:
        True if *path* is protected, False otherwise.
    """
    for entry in protected:
        if path == entry or path.startswith(entry):
            return True
    return False
