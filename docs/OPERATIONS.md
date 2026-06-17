# RAMS production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 17 June 2026

Use public `/livez` for process liveness and bearer-protected `/readyz`, `/readiness`, `/ops/warmup` and `/ops/excellence` for operational evidence. Keep one worker and one concurrent pipeline unless production profiling justifies a change.

RAMS verifies the governed audits bucket periodically, records release/retention evidence in reports and alerts HIVE when a previously healthy storage check fails. Repository checkouts are materialised on demand beneath `/tmp/rams-repos`.

For recovery, preserve audit evidence, repair repository authentication or validation tooling, warm the service, confirm readiness and run one dry-run audit before live writes. See [`OPERATIONAL_ALERTING.md`](OPERATIONAL_ALERTING.md).
