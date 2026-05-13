"""
Task ranker for the Repo Management Suite.

Scores and sorts NormalisedIssue dicts into three queues:
  code_fix        — ranked by severity_weight * confidence, capped at RMS_MAX_ISSUES_PER_RUN
  manual_review   — items that require human judgement
  future_guidance — editorial / low-confidence findings deferred for later

Severity weights:
  critical = 4
  high     = 3
  medium   = 2
  low      = 1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


@dataclass
class RankedQueues:
    """Three sorted queues produced by rank()."""

    code_fix: list[dict[str, Any]] = field(default_factory=list)
    manual_review: list[dict[str, Any]] = field(default_factory=list)
    future_guidance: list[dict[str, Any]] = field(default_factory=list)


def _score(issue: dict[str, Any]) -> float:
    """
    Compute severity_weight * confidence for a normalised issue.

    Args:
        issue: NormalisedIssue dict with 'severity' and 'confidence' keys.

    Returns:
        Numeric score (higher = more urgent).
    """
    severity = str(issue.get("severity", "low")).lower()
    weight = _SEVERITY_WEIGHTS.get(severity, 1)
    confidence: float = float(issue.get("confidence", 1.0))
    return weight * confidence


def rank(issues: list[dict[str, Any]], max_code_fix: int = 5) -> RankedQueues:
    """
    Sort *issues* into three queues and cap the code_fix queue.

    Args:
        issues: List of NormalisedIssue dicts from issue_normaliser.
        max_code_fix: Maximum number of items allowed in the code_fix queue.
                      Excess items are dropped (logged at DEBUG level).

    Returns:
        RankedQueues with each queue sorted descending by score.
    """
    code_fix: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    future_guidance: list[dict[str, Any]] = []

    for issue in issues:
        classification = str(issue.get("classification", "future_guidance"))
        if classification == "code_fix":
            code_fix.append(issue)
        elif classification == "manual_review":
            manual_review.append(issue)
        else:
            future_guidance.append(issue)

    # Sort all queues descending by score
    key = _score
    code_fix.sort(key=key, reverse=True)
    manual_review.sort(key=key, reverse=True)
    future_guidance.sort(key=key, reverse=True)

    # Cap code_fix
    if len(code_fix) > max_code_fix:
        dropped = len(code_fix) - max_code_fix
        logger.debug(
            "task_ranker: capping code_fix queue at %d — dropping %d lower-priority items",
            max_code_fix,
            dropped,
        )
        code_fix = code_fix[:max_code_fix]

    return RankedQueues(
        code_fix=code_fix,
        manual_review=manual_review,
        future_guidance=future_guidance,
    )
