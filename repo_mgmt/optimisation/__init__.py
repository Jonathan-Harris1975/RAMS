"""
RAMS Optimisation Subsystem.

Converts recurring audit evidence into deterministic, confidence-scored,
reversible, and auditable optimisation actions for AIMS. See
``docs/OPTIMISATION_ENGINE.md`` for the full architecture and workflow.

This package is deliberately *not* an uncontrolled self-modifying system:

  * Nothing is optimised from a single anomaly (repo_mgmt.optimisation.trend_analysis).
  * Every action carries a confidence score that determines what it is allowed
    to do, from silent observation up to a human-reviewable patch candidate
    (repo_mgmt.optimisation.confidence_engine).
  * Every applied action is wrapped in a before/after experiment record
    (repo_mgmt.optimisation.experiment_manager) and can be automatically
    reverted if post-change verification fails
    (repo_mgmt.optimisation.rollback_manager).
  * Every action, applied or not, is written to an append-only history for
    trend analysis and audit (repo_mgmt.optimisation.history).
  * Code-level changes are never applied directly by the optimisation
    subsystem. They are emitted as a patch package that must clear the
    existing AnchorPatch/v1 schema, validation runner, and Phase 4C
    automation gate before anything can be committed
    (repo_mgmt.optimisation.patch_generator).
"""

from __future__ import annotations

from repo_mgmt.optimisation.confidence_engine import ConfidenceEngine, ConfidenceResult
from repo_mgmt.optimisation.experiment_manager import ExperimentManager, ExperimentRecord
from repo_mgmt.optimisation.history import OptimisationHistoryStore
from repo_mgmt.optimisation.models import (
    AuditEvidence,
    ConfidenceTier,
    OptimisationAction,
    OptimisationCategory,
    OptimisationOutcome,
)
from repo_mgmt.optimisation.optimisation_engine import OptimisationEngine
from repo_mgmt.optimisation.patch_generator import PatchGenerator, PatchGeneratorError
from repo_mgmt.optimisation.policy import OptimisationPolicy, PolicyConfigurationError
from repo_mgmt.optimisation.rollback_manager import RollbackManager
from repo_mgmt.optimisation.trend_analysis import TrendAnalyser, TrendSignal

__all__ = [
    "AuditEvidence",
    "ConfidenceEngine",
    "ConfidenceResult",
    "ConfidenceTier",
    "ExperimentManager",
    "ExperimentRecord",
    "OptimisationAction",
    "OptimisationCategory",
    "OptimisationEngine",
    "OptimisationHistoryStore",
    "OptimisationOutcome",
    "OptimisationPolicy",
    "PatchGenerator",
    "PatchGeneratorError",
    "PolicyConfigurationError",
    "RollbackManager",
    "TrendAnalyser",
    "TrendSignal",
]
