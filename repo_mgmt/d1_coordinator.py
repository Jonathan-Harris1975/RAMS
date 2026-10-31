"""D1 transactional coordinator API client.

Use only from trusted runtime with a scoped D1 token. SQL must be a
single atomic conditional statement for claims; never SELECT then UPDATE.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

_UUID = re.compile(r"^[0-9a-f-]{36}$")
_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")


class D1CoordinationError(RuntimeError):
    pass


class D1Coordinator:
    def __init__(self, account_id: str, database_id: str, token: str) -> None:
        if not _ACCOUNT.fullmatch(account_id) or not _UUID.fullmatch(database_id):
            raise ValueError("invalid D1 resource identifiers")
        if not token or any(c.isspace() for c in token):
            raise ValueError("invalid D1 token")
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self._token = token

    def query(self, sql: str, params: list[str] | None = None) -> list[dict[str, Any]]:
        """Execute one parameterised statement and reject ambiguous results."""
        if not sql or ";" in sql.rstrip().rstrip(";") or len(sql) > 8192:
            raise ValueError("expected one bounded SQL statement")
        payload = json.dumps({"sql": sql, "params": params or []}).encode()
        request = urllib.request.Request(
            self._url, data=payload, method="POST",
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise D1CoordinationError("D1 coordination request failed or outcome uncertain") from exc
        if not data.get("success") or not isinstance(data.get("result"), list):
            raise D1CoordinationError("D1 coordination rejected the query")
        results = data["result"]
        if len(results) != 1 or not results[0].get("success"):
            raise D1CoordinationError("D1 coordination statement did not succeed")
        rows = results[0].get("results")
        if not isinstance(rows, list):
            raise D1CoordinationError("D1 returned invalid results")
        return rows
