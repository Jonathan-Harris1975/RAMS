"""
AnchorPatch/v1 protocol for the Repo Management Suite.

The protocol is intentionally bounded: every change names a repo-relative
file, proves its position with a non-empty anchorBefore string, and uses an
exact find string for text replacement or deletion. AnchorPatch/v1 does not
support whole-file deletion.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from repo_mgmt.schemas import AnchorPatchModel


PROTOCOL_VERSION = "AnchorPatch/v1"


class PathTraversalError(Exception):
    """Raised when a patch path resolves outside the target repository root."""


class ProtectedPathError(Exception):
    """Raised when a patch targets a path that is protected for this pipeline."""


class PatchSchemaError(Exception):
    """Raised when an AnchorPatch/v1 document fails schema validation."""


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
    changes = doc.get("changes")
    if not isinstance(changes, list):
        raise PatchSchemaError("'changes' must be a JSON array")
    try:
        model = AnchorPatchModel.model_validate(doc)
    except ValidationError as exc:
        raise PatchSchemaError(str(exc)) from exc
    return model.model_dump(exclude_none=True)


def is_protected(path: str, protected: frozenset[str]) -> bool:
    """
    Return True if *path* matches any entry in the *protected* set.

    Matching is case-sensitive and uses forward-slash repo-relative paths.
    """
    for entry in protected:
        if path == entry or path.startswith(entry):
            return True
    return False
