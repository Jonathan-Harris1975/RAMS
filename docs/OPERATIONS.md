# RAMS production operations

**Status:** Production-controlled  
**Last reviewed:** 16 June 2026

## Deployment and probes

RAMS runs from the root Dockerfile on Koyeb. Use `/livez` for liveness and authenticated `/readyz` for dependency readiness. Keep one worker and one concurrent pipeline on the current instance.

## Safe operating mode

The normal production posture is dry-run-first. Live writes require `RMS_LIVE_WRITE_ENABLED=true`, an approved release gate, valid repository credentials and successful target validation. Never use missing validation tools as a reason to bypass the gate.

## Routine checks

1. Confirm `/livez` and `/readyz`.
2. Run `/ops/warmup` with the RAMS bearer token.
3. Verify R2 audit access and target repository paths.
4. Execute a single dry-run audit and inspect the evidence pack.
5. Resume scheduled workloads only after the review queue is clear.

## Rollback

Revert to the previous Koyeb deployment or commit. Preserve generated evidence and logs before rerunning a failed audit.
