# RAMS professional operations and alerting

**Status:** Paid Koyeb production service  
**Last reviewed:** 21 June 2026

RAMS exposes authenticated operational evidence through `GET /ops/excellence`. The response includes release identity, periodic audits-bucket verification, retention metadata, repository-bootstrap state, Koyeb deployment contract, live-write controls and model-provider privacy policy.

## Runtime controls

```env
RMS_R2_VERIFY_INTERVAL_SECONDS=900
RMS_REPORT_RETENTION_DAYS=180
RMS_RELEASE_ID={{ secret.RMS_RELEASE_ID }}
OPS_ALERT_WEBHOOK_URL={{ secret.OPS_ALERT_WEBHOOK_URL }}
OPS_ALERT_WEBHOOK_TOKEN={{ secret.OPS_EVENT_INGEST_TOKEN }}
OPS_ALERT_TIMEOUT_SECONDS=8
```

`RMS_RELEASE_ID` may be the Git commit SHA or Koyeb deployment identifier. Store it as a secret-style binding if the deployment platform cannot inject a non-secret runtime variable reliably.

The periodic R2 check uses the governed `audits` bucket. It sends a central HIVE Ops event only when verification moves from healthy to failed, avoiding repeated alert noise during one incident. Each published report carries redacted release evidence and the intended retention period.

## Repository bootstrap recovery

1. Confirm `RMS_REPO_BOOTSTRAP_ENABLED=true`.
2. Confirm `RMS_WEBSITE_REPO_URL` and `RMS_AIMS_REPO_URL` secret bindings resolve to repository URLs.
3. Check GitHub credential scope without printing the token.
4. Remove only the failed temporary checkout beneath `/tmp/rams-repos`; never delete audit evidence.
5. Run authenticated `/ops/warmup`, then `/readyz`.
6. Perform one dry-run audit before resuming live-write work.

## Deployment notifications

The post-CI Koyeb watcher needs GitHub secrets:

```text
KOYEB_TOKEN
KOYEB_SERVICE
OPS_ALERT_WEBHOOK_URL
OPS_ALERT_WEBHOOK_TOKEN
```

CI failures and failed paid-production deployments are sent to HIVE-UI Ops independently of provider email. Event delivery is bounded, redacted and non-blocking.
