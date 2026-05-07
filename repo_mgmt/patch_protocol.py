"""
AnchorPatch/v1 protocol for the Repo Management Suite.

Defines the patch schema, schema validation, and path-safety helpers used
by both the patch applier and the normaliser pipeline guard.

Schema example:
  {
    "patchProtocol": "AnchorPatch/v1",
    "changes": [
      {
        "file": "repo-relative path",
        "operation": "replace | insert_after | delete",
        "anchorBefore": "unique string confirming file position",
        "find": "exact text to match",
        "replace": "replacement text",
        "rationale": "reason"
      }
    ]
  }
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


# ── Schema helpers ─────────────────────────────────────────────────────────


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
            if not change.get("find") or not isinstance(change["find"], str):
                raise PatchSchemaError(
                    f"changes[{i}].find must be a non-empty string for operation={operation!r}"
                )

        if operation == "replace":
            if "replace" not in change:
                raise PatchSchemaError(
                    f"changes[{i}].replace is required for operation='replace'"
                )

    return doc


def is_protected(path: str, protected: frozenset[str]) -> bool:
    """
    Return True if *path* matches any entry in the *protected* set.

    An entry in *protected* matches if:
    - The path equals the entry exactly (file match), OR
    - The path starts with the entry (prefix/directory match).

    Args:
        path: Repo-relative path string to test.
        protected: Frozenset of protected path prefixes or exact names.

    Returns:
        True if *path* is protected, False otherwise.
    """
    for entry in protected:
        if path == entry or path.startswith(entry):
            return True
    return False
