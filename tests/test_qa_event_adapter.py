"""Tests for repo_mgmt.optimisation.qa_event_adapter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


from repo_mgmt.optimisation.qa_event_adapter import (
    IngestSummary,
    QaEventWatermark,
    _utc_day_strings,
    ingest_new_qa_events,
    list_qa_event_keys,
    map_qa_event,
    read_qa_events,
)


def _event(**overrides) -> dict:
    defaults = dict(
        id="qa-scheduler-20260101T000000Z-1",
        ts="2026-01-01T00:00:00.000Z",
        source="scheduler.dedupe",
        type="duplicate_blocked",
        severity="medium",
        message="duplicate content blocked",
        detail={"account": "tiktok-main"},
    )
    defaults.update(overrides)
    return defaults


class TestMapQaEvent:
    def test_maps_a_well_formed_event(self) -> None:
        evidence = map_qa_event(_event(), pipeline="on-brand")
        assert evidence is not None
        assert evidence.audit_id == "qa-scheduler-20260101T000000Z-1"
        assert evidence.pipeline == "on-brand"
        assert evidence.category == "scheduler"
        assert evidence.signal == "scheduler.dedupe.duplicate_blocked"
        assert evidence.severity == "medium"
        assert "duplicate content blocked" in evidence.detail

    def test_maps_info_severity_down_to_low(self) -> None:
        evidence = map_qa_event(_event(severity="info"), pipeline="on-brand")
        assert evidence is not None
        assert evidence.severity == "low"

    def test_maps_validator_source_to_validators_category(self) -> None:
        evidence = map_qa_event(
            _event(source="validator.anti-hype.rss", type="anti_hype_defects"),
            pipeline="on-brand",
        )
        assert evidence is not None
        assert evidence.category == "validators"

    def test_maps_podcast_source_to_podcasts_category(self) -> None:
        evidence = map_qa_event(_event(source="podcast.artwork"), pipeline="on-brand")
        assert evidence is not None
        assert evidence.category == "podcasts"

    def test_unknown_source_falls_back_to_configuration(self) -> None:
        evidence = map_qa_event(_event(source="mystery.thing"), pipeline="on-brand")
        assert evidence is not None
        assert evidence.category == "configuration"

    def test_returns_none_for_missing_id(self) -> None:
        assert map_qa_event(_event(id=""), pipeline="on-brand") is None

    def test_returns_none_for_missing_source(self) -> None:
        assert map_qa_event(_event(source=""), pipeline="on-brand") is None

    def test_returns_none_for_unparseable_timestamp(self) -> None:
        assert map_qa_event(_event(ts="not-a-date"), pipeline="on-brand") is None

    def test_handles_missing_detail_gracefully(self) -> None:
        event = _event()
        event.pop("detail")
        evidence = map_qa_event(event, pipeline="on-brand")
        assert evidence is not None


class TestListQaEventKeys:
    def test_lists_keys_across_days_back(self) -> None:
        r2 = MagicMock()
        r2.list_objects.return_value = ["qa-events/2026-01-01/a.json"]
        keys = list_qa_event_keys(r2, "audits", days_back=2)
        assert r2.list_objects.call_count == 2
        assert keys == ["qa-events/2026-01-01/a.json"]

    def test_filters_out_non_json_keys(self) -> None:
        r2 = MagicMock()
        r2.list_objects.return_value = ["qa-events/2026-01-01/a.json", "qa-events/2026-01-01/.keep"]
        keys = list_qa_event_keys(r2, "audits", days_back=1)
        assert keys == ["qa-events/2026-01-01/a.json"]

    def test_one_day_failing_does_not_abort_others(self) -> None:
        r2 = MagicMock()
        r2.list_objects.side_effect = [RuntimeError("boom"), ["qa-events/2026-01-01/a.json"]]
        keys = list_qa_event_keys(r2, "audits", days_back=2)
        assert keys == ["qa-events/2026-01-01/a.json"]


class TestReadQaEvents:
    def test_reads_and_parses_events(self) -> None:
        r2 = MagicMock()
        r2.get_object_limited.return_value = json.dumps(_event()).encode()
        events = read_qa_events(r2, "audits", ["qa-events/2026-01-01/a.json"])
        assert len(events) == 1
        assert events[0]["id"] == "qa-scheduler-20260101T000000Z-1"

    def test_skips_unreadable_object(self) -> None:
        r2 = MagicMock()
        r2.get_object_limited.side_effect = RuntimeError("not found")
        events = read_qa_events(r2, "audits", ["qa-events/2026-01-01/a.json"])
        assert events == []

    def test_skips_invalid_json(self) -> None:
        r2 = MagicMock()
        r2.get_object_limited.return_value = b"not json {"
        events = read_qa_events(r2, "audits", ["qa-events/2026-01-01/a.json"])
        assert events == []

    def test_skips_non_dict_json(self) -> None:
        r2 = MagicMock()
        r2.get_object_limited.return_value = b"[1, 2, 3]"
        events = read_qa_events(r2, "audits", ["qa-events/2026-01-01/a.json"])
        assert events == []


class TestQaEventWatermark:
    def test_load_returns_none_when_unset(self, tmp_path) -> None:
        watermark = QaEventWatermark(tmp_path)
        ts, ids = watermark.load("on-brand")
        assert ts is None
        assert ids == set()

    def test_save_then_load_roundtrips(self, tmp_path) -> None:
        watermark = QaEventWatermark(tmp_path)
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        watermark.save("on-brand", ts, {"id-1", "id-2"})
        loaded_ts, loaded_ids = watermark.load("on-brand")
        assert loaded_ts == ts
        assert loaded_ids == {"id-1", "id-2"}

    def test_corrupt_watermark_file_treated_as_unset(self, tmp_path) -> None:
        watermark = QaEventWatermark(tmp_path)
        path = watermark._path_for("on-brand")
        path.write_text("not json", encoding="utf-8")
        ts, ids = watermark.load("on-brand")
        assert ts is None
        assert ids == set()


class TestIngestNewQaEvents:
    def _stack(self, tmp_path):
        r2 = MagicMock()
        watermark = QaEventWatermark(tmp_path / "watermarks")
        engine = MagicMock()
        return r2, watermark, engine

    def test_ingests_new_events_and_advances_watermark(self, tmp_path) -> None:
        r2, watermark, engine = self._stack(tmp_path)
        r2.list_objects.return_value = ["qa-events/2026-01-01/a.json"]
        r2.get_object_limited.return_value = json.dumps(_event()).encode()

        summary = ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )

        assert summary.events_seen == 1
        assert summary.events_ingested == 1
        assert len(summary.new_signatures) == 1
        engine.ingest_findings.assert_called_once()
        last_ts, ids = watermark.load("on-brand")
        assert last_ts is not None
        assert "qa-scheduler-20260101T000000Z-1" in ids

    def test_second_call_does_not_reingest_same_event(self, tmp_path) -> None:
        r2, watermark, engine = self._stack(tmp_path)
        r2.list_objects.return_value = ["qa-events/2026-01-01/a.json"]
        r2.get_object_limited.return_value = json.dumps(_event()).encode()

        ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )
        engine.reset_mock()
        summary = ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )

        assert summary.events_ingested == 0
        engine.ingest_findings.assert_not_called()

    def test_malformed_events_are_counted_and_skipped(self, tmp_path) -> None:
        r2, watermark, engine = self._stack(tmp_path)
        r2.list_objects.return_value = ["qa-events/2026-01-01/bad.json"]
        r2.get_object_limited.return_value = json.dumps(_event(id="")).encode()

        summary = ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )

        assert summary.events_skipped_malformed == 1
        assert summary.events_ingested == 0
        engine.ingest_findings.assert_not_called()

    def test_listing_failure_returns_empty_summary_without_raising(self, tmp_path) -> None:
        r2, watermark, engine = self._stack(tmp_path)
        r2.list_objects.side_effect = RuntimeError("bucket unavailable")

        summary = ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )

        assert summary == IngestSummary()
        engine.ingest_findings.assert_not_called()

    def test_only_events_newer_than_watermark_are_ingested(self, tmp_path) -> None:
        r2, watermark, engine = self._stack(tmp_path)
        older = _event(id="qa-1", ts="2026-01-01T00:00:00.000Z")
        newer = _event(id="qa-2", ts="2026-01-01T01:00:00.000Z")
        watermark.save("on-brand", datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc), set())
        r2.list_objects.return_value = ["qa-events/2026-01-01/a.json", "qa-events/2026-01-01/b.json"]
        r2.get_object_limited.side_effect = [
            json.dumps(older).encode(),
            json.dumps(newer).encode(),
        ]

        summary = ingest_new_qa_events(
            r2=r2, bucket="audits", pipeline="on-brand", engine=engine, watermark=watermark
        )

        assert summary.events_ingested == 1
        [ingested] = engine.ingest_findings.call_args[0][0]
        assert ingested.audit_id == "qa-2"


def test_utc_day_strings_returns_requested_count() -> None:
    days = _utc_day_strings(3)
    assert len(days) == 3
    today = datetime.now(timezone.utc).date()
    assert days[0] == today.isoformat()
    assert days[1] == (today - timedelta(days=1)).isoformat()
