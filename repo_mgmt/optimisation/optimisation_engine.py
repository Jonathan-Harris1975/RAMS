"""
Optimisation Engine for the RAMS Optimisation Subsystem.

Converts audit findings into optimisation actions across the seven
supported categories (scheduler, validators, prompts, rss, podcasts,
platform_weighting, configuration). This module is the orchestrator: it
does not itself decide confidence (confidence_engine), require recurrence
(trend_analysis), apply changes (experiment_manager / rollback_manager), or
generate patches (patch_generator) -- it wires those together and enforces
the routing contract:

    observe          -> logged to history only, nothing else happens
    recommend        -> action recorded, surfaced for human decision
    auto_configure   -> applied via ExperimentManager (auto-rollback on
                         failed verification), only for enabled categories
    patch_candidate  -> handed to PatchGenerator; still requires the
                         existing automation gate before anything commits

Every action is written to the Optimisation History regardless of tier, so
"nothing happened" is itself an auditable, queryable fact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from repo_mgmt.optimisation.confidence_engine import ConfidenceEngine
from repo_mgmt.optimisation.experiment_manager import ExperimentManager, ExperimentRecord
from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import AuditEvidence, OptimisationAction
from repo_mgmt.optimisation.policy import OptimisationPolicy
from repo_mgmt.optimisation.trend_analysis import TrendAnalyser, TrendSignal

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """What the Optimisation Engine did with one action, for the caller."""

    action: OptimisationAction
    routed_to: str  # "observed" | "recommended" | "applied" | "rolled_back" | "patch_pending" | "not_eligible"
    experiment: ExperimentRecord | None = None
    detail: str = ""


class OptimisationEngine:
    """Turns recurring audit evidence into confidence-scored, routed actions."""

    def __init__(
        self,
        policy: OptimisationPolicy,
        history: OptimisationHistoryStore,
        trend_analyser: TrendAnalyser,
        confidence_engine: ConfidenceEngine,
        experiment_manager: ExperimentManager,
    ) -> None:
        self._policy = policy
        self._history = history
        self._trend = trend_analyser
        self._confidence = confidence_engine
        self._experiments = experiment_manager

    def ingest_findings(self, findings: list[AuditEvidence]) -> None:
        """Record a batch of audit findings as evidence for trend analysis.

        This is the only entry point that should be called directly from an
        audit run; it never produces an action by itself (see `evaluate`).
        """
        for finding in findings:
            self._trend.ingest(finding)

    def evaluate(self, pipeline: str, signature: str) -> OptimisationAction | None:
        """Score one evidence signature and return an action if it is eligible.

        Returns ``None`` if the signature has not yet recurred across enough
        audit cycles (trend_analysis) -- this is the single-anomaly guard
        surfaced at the engine level.
        """
        trend_signal: TrendSignal | None = self._trend.evaluate(pipeline, signature)
        if trend_signal is None:
            return None

        if not self._policy.is_category_enabled(trend_signal.category):  # type: ignore[arg-type]
            logger.info(
                "category %s is disabled by policy; confidence_engine.score() will still run "
                "below, but policy.effective_tier() will cap the resulting action for %s at "
                "'observe' regardless of its raw confidence score",
                trend_signal.category,
                signature,
            )

        result = self._confidence.score(
            evidence=list(trend_signal.evidence),
            distinct_cycles=trend_signal.distinct_cycles,
            category=trend_signal.category,
        )

        action = OptimisationAction(
            action_id=f"act-{signature}",
            signature=signature,
            pipeline=pipeline,
            category=trend_signal.category,  # type: ignore[arg-type]
            signal=trend_signal.signal,
            description=(
                f"{trend_signal.category} signal {trend_signal.signal!r} recurred across "
                f"{trend_signal.distinct_cycles} audit cycles"
            ),
            supporting_audit_ids=list(trend_signal.distinct_audit_ids),
            supporting_cycles=trend_signal.distinct_cycles,
            confidence_score=result.score,
            tier=result.effective_tier,
        )

        self._history.append(
            pipeline,
            {
                "type": "action",
                "signature": signature,
                **action.model_dump(mode="json"),
                "rationale": result.rationale,
            },
        )
        return action

    def route(
        self,
        action: OptimisationAction,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        apply_fn: Callable[[dict[str, Any]], None] | None = None,
        verify_fn: Callable[[], bool] | None = None,
    ) -> RoutingResult:
        """Route an action according to its effective tier.

        ``before``/``after``/``apply_fn``/``verify_fn`` are only required
        for ``auto_configure``-tier actions; other tiers ignore them.
        """
        if action.tier == "observe":
            return RoutingResult(action=action, routed_to="observed", detail="below recommend threshold")

        if action.tier == "recommend":
            self._history.append(
                action.pipeline,
                {"type": "recommendation", "signature": action.signature, "action_id": action.action_id},
            )
            return RoutingResult(action=action, routed_to="recommended", detail="awaiting human decision")

        if action.tier == "auto_configure":
            if before is None or after is None or apply_fn is None or verify_fn is None:
                raise ValueError(
                    "auto_configure routing requires before, after, apply_fn, and verify_fn"
                )
            experiment = self._experiments.run(
                action=action, before=before, after=after, apply_fn=apply_fn, verify_fn=verify_fn
            )
            if experiment.outcome == "verified":
                routed = "applied"
            elif experiment.outcome == "rejected":
                routed = "not_eligible"
            else:
                routed = "rolled_back"
            return RoutingResult(action=action, routed_to=routed, experiment=experiment, detail=experiment.detail)

        if action.tier == "patch_candidate":
            self._history.append(
                action.pipeline,
                {"type": "patch_candidate_pending", "signature": action.signature, "action_id": action.action_id},
            )
            return RoutingResult(
                action=action,
                routed_to="patch_pending",
                detail="hand off to repo_mgmt.optimisation.patch_generator.PatchGenerator",
            )

        return RoutingResult(action=action, routed_to="not_eligible", detail=f"unknown tier {action.tier}")
