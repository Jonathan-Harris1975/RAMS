#!/usr/bin/env python3
"""Classify a Kilo webhook result without exposing the webhook credential."""

from __future__ import annotations

import json
import re
import sys


def classify_status(value: str) -> dict[str, str]:
    """Return a persisted, bounded-retry state and safe next action."""
    status = value.strip()
    if not re.fullmatch(r"\d{3}", status):
        status = "000"
    code = int(status)
    if 200 <= code < 300:
        return {"state": "accepted", "guidance": "Kilo accepted the handoff."}
    if 400 <= code < 500:
        return {
            "state": f"rejected-http-{status}",
            "guidance": (
                "Owner action required: verify this repository's KILO_REPAIR_TRIGGER_URL "
                "and Kilo trigger access, then inspect this run before one bounded retry. "
                "Do not expose or copy the secret URL."
            ),
        }
    if status == "000" or 500 <= code < 600:
        return {
            "state": f"retryable-http-{status}",
            "guidance": (
                "Handoff transport/provider failure. Verify the run and Kilo service state "
                "before one bounded retry; the pending incident remains correlated to this run."
            ),
        }
    return {
        "state": f"retryable-http-{status}",
        "guidance": "Unexpected redirect or response; owner must inspect the endpoint before one bounded retry.",
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: kilo_handoff_result.py HTTP_STATUS")
    print(json.dumps(classify_status(sys.argv[1]), sort_keys=True))
