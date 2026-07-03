> **Document status:** Production reference
> **Last reviewed:** 2 July 2026
> **Operational authority:** README, SECURITY policy, release gate, operations guide, and this document.

# RAMS Optimisation Subsystem

The Optimisation Subsystem (`repo_mgmt/optimisation/`) turns recurring audit
evidence from the `seo-aeo-geo`, `mobile-ux`, and `on-brand` pipelines into
deterministic, confidence-scored, reversible, auditable optimisation
actions for AIMS. It is **not** an uncontrolled self-modifying system: every
component that follows exists specifically to bound what optimisation is
allowed to do on its own.

```
Audit findings (R2)
        │
        ▼
┌───────────────────┐
│  AuditEvidence     │  repo_mgmt.optimisation.models
└───────┬────────────┘
        ▼
┌───────────────────┐   never scores from a single audit cycle
│  Trend Analysis    │──────────────────────────────────────────┐
└───────┬────────────┘                                          │
        │ (eligible signal: N distinct audit cycles)             │
        ▼                                                        │
┌───────────────────┐                                            │
│ Confidence Engine  │──▶ score (0–100) + tier                   │
└───────┬────────────┘                                            │
        ▼                                                        │
┌───────────────────┐   capped by per-category policy ceiling    │
│Optimisation Engine │◀──────────────────────────────────────────┘
└───────┬────────────┘
        │ routes by effective tier
        ├─ observe          → logged only
        ├─ recommend        → surfaced for a human decision
        ├─ auto_configure   → Experiment Manager + Rollback Manager
        └─ patch_candidate  → Patch Generator → existing gate/validation
        ▼
┌───────────────────┐
│ Optimisation       │  append-only, feeds future Trend Analysis
│ History            │
└────────────────────┘
```

## Supported categories

The engine converts findings into actions across seven categories:
`scheduler`, `validators`, `prompts`, `rss`, `podcasts`,
`platform_weighting`, and `configuration`. Every category is independently
enabled/disabled and independently ceilinged in the policy file — see
[Configuration Manager](#configuration-manager) below.

## Confidence scoring

Every recommendation receives a 0–100 confidence score from
`repo_mgmt.optimisation.confidence_engine.ConfidenceEngine`, computed as a
weighted blend of four signals (weights are policy-configured, not
hard-coded):

- **evidence_strength** — how far the observed value deviates from the
  expected value.
- **sample_size** — how many independent samples the evidence spans.
- **recurrence** — how many distinct audit cycles reproduced the same
  signal (supplied by Trend Analysis, never guessed at).
- **severity** — the audit-assigned severity of the underlying finding.

The score maps to a tier via the externally configured policy:

| Score range | Tier | What it means |
|---|---|---|
| < 70 | `observe` | Logged only. No action, no recommendation surfaced. |
| 70–90 | `recommend` | Surfaced for a human decision. Nothing is applied. |
| 90–98 | `auto_configure` | May be applied automatically, wrapped in an Experiment (before/after) with automatic rollback on failed verification. |
| 98+ | `patch_candidate` | May become a code-level patch proposal — never applied automatically, still gated by tests/lint/regression/acceptance criteria and the existing Phase 4C automation gate. |

**A single audit cycle can never reach `auto_configure` or
`patch_candidate`.** This is enforced twice: once in Trend Analysis (which
simply refuses to return eligibility below the configured minimum audit
cycle count) and again inside the Confidence Engine itself (a hard score
cap when `distinct_cycles <= 1`), so the guarantee holds even if a caller
invokes the Confidence Engine directly.

**A category's policy ceiling caps the *effective* tier**, independent of
the raw score. For example, `scheduler` and `validators` ship capped at
`recommend` by default: even a 99-confidence scheduler finding is only ever
surfaced for human review, never auto-applied or turned into a patch. The
raw score is still recorded in full for audit purposes — only the routing
behaviour is capped.

## Trend Analysis: the single-anomaly guard

`repo_mgmt.optimisation.trend_analysis.TrendAnalyser` is what stops RAMS
from optimising based on one bad audit run. Evidence is ingested into the
append-only Optimisation History keyed by a stable signature
(`pipeline:category:signal`). A signature only becomes eligible for
confidence scoring once it has recurred across at least
`trend_analysis.min_audit_cycles` **distinct audit ids** (re-ingesting the
same audit run's evidence does not count twice) and across at least
`trend_analysis.min_distinct_evidence_samples` samples, within a
configurable recency window. Evidence outside that window stops counting,
so a signal that stopped recurring months ago does not keep accumulating
toward the threshold forever.

## Experiment Manager

`repo_mgmt.optimisation.experiment_manager.ExperimentManager` runs every
`auto_configure`-tier action as a tracked experiment, recording:

- **before** — the exact prior configuration state
- **after** — the proposed new state
- **audit_ids** — every audit run that justified the change
- **confidence** — the score that authorised it
- **duration** — how long the apply-and-verify cycle took
- **outcome** — `verified` or `rolled_back`

Records are written to the Optimisation History at both the `pending` and
final stages, so the full lifecycle is reconstructable from the audit log
alone even if the process restarts mid-experiment.

## Rollback

`repo_mgmt.optimisation.rollback_manager.RollbackManager` snapshots the
exact pre-change configuration state to disk **before** any
`auto_configure` action is applied. The Experiment Manager always takes a
snapshot first, then applies the change, then runs the caller-supplied
`verify_fn`. If verification fails — or raises an exception — the manager
automatically restores the snapshot via the caller-supplied `apply_fn`,
with no human step required. Snapshots persist across process restarts, so
a deploy that lands between "applied" and "verified" does not strand the
system in an unverified state.

Rollback is deliberately agnostic to *what* a configuration target is (a
scheduler interval, a prompt template, an RSS/podcast weighting table): it
only guarantees the exact prior value is handed back to the same `apply_fn`
that made the change, so restoring is symmetric with applying.

## Patch Generator: the one place code is touched

`repo_mgmt.optimisation.patch_generator.PatchGenerator` handles
`patch_candidate`-tier actions only, and it is the most constrained module
in the subsystem by design:

- It **never writes to the repository**. It assembles an AnchorPatch/v1
  document (validated through the existing
  `repo_mgmt.patch_protocol.validate_patch`, the same schema every
  human-authored patch goes through) plus a policy-complete package.
- A package is only "complete" once it carries **passing tests, passing
  lint, passing regression checks, explicit acceptance criteria, and a
  non-empty rollback package** — all required by
  `patch_generator` policy. Missing any of these raises
  `PatchGeneratorError` rather than emitting a partial patch.
- The package's routing outcome (`auto_pr_eligible` vs `manual_review`) is
  decided by the **existing** Phase 4C automation gate
  (`repo_mgmt.automation_gate`), not by the Patch Generator itself — it
  only records that outcome.
- No category ships with a `patch_candidate` policy ceiling by default
  (see `config/optimisation_policy.json`); reaching this tier at all
  requires an operator to deliberately raise a category's
  `max_tier_without_review`.

In short: reaching patch-candidate confidence earns a *proposal* that flows
through the same tests/lint/regression/review controls a human-authored
patch would, not a bypass around them.

## Optimisation History

`repo_mgmt.optimisation.history.OptimisationHistoryStore` is an
append-only, JSONL-backed log (one file per pipeline) of every evidence
record, action, recommendation, experiment, and rollback. Nothing is ever
mutated or deleted in place; corrections are new entries. This is both the
audit trail and the input Trend Analysis reads to establish recurrence, and
it is the intended input for future trend-analysis and reporting tooling.

## Configuration Manager

All thresholds — confidence tier boundaries, confidence weights, trend
analysis minimums, per-category enablement and ceilings, rollback
behaviour, and patch-generator requirements — live in
`config/optimisation_policy.json`, loaded and strictly validated by
`repo_mgmt.optimisation.policy.load_policy()`. **No optimisation threshold
is a Python literal.** Override the policy location with the
`RMS_OPTIMISATION_POLICY_PATH` environment variable (e.g. for a
staging-specific policy).

A global, fail-closed kill switch also exists at the `Settings` level:
`RMS_OPTIMISATION_ENABLED` (default `false`). Even with a permissive
policy file, no `auto_configure` or `patch_candidate` routing runs unless
this is explicitly set `true` by an operator — following the same
fail-closed pattern as `RMS_REPO_BOOTSTRAP_ENABLED` elsewhere in RAMS.

## Worked example

1. Three consecutive `mobile-ux` audit runs each report the same
   `prompts.system_prompt_drift` signal with a large observed/expected gap
   and `critical` severity.
2. `TrendAnalyser.evaluate()` returns a `TrendSignal` only after the third
   run (default policy: `min_audit_cycles = 3`).
3. `ConfidenceEngine.score()` computes ~92/100 given the strong deviation,
   full sample count, and three-cycle recurrence.
4. `OptimisationPolicy.effective_tier("prompts", "auto_configure")` allows
   this — `prompts` is ceilinged at `auto_configure`, not below it.
5. `OptimisationEngine.route()` hands the action to `ExperimentManager.run()`,
   which snapshots the current prompt config, applies the new one, and
   verifies it against the eval set.
6. If verification fails, `RollbackManager` restores the prior prompt
   automatically and the experiment is recorded `rolled_back`. If it
   passes, it's recorded `verified`.
7. Either outcome is written to the Optimisation History, queryable for
   future trend analysis.

## Files

| Path | Responsibility |
|---|---|
| `repo_mgmt/optimisation/models.py` | Shared strict schemas (`AuditEvidence`, `OptimisationAction`). |
| `repo_mgmt/optimisation/policy.py` | Configuration Manager — loads and validates `config/optimisation_policy.json`. |
| `repo_mgmt/optimisation/confidence_engine.py` | Confidence Engine. |
| `repo_mgmt/optimisation/trend_analysis.py` | Trend Analysis / single-anomaly guard. |
| `repo_mgmt/optimisation/history.py` | Optimisation History (append-only JSONL). |
| `repo_mgmt/optimisation/rollback_manager.py` | Rollback Manager. |
| `repo_mgmt/optimisation/experiment_manager.py` | Experiment Manager. |
| `repo_mgmt/optimisation/patch_generator.py` | Patch Generator. |
| `repo_mgmt/optimisation/optimisation_engine.py` | Orchestrator tying the above together. |
| `config/optimisation_policy.json` | Externalised thresholds (default policy). |
