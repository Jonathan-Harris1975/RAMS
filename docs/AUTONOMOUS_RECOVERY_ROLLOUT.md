# RAMS autonomous recovery rollout

## Implementation and verification contract

This rollout is RAMS-only. Do not propagate to other repositories until
all seven requirements have been verified on the default branch and in production.

1. **Detect**: authenticated GitHub workflow/PR failure events; operational
   alerts must remain active and failures must not be silently treated as success.
2. **Diagnose**: retain original failing checks, exact head SHA and a stable
   failure fingerprint; no unverified root-cause assumptions.
3. **Repair**: route bounded repair tasks to the governed model ladder;
   reject out-of-scope patches and retain model-cost evidence.
4. **Recover**: `IncidentIdentity` provides stable incident and exact-head
   attempt keys. `IncidentLedger` provides atomic, bounded leases and retry
   accounting. It MUST use persistent storage, never a temporary CI workspace
   or ephemeral Koyeb filesystem. Do not enable distributed repair workers
   until a transactional shared coordinator is provisioned and tested.
5. **Validate**: require exact-head CI, CodeQL, security and hardening results.
   A previously passing SHA never admits a new SHA.
6. **Merge/deploy**: trusted default-branch admission and native GitHub merge
   rules remain authoritative. No owner-approval forgery, check bypass or
   force-push. Verify deployment against the merged SHA before closing.
7. **Learn/report**: record incident outcomes, retries, model routing/cost,
   escalation and production health in existing operational reporting.

## Release blockers

- Ledger is currently a tested component, **not** wired to the production
  PR repair router or persistent shared storage.
- Exact-head recovery lifecycle, reporting and deployment receipts require
  integration tests against the running services.
- PR #435 must leave draft, pass all required checks and merge through the
  repository's existing trusted admission policy.
- No rollout to the next repository until these checks are evidenced.

The repair webhook's existing GitHub-comment reservation remains authoritative
until a durable, shared ledger is fully integrated. Do not run two independent
deduplication authorities or claim cross-instance exactly-once dispatch.

## Cloudflare R2 evidence storage (provisioned 2026-10-31)

Cloudflare account's existing bucket: `hive-repositories`.
Verified object: `autonomous-recovery/RAMS/README.json`.
Repository-scoped keys must remain under `autonomous-recovery/RAMS/`:
`incidents/`, `attempts/`, `audit/`, and `receipts/`.
Create other repository prefixes **only** as each repository's upgrade starts.

R2 objects are durable evidence storage, **not** a POSIX-mounted SQLite
volume and not a transactional compare-and-swap lease coordinator. Do not
upload a live SQLite database and assume concurrent writes are safe. Keep
GitHub's existing comment reservation as dispatch deduplication until a
transactional coordination backend (for example, a properly configured D1
or Durable Object service) is implemented and validated. Use the existing
R2 client and credentials; do not add public access or embed secrets.

## Provisioned D1 transactional coordinator (2026-10-31)

Cloudflare D1 database `rams-autonomous-recovery` (EU jurisdiction), UUID
`759c49e9-0d72-4967-8a32-1e8b6dd6a1f8`. Created and confirmed tables:
`incidents`, `incident_events`, and index `incident_events_key`.
This is the **coordination** store; R2 `hive-repositories/autonomous-recovery/RAMS/`
is the **evidence** store. D1 is not automatically accessible to the Koyeb
Python service. Before activation, deploy an authenticated Worker/D1 binding
or another supported secure D1 access path, implement atomic conditional
claims and exact-owner fencing, test concurrent workers, and provision scoped
credentials. Never expose Cloudflare account-wide tokens to untrusted PRs.
