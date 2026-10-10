# RAMS controlled live-test readiness ledger

Recorded: 2026-10-10 UTC
Repository: Jonathan-Harris1975/RAMS
Baseline main SHA: `b91884cf899261caba4a58552348673956aebfe3`
Verdict: **NOT READY**

Hardening branch: `assurance/live-test-evidence-20261010` (PR #346). Deployment watcher now requires a provider-observed deployment ID and sha256 image digest before emitting the attestation; this has not been validated against live Koyeb payloads or a CI run.

This is an evidence ledger, not an assertion of successful provider verification. A missing run URL or observation means blocked, never passed.

| Area | Requirement | Status | Implementation evidence | Test / observation | Remaining dependency | Owner |
|---|---|---|---|---|---|---|
| Detection | Koyeb deployment watch | blocked | `.github/workflows/koyeb-deployment-watch.yml`, `scripts/watch_koyeb_deployment.py` | Current HEAD run and Koyeb deployment evidence not yet captured | Verify provider configuration, run conclusions and skipped jobs | RAMS operations |
| Detection | Failure diagnostics | blocked | `.github/workflows/failure-diagnostics.yml` | Recent failure/cancelled/skipped run coverage not yet reconciled | Inspect 30 relevant runs | RAMS CI |
| Self-repair | Classification and bounded repair | blocked | `.github/workflows/autonomous-repair.yml` | Live and fixture-backed failure routing not yet demonstrated | Verify App credentials, deduplication, escalation | RAMS automation |
| Safeguards | Immutable deployment identity | blocked | `scripts/watch_koyeb_deployment.py` checks commit SHA and timestamp; workflow emits `deployment-attestation.json` | New watcher emits observed image digest and deployment ID and configured environment; actual Koyeb payload and environment provenance unverified | Verify actual provider payload shape, environment identity, and mismatch handling in staging | RAMS release |
| Safeguards | Local release checks | blocked | `scripts/release_gate.sh`, `scripts/verify_hash_enforcement.py` | Full checks not run for baseline SHA in this review | Execute checks in clean runner | RAMS CI |
| Safeguards | Branch rules and required checks | blocked | GitHub repository settings | Rules and required-check names not verified | Inspect repository rules | Repository owner |
| Coordination | OIDC and MAST/HIVE evidence contracts | blocked | `.github/workflows/oidc-readiness.yml` | No verified cross-repo consumption or freshness evidence | Inspect MAST/HIVE and provider trust | Ecosystem maintainers |
| Coordination | PR reconciliation | blocked | PR #345 | PR #345 merged; PR #346 remains draft; reviews and checks not yet validated | Review and merge under branch protection | Repository maintainers |

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

## 2026-10-10 implementation update

- PR #345 was merged into main at `b91884cf899261caba4a58552348673956aebfe3`.
- PR #346 commit `2ec4afbc163cd705bc60ee216d177c09bb8ce932`: CI run 38042400754, CodeQL 38042400720, security 38042400747, hardening 38042400732 and autofix 38042400787 all concluded success.
- PR #346 commits `21f8524e7a819d840a4aad6c1e6a38700a808d0e` and `1a9fc89590bfad27f0f0b14b9fb3bf303150a05c` add a second independent provider-SHA/status validation at the attestation boundary and regression cases. No workflow runs were returned for the latter commit at the time of inspection: **these new changes are not yet CI-verified**.
- Still blocked: provider-observed service/environment provenance, current-commit CI/security checks, branch protection, MAST/HIVE consumption, staging rehearsal and rollback evidence. Do not treat earlier green checks as validation of newer commits.

**Verdict remains NOT READY until mandatory evidence is verified.**

## Subsequent hardening and validation status

- Commit `f6f56e2aaa6e92ed0d8070a9da983a9772d2ca5a` rejects contradictory provider-supplied service IDs; commit `e6dace8f3f8f80ce523e75ff940c64bca10a49b8` adds the corresponding regression test. Absence of a provider service ID is **not** proof of service provenance.
- Commit `c7cabd2d93470e804c7c02c48a63fa06992a1766` restricts manually dispatched production deployment verification to `main`; commit `02362bcf28a4709544597617c2ba3d7dbfde24d4` adds the workflow regression test.
- On earlier SHA `6b4908dba1b07523e3c69f502c0e2785a41ead2d`, CodeQL, security, hardening and autofix succeeded; CI was still in progress when inspected. On SHA `e6dace8f3f8f80ce523e75ff940c64bca10a49b8`, security and autofix succeeded; CI was in progress, CodeQL queued and hardening cancelled by a newer commit. These observations do not validate the current head.
- MAST and HIVE repository read access confirmed; live OIDC evidence consumption remains unverified. Provider staging evidence and branch-protection settings remain unavailable through the current inspection.

**Stop condition:** Do not merge, deploy or mark READY until the final head passes all required checks and the staging/rollback/provider provenance and integration evidence is captured.
