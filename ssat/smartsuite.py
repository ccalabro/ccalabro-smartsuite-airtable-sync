"""SmartSuite REST API, records and table structure.

From https://developers.smartsuite.com: `Authorization: Token <key>` plus an
`ACCOUNT-ID` header with the 8 character workspace id, base
https://app.smartsuite.com/api/v1, Tables are still called "applications" in
paths, list is a POST with `offset` and `limit` (max 1000), bulk create,
update, and delete take up to 25 records, and the limit is 5 requests per
second with a 30 second wait after a 429.
"""
from __future__ import annotations

import secrets
import string
from typing import Iterator

from .http import JsonClient, Transport

BASE_URL = "https://app.smartsuite.com/api/v1"
BATCH = 25
PAGE = 1000


class SmartSuite:
    def __init__(self, api_key: str, workspace_id: str, table_id: str, transport: Transport | None = None, **http):
        self.table_id = table_id
        self.http = JsonClient("SmartSuite", BASE_URL,
                               {"Authorization": f"Token {api_key}", "ACCOUNT-ID": workspace_id},
                               transport=transport, **http)

    @property
    def _path(self) -> str:
        return f"/applications/{self.table_id}"

    def table(self) -> dict:
        return self.http.request("GET", f"{self._path}/")

    def add_text_field(self, label: str) -> None:
        slug = "s" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(9))
        # SmartSuite wants the slug of the field the new one goes after, and
        # rejects null, so put it after the last field in the table.
        structure = self.table().get("structure") or []
        prev = structure[-1]["slug"] if structure else "title"
        self.http.request("POST", f"{self._path}/add_field/", body={
            "field": {"slug": slug, "label": label, "field_type": "textfield", "params": {}, "is_new": True},
            "field_position": {"prev_sibling_slug": prev},
            "auto_fill_structure_layout": True,
        })

    def iter_records(self) -> Iterator[dict]:
        offset = 0
        while True:
            page = self.http.request("POST", f"{self._path}/records/list/",
                                     params={"offset": offset, "limit": PAGE}, body={"sort": [], "filter": {}})
            items = page.get("items") or []
            yield from items
            offset += len(items)
            if not items or offset >= int(page.get("total") or 0):
                return

    def create(self, records: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(records), BATCH):
            out += self.http.request("POST", f"{self._path}/records/bulk/", body={"items": records[i:i + BATCH]})
        return out

    def update(self, records: list[dict]) -> list[dict]:
        """records carry their `id`. PATCH only touches the given fields. The API
        skips unknown ids without an error, so callers should check the result."""
        out = []
        for i in range(0, len(records), BATCH):
            out += self.http.request("PATCH", f"{self._path}/records/bulk/", body={"items": records[i:i + BATCH]})
        return out

    def delete(self, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self.http.request("PATCH", f"{self._path}/records/bulk_delete/", params={"fields": "id"},
                              body={"items": ids[i:i + BATCH]})
