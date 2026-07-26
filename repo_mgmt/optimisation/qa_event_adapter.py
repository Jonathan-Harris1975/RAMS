"""
QA Event Adapter for the RAMS Optimisation Subsystem.

Bridges AIMS's structured QA events -- written to the ``audits`` R2 bucket
at ``qa-events/{day}/{id}.json`` by ``services/shared/utils/qaEvents.js``
-- into ``repo_mgmt.optimisation.models.AuditEvidence``, the atomic unit
the Trend Analyser and Confidence Engine reason over.

This is the "fuel line" identified in the deployment readiness review: the
optimisation subsystem's internals (trend analysis, confidence scoring,
experiments, rollback) were correct and well-tested, but nothing ever read
AIMS's QA events into them. ``ingest_new_qa_events`` is that reader and the
only place this bucket prefix is turned into ``AuditEvidence``.

Design notes:
  * Fail-soft throughout, matching ``repo_mgmt.audit_reader``: a missing
    bucket, an unreadable object, or a malformed event is logged and
    skipped rather than raised, so a bad AIMS deploy can never break a RAMS
    pipeline run.
  * A small on-disk watermark (``QaEventWatermark``) tracks the newest
    event timestamp already ingested per pipeline, so calling this once per
    pipeline run never double-counts the same event as two separate audit
    cycles -- that would silently inflate ``distinct_cycles`` and defeat
    Trend Analysis's single-anomaly guard.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repo_mgmt.optimisation.models import AuditEvidence, OptimisationCategory

if TYPE_CHECKING:
    from repo_mgmt.optimisation.optimisation_engine import OptimisationEngine
    from repo_mgmt.r2_client import R2Client

logger = logging.getLogger(__name__)

_QA_EVENTS_PREFIX = "qa-events"
_DEFAULT_STATE_DIR = Path("data") / "optimisation_state" / "qa_event_watermarks"
_DEFAULT_DAYS_BACK = 2  # today + yesterday (UTC), covers events written near midnight
_DEFAULT_MAX_KEYS_PER_DAY = 500
_DEFAULT_MAX_EVENTS_PER_RUN = 300
_DEFAULT_MAX_OBJECT_BYTES = 262_144  # 256 KiB; QA events are small structured JSON

# AIMS event `source` values (see services/shared/utils/qaEvents.js callers,
# e.g. "scheduler.dedupe", "validator.anti-hype.<pipeline>", "podcast.artwork")
# are prefixed by subsystem. Map each known prefix to the nearest
# optimisation category. Unmapped sources fall back to "configuration"
# rather than being dropped, so evidence is never silently lost -- it just
# starts out in the most conservative, human-reviewed category.
_SOURCE_CATEGORY_PREFIXES: tuple[tuple[str, OptimisationCategory], ...] = (
    ("scheduler", "scheduler"),
    ("validator", "validators"),
    ("prompt", "prompts"),
    ("rss", "rss"),
    ("podcast", "podcasts"),
    ("platform", "platform_weighting"),
    ("weighting", "platform_weighting"),
)

# qaEvents.js allows an "info" severity that AuditEvidence does not model
# (it only covers actionable audit severities). Map info down to "low"
# rather than dropping the event -- ingestion is meant to be permissive;
# confidence scoring is where severity actually matters.
_SEVERITY_MAP: dict[str, str] = {
    "info": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}


def _category_for_source(source: str) -> OptimisationCategory:
    """Map an AIMS QA event `source` string to an optimisation category."""
    lowered = source.lower()
    for prefix, category in _SOURCE_CATEGORY_PREFIXES:
        if lowered.startswith(prefix) or f".{prefix}" in lowered:
            return category
    return "configuration"


def map_qa_event(event: dict[str, Any], *, pipeline: str) -> AuditEvidence | None:
    """Map one AIMS QA event dict to an ``AuditEvidence``, or ``None`` if malformed.

    Malformed events (missing id/source/type, or an unparseable timestamp)
    are logged and skipped rather than raising, matching the fail-soft
    posture of the rest of the R2 read path.
    """
    event_id = str(event.get("id") or "").strip()
    source = str(event.get("source") or "").strip()
    event_type = str(event.get("type") or "").strip()
    if not event_id or not source or not event_type:
        logger.warning(
            "qa_event_adapter: skipping malformed event (missing id/source/type): %r",
            event,
        )
        return None

    ts_raw = str(event.get("ts") or "")
    try:
        observed_at = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning(
            "qa_event_adapter: skipping event %s with unparseable ts=%r", event_id, ts_raw
        )
        return None

    severity_raw = str(event.get("severity") or "medium").lower()
    severity = _SEVERITY_MAP.get(severity_raw, "medium")

    message = str(event.get("message") or "")
    detail_payload = event.get("detail")
    detail_text = message
    if isinstance(detail_payload, dict) and detail_payload:
        try:
            detail_text = f"{message} {json.dumps(detail_payload, sort_keys=True, default=str)}".strip()
        except (TypeError, ValueError):
            pass

    try:
        return AuditEvidence(
            audit_id=event_id,
            pipeline=pipeline,
            category=_category_for_source(source),
            signal=f"{source}.{event_type}",
            severity=severity,  # type: ignore[arg-type]
            sample_size=1,
            detail=detail_text[:2000],
            observed_at=observed_at,
        )
    except Exception:
        logger.warning(
            "qa_event_adapter: could not build AuditEvidence from event %s",
            event_id,
            exc_info=True,
        )
        return None


def _utc_day_strings(days_back: int) -> list[str]:
    """Return UTC calendar day strings from today back ``days_back - 1`` days."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(max(1, days_back))]


def list_qa_event_keys(
    r2: "R2Client",
    bucket: str,
    *,
    days_back: int = _DEFAULT_DAYS_BACK,
    max_keys_per_day: int = _DEFAULT_MAX_KEYS_PER_DAY,
) -> list[str]:
    """List ``qa-events/{day}/*.json`` object keys for the last *days_back* UTC days.

    Fail-soft: a listing failure for one day (or the whole bucket) is
    logged and treated as "no keys for that day" rather than raised.
    """
    keys: list[str] = []
    for day in _utc_day_strings(days_back):
        prefix = f"{_QA_EVENTS_PREFIX}/{day}/"
        try:
            keys.extend(r2.list_objects(bucket, prefix, max_keys=max_keys_per_day))
        except Exception as exc:
            logger.warning(
                "qa_event_adapter: could not list %r in bucket %r: %s", prefix, bucket, exc
            )
    # R2 listings for adjacent day windows should be disjoint, but test doubles,
    # eventual-consistency edges, or malformed providers can repeat keys. Preserve
    # order while de-duplicating so one QA event can never count as two cycles.
    return list(dict.fromkeys(key for key in keys if key.endswith(".json")))


def read_qa_events(
    r2: "R2Client",
    bucket: str,
    keys: list[str],
    *,
    max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
) -> list[dict[str, Any]]:
    """Read and parse each qa-event JSON object; unreadable objects are skipped."""
    events: list[dict[str, Any]] = []
    for key in keys:
        try:
            raw = r2.get_object_limited(bucket=bucket, key=key, max_bytes=max_object_bytes)
        except Exception as exc:
            logger.warning("qa_event_adapter: could not read %r from bucket %r: %s", key, bucket, exc)
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            logger.warning("qa_event_adapter: %r is not valid JSON: %s", key, exc)
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
        else:
            logger.warning(
                "qa_event_adapter: %r parsed as %s (expected dict) — skipping",
                key,
                type(parsed).__name__,
            )
    return events


class QaEventWatermark:
    """Tracks the newest QA event timestamp already ingested, per pipeline.

    Backed by a tiny on-disk JSON file (one per pipeline) rather than the
    JSONL history log, since this is bookkeeping metadata, not an audit
    record -- it should never grow unbounded and never needs to be replayed.
    Storing the event ids observed *at* the watermark timestamp (not just
    the timestamp) guards against dropping same-timestamp siblings on the
    boundary.
    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._state_dir = Path(state_dir) if state_dir else _DEFAULT_STATE_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for(self, pipeline: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pipeline)
        return self._state_dir / f"qa_event_watermark_{safe}.json"

    def load(self, pipeline: str) -> tuple[datetime | None, set[str]]:
        """Return ``(last_ts, ids_at_last_ts)`` for a pipeline, or ``(None, set())``."""
        path = self._path_for(pipeline)
        if not path.exists():
            return None, set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(str(data["last_ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ids = set(data.get("ids_at_last_ts") or [])
            return ts, ids
        except Exception:
            logger.warning(
                "qa_event_adapter: could not read watermark for %s; treating as unset",
                pipeline,
                exc_info=True,
            )
            return None, set()

    def save(self, pipeline: str, last_ts: datetime, ids_at_last_ts: set[str]) -> None:
        """Persist the newest processed timestamp and the ids observed at it."""
        path = self._path_for(pipeline)
        payload = {
            "last_ts": last_ts.isoformat(),
            "ids_at_last_ts": sorted(ids_at_last_ts),
        }
        with self._lock:
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )


@dataclass
class IngestSummary:
    """What one ``ingest_new_qa_events`` call did, for logging/observability."""

    events_seen: int = 0
    events_ingested: int = 0
    events_skipped_malformed: int = 0
    new_signatures: set[str] = field(default_factory=set)


def ingest_new_qa_events(
    *,
    r2: "R2Client",
    bucket: str,
    pipeline: str,
    engine: "OptimisationEngine",
    watermark: QaEventWatermark,
    days_back: int = _DEFAULT_DAYS_BACK,
    max_events: int = _DEFAULT_MAX_EVENTS_PER_RUN,
    max_object_bytes: int = _DEFAULT_MAX_OBJECT_BYTES,
) -> IngestSummary:
    """List, read, map, and ingest new AIMS QA events for one RAMS pipeline.

    This is the call site described in the deployment readiness review: the
    only place AIMS's ``qa-events/{day}/*.json`` objects are turned into
    ``AuditEvidence`` and fed into ``OptimisationEngine.ingest_findings()``.
    Entirely fail-soft -- any error here is logged and results in an
    empty/partial summary, never a raised exception, so a RAMS pipeline run
    can never fail because of an AIMS-side QA event.
    """
    summary = IngestSummary()
    last_ts, ids_at_last_ts = watermark.load(pipeline)

    try:
        keys = list_qa_event_keys(r2, bucket, days_back=days_back)
    except Exception:
        logger.warning(
            "qa_event_adapter: listing qa-events failed for pipeline %s", pipeline, exc_info=True
        )
        return summary

    if not keys:
        return summary

    events = read_qa_events(r2, bucket, keys, max_object_bytes=max_object_bytes)
    summary.events_seen = len(events)

    # Sort by timestamp so the watermark always advances monotonically even
    # if R2 listing returns keys out of order.
    events.sort(key=lambda event: str(event.get("ts") or ""))

    new_evidence: list[AuditEvidence] = []
    newest_ts = last_ts
    newest_ids: set[str] = set(ids_at_last_ts)

    for event in events[:max_events]:
        evidence = map_qa_event(event, pipeline=pipeline)
        if evidence is None:
            summary.events_skipped_malformed += 1
            continue

        if last_ts is not None:
            if evidence.observed_at < last_ts:
                continue
            if evidence.observed_at == last_ts and evidence.audit_id in ids_at_last_ts:
                continue

        new_evidence.append(evidence)
        summary.new_signatures.add(evidence.signature)

        if newest_ts is None or evidence.observed_at > newest_ts:
            newest_ts = evidence.observed_at
            newest_ids = {evidence.audit_id}
        elif evidence.observed_at == newest_ts:
            newest_ids.add(evidence.audit_id)

    if not new_evidence:
        return summary

    engine.ingest_findings(new_evidence)
    summary.events_ingested = len(new_evidence)

    if newest_ts is not None:
        watermark.save(pipeline, newest_ts, newest_ids)

    logger.info(
        "qa_event_adapter: ingested %d/%d new qa-event(s) for pipeline %s across %d signature(s)",
        summary.events_ingested,
        summary.events_seen,
        pipeline,
        len(summary.new_signatures),
    )
    return summary
