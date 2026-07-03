"""
Optimisation History for the RAMS Optimisation Subsystem.

An append-only, local, JSONL-backed log of every optimisation event: raw
evidence ingested, actions proposed, actions applied, verification
outcomes, and rollbacks. Nothing is ever mutated or deleted in place --
corrections are new entries, so the log itself is the audit trail.

The store is intentionally simple (one JSONL file per pipeline) rather than
a database, matching RAMS's existing preference for durable, inspectable
JSON artifacts (see repo_mgmt.report_writer). It is the input trend_analysis
reads to decide whether a signal has recurred across enough audit cycles,
and it is what future trend-analysis / reporting tooling should query.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path("data") / "optimisation_history"


class OptimisationHistoryStore:
    """Append-only JSONL history, one file per pipeline, thread-safe writes."""

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._state_dir = Path(state_dir) if state_dir else _DEFAULT_STATE_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file_for(self, pipeline: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pipeline)
        return self._state_dir / f"{safe_name}.jsonl"

    def append(self, pipeline: str, record: dict[str, Any]) -> None:
        """Append one JSON-serialisable record. Never overwrites prior entries."""
        line = json.dumps(record, default=str, sort_keys=True)
        path = self._file_for(pipeline)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_all(self, pipeline: str) -> Iterator[dict[str, Any]]:
        """Yield every record for a pipeline in append order."""
        path = self._file_for(pipeline)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("skipping corrupt optimisation history line in %s", path)
                    continue

    def query(
        self,
        pipeline: str,
        *,
        record_type: str | None = None,
        signature: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching records, filtered by type and/or evidence signature."""
        results = []
        for record in self.read_all(pipeline):
            if record_type is not None and record.get("type") != record_type:
                continue
            if signature is not None and record.get("signature") != signature:
                continue
            results.append(record)
        return results
