# RAMS controlled live-test readiness ledger

Recorded: 2026-10-10 UTC
Repository: Jonathan-Harris1975/RAMS
Baseline main SHA: `2edea2cc6ac91892caeba5d43047e0817ea3a1f2`
Verdict: **NOT READY**

This is an evidence ledger, not an assertion of successful provider verification. A missing run URL or observation means blocked, never passed.

| Area | Requirement | Status | Implementation evidence | Test / observation | Remaining dependency | Owner |
|---|---|---|---|---|---|---|
| Detection | Koyeb deployment watch | blocked | `.github/workflows/koyeb-deployment-watch.yml`, `scripts/watch_koyeb_deployment.py` | Current HEAD run and Koyeb deployment evidence not yet captured | Verify provider configuration, run conclusions and skipped jobs | RAMS operations |
| Detection | Failure diagnostics | blocked | `.github/workflows/failure-diagnostics.yml` | Recent failure/cancelled/skipped run coverage not yet reconciled | Inspect 30 relevant runs | RAMS CI |
| Self-repair | Classification and bounded repair | blocked | `.github/workflows/autonomous-repair.yml` | Live and fixture-backed failure routing not yet demonstrated | Verify App credentials, deduplication, escalation | RAMS automation |
| Safeguards | Immutable deployment identity | blocked | `scripts/watch_koyeb_deployment.py` checks commit SHA and timestamp; workflow emits `deployment-attestation.json` | Current attestation lacks observed image digest, deployment ID and environment | Bind attestation to verified provider fields and test mismatches | RAMS release |
| Safeguards | Local release checks | blocked | `scripts/release_gate.sh`, `scripts/verify_hash_enforcement.py` | Full checks not run for baseline SHA in this review | Execute checks in clean runner | RAMS CI |
| Safeguards | Branch rules and required checks | blocked | GitHub repository settings | Rules and required-check names not verified | Inspect repository rules | Repository owner |
| Coordination | OIDC and MAST/HIVE evidence contracts | blocked | `.github/workflows/oidc-readiness.yml` | No verified cross-repo consumption or freshness evidence | Inspect MAST/HIVE and provider trust | Ecosystem maintainers |
| Coordination | PR reconciliation | blocked | PR #345 | Open documentation correction; reviews and checks not yet validated | Review and merge under branch protection | Repository maintainers |

## Safe live-test runbook

1. Freeze candidate SHA and record required checks, approvals, deployment target, image digest and service ID.
2. Confirm OIDC issuer, audience, repository, ref, environment and provider trust, using sanitised claims only.
3. Run clean-room lint, type, dependency, security, unit, contract and Docker release checks.
4. Rehearse in staging with dry-run and live writes disabled. Record SHA, image digest, Koyeb deployment ID, service, region, environment, health and timestamps.
5. Inject mocked failures: stale SHA, wrong image digest, wrong service/environment, unhealthy/crash loop, startup timeout, 429/5xx, partial region degradation, unavailable provider, duplicate event, malicious input, cancelled/skipped jobs.
6. Confirm fail-closed gating, bounded retry/backoff, deduplication, escalation, audit evidence and absence of unapproved production mutations.
7. Obtain human approval for controlled production tests; capture each run URL and deployment identity.
8. Abort on identity mismatch, unauthorised write, missing evidence, uncontrolled retry, degraded health or failed rollback. Stop further dispatches and revert to the last independently verified healthy deployment via approved provider procedure.
9. Record witnessed results and rollback outcome here before changing the verdict.

**No live failure injection or deployment mutation is authorised by this document.**
