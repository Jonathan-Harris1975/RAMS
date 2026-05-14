"""Strict runtime schemas for RAMS task, patch, validation, and report data."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PipelineId = Literal["seo-aeo-geo", "mobile-ux", "on-brand"]
Severity = Literal["critical", "high", "medium", "low"]
Classification = Literal["code_fix", "future_guidance", "manual_review", "skipped"]
PatchOperation = Literal["replace", "insert_after", "delete"]


def normalise_repo_relative_path(path: str) -> str:
    """Return a forward-slash repo-relative path or raise ValueError."""
    cleaned = str(path).strip().replace("\\", "/")
    if not cleaned:
        raise ValueError("path must be a non-empty repo-relative string")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    posix = PurePosixPath(cleaned)
    if posix.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    parts = posix.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"path traversal is not allowed: {path!r}")
    if parts and parts[0].endswith(":"):
        raise ValueError(f"drive-qualified paths are not allowed: {path!r}")
    return posix.as_posix()


class NormalisedIssueModel(BaseModel):
    """Strict NormalisedIssue schema with extra metadata allowed for reporting."""

    model_config = ConfigDict(extra="allow")

    taskId: str
    pipeline: PipelineId
    sourceAudit: str
    classification: Classification
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    affectedPaths: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    requiredOutcome: str
    allowedFixClass: str
    validationCommands: list[str] = Field(default_factory=list)
    status: str

    @field_validator("taskId")
    @classmethod
    def _task_id_must_be_non_empty(cls, value: str) -> str:
        """Require a non-empty task identifier."""
        if not value.strip():
            raise ValueError("taskId must be non-empty")
        return value

    @field_validator("affectedPaths")
    @classmethod
    def _affected_paths_are_repo_relative(cls, value: list[str]) -> list[str]:
        """Require every affected path to be repo-relative and traversal-free."""
        return [normalise_repo_relative_path(path) for path in value]

    @field_validator("evidence", "validationCommands")
    @classmethod
    def _string_lists(cls, value: list[Any]) -> list[str]:
        """Coerce list items to strings without accepting non-list containers."""
        return [str(item) for item in value]

    @model_validator(mode="after")
    def _task_id_contains_pipeline(self) -> "NormalisedIssueModel":
        """Require taskId to contain the active pipeline id."""
        if self.pipeline not in self.taskId:
            raise ValueError("taskId must contain the pipeline id")
        return self


class AnchorChangeModel(BaseModel):
    """One bounded AnchorPatch/v1 change operation."""

    model_config = ConfigDict(extra="forbid")

    file: str
    operation: PatchOperation
    anchorBefore: str
    find: str
    replace: str | None = None
    rationale: str

    @field_validator("file")
    @classmethod
    def _file_is_repo_relative(cls, value: str) -> str:
        """Require change.file to be repo-relative and traversal-free."""
        return normalise_repo_relative_path(value)

    @field_validator("anchorBefore", "find", "rationale")
    @classmethod
    def _required_text(cls, value: str) -> str:
        """Require non-empty textual fields."""
        if not isinstance(value, str) or not value:
            raise ValueError("field must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _replace_required_for_insert_and_replace(self) -> "AnchorChangeModel":
        """Require replace text for replace and insert_after operations."""
        if self.operation in {"replace", "insert_after"} and self.replace is None:
            raise ValueError("replace is required for replace and insert_after")
        return self


class AnchorPatchModel(BaseModel):
    """Strict AnchorPatch/v1 document schema."""

    model_config = ConfigDict(extra="forbid")

    patchProtocol: Literal["AnchorPatch/v1"]
    changes: list[AnchorChangeModel]
    reason: str | None = None

    @model_validator(mode="after")
    def _empty_changes_require_reason(self) -> "AnchorPatchModel":
        """Require a reason when a planner returns no executable changes."""
        if not self.changes and not (self.reason and self.reason.strip()):
            raise ValueError("empty AnchorPatch/v1 changes require a non-empty reason")
        return self


class ValidationSummaryModel(BaseModel):
    """Strict validation section for a run report."""

    model_config = ConfigDict(extra="forbid")

    commands: list[str] = Field(default_factory=list)
    passed: bool
    outputTail: str = ""


class CommitInfoModel(BaseModel):
    """Strict commit metadata entry for a run report."""

    model_config = ConfigDict(extra="forbid")

    sha: str
    message: str
    files: list[str] = Field(default_factory=list)

    @field_validator("files")
    @classmethod
    def _files_are_repo_relative(cls, value: list[str]) -> list[str]:
        """Require committed file paths to be repo-relative."""
        return [normalise_repo_relative_path(path) for path in value]


class RunReportModel(BaseModel):
    """Strict RunReport schema used before report serialisation."""

    model_config = ConfigDict(extra="allow")

    runId: str
    pipeline: PipelineId
    targetRepo: str
    branch: str
    dryRun: bool
    summary: dict[str, int]
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    validation: ValidationSummaryModel | None = None
    commits: list[CommitInfoModel] = Field(default_factory=list)
