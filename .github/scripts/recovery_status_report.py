#!/usr/bin/env python3
"""Emit bounded, credential-free recovery status for workflow artefacts.

Read-only D1 query. A missing or unavailable coordinator is a hard failure when
coordination has explicitly been enabled; no fabricated success report.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from repo_mgmt.d1_coordinator import D1Coordinator
from repo_mgmt.recovery_report import recovery_summary


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    coordinator = D1Coordinator(
        os.environ["CLOUDFLARE_ACCOUNT_ID"],
        os.environ["RAMS_RECOVERY_D1_DATABASE_ID"],
        os.environ["RAMS_RECOVERY_D1_TOKEN"],
    )
    report = recovery_summary(coordinator, repository, limit=50)
    output = Path("rams-autonomous-recovery-report.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"RAMS durable recovery: {report['total_returned']} incidents; "
          f"{report['active']} active; {report['resolved']} resolved; "
          f"{report['retry_attempts']} retry attempts. "
          f"Report: {output}")


if __name__ == "__main__":
    main()
