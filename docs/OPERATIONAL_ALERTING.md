# RAMS professional operations and alerting

**Status:** Paid Koyeb production service  
**Last reviewed:** 17 June 2026

RAMS exposes authenticated operational evidence through `GET /ops/excellence`. The response includes release identity, periodic audits-bucket verification, retention metadata and repository-bootstrap state.

## Runtime controls

```env
RMS_R2_VERIFY_INTERVAL_SECONDS=900
RMS_REPORT_RETENTION_DAYS=180
RMS_RELEASE_ID=<commit-or-koyeb-deployment-id>
OPS_ALERT_WEBHOOK_URL=https://<hive-api>/v1/ops/events
OPS_ALERT_WEBHOOK_TOKEN={{ secret.OPS_EVENT_INGEST_TOKEN }}
OPS_ALERT_TIMEOUT_SECONDS=8
```

The periodic R2 check uses the governed audits bucket. It sends a central event only when the verification state changes from successful to failed, avoiding repeated alerts during one incident. Each published report carries redacted release evidence and its intended retention period.

## Repository bootstrap recovery

1. Confirm `RMS_REPO_BOOTSTRAP_ENABLED=true` and the two repository URL secret references resolve.
2. Check GitHub credential scope without printing the token.
3. Remove only the failed temporary checkout beneath `/tmp/rams-repos`; never delete audit evidence.
4. Run authenticated `/ops/warmup`, then `/readyz`.
5. Perform one dry-run audit before resuming live-write work.

## Deployment notifications

The post-CI Koyeb watcher needs GitHub secrets `KOYEB_TOKEN`, `KOYEB_SERVICE`, `OPS_ALERT_WEBHOOK_URL` and `OPS_ALERT_WEBHOOK_TOKEN`. CI failures and failed paid-production deployments are sent to HIVE-UI Ops independently of provider email.
