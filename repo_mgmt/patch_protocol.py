"""
AnchorPatch/v1 protocol for the Repo Management Suite.

The protocol is intentionally bounded: every change names a repo-relative
file, proves its position with a non-empty anchorBefore string, and uses an
exact find string for text replacement or deletion. AnchorPatch/v1 does not
support whole-file deletion.
"""

from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = "AnchorPatch/v1"

_VALID_OPERATIONS = frozenset(["replace", "insert_after", "delete"])


class PathTraversalError(Exception):
    """Raised when a patch path resolves outside the target repository root."""


class ProtectedPathError(Exception):
    """Raised when a patch targets a path that is protected for this pipeline."""


class PatchSchemaError(Exception):
    """Raised when an AnchorPatch/v1 document fails schema validation."""


def _require_non_empty_string(change: dict[str, Any], key: str, index: int) -> str:
    """Return a required non-empty string field or raise PatchSchemaError."""
    value = change.get(key)
    if not isinstance(value, str) or not value:
        raise PatchSchemaError(f"changes[{index}].{key} must be a non-empty string")
    return value


def validate_patch(doc: Any) -> dict[str, Any]:
    """
    Validate an AnchorPatch/v1 document and return it normalised.

    Args:
        doc: Parsed JSON object expected to be a mapping.

    Returns:
        The validated patch document.

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

    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise PatchSchemaError(f"changes[{index}] must be a JSON object")

        file_path = change.get("file")
        if not isinstance(file_path, str) or not file_path:
            raise PatchSchemaError(f"changes[{index}].file must be a non-empty string")

        operation = change.get("operation")
        if operation not in _VALID_OPERATIONS:
            raise PatchSchemaError(
                f"changes[{index}].operation must be one of "
                f"{sorted(_VALID_OPERATIONS)}, got {operation!r}"
            )

        _require_non_empty_string(change, "anchorBefore", index)

        if operation in ("replace", "insert_after", "delete"):
            _require_non_empty_string(change, "find", index)

        if operation in ("replace", "insert_after") and "replace" not in change:
            raise PatchSchemaError(
                f"changes[{index}].replace is required for operation={operation!r}"
            )

    return doc


def is_protected(path: str, protected: frozenset[str]) -> bool:
    """
    Return True if *path* matches any entry in the *protected* set.

    Matching is case-sensitive and uses forward-slash repo-relative paths.
    """
    for entry in protected:
        if path == entry or path.startswith(entry):
            return True
    return False
