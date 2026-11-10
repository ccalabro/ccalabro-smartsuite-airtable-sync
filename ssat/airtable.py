"""Airtable Web API, records and schema.

From https://airtable.com/developers/web/api: personal access token as a
Bearer token, 100 records per list page with an `offset` cursor, 10 records
per create, update, or delete call, `typecast` to accept select option names,
and 5 requests per second per base.
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import quote

from .http import JsonClient, Transport

BASE_URL = "https://api.airtable.com/v0"
BATCH = 10


class Airtable:
    def __init__(self, token: str, base_id: str, table: str, transport: Transport | None = None, **http):
        self.base_id = base_id
        self.table = table
        self.http = JsonClient("Airtable", BASE_URL, {"Authorization": f"Bearer {token}"},
                               transport=transport, **http)

    @property
    def _path(self) -> str:
        return f"/{self.base_id}/{quote(self.table, safe='')}"

    def tables(self) -> list[dict]:
        return self.http.request("GET", f"/meta/bases/{self.base_id}/tables")["tables"]

    def table_schema(self) -> dict:
        for t in self.tables():
            if self.table in (t["id"], t["name"]):
                return t
        raise LookupError(f"Airtable base has no table {self.table!r}")

    def create_field(self, table_id: str, name: str, ftype: str = "singleLineText") -> dict:
        return self.http.request("POST", f"/meta/bases/{self.base_id}/tables/{table_id}/fields",
                                 body={"name": name, "type": ftype})

    def iter_records(self) -> Iterator[dict]:
        offset = None
        while True:
            page = self.http.request("GET", self._path, params={"pageSize": 100, "offset": offset})
            yield from page.get("records") or []
            offset = page.get("offset")
            if not offset:
                return

    def create(self, fields_list: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(fields_list), BATCH):
            chunk = [{"fields": f} for f in fields_list[i:i + BATCH]]
            out += self.http.request("POST", self._path, body={"records": chunk, "typecast": True})["records"]
        return out

    def update(self, records: list[dict]) -> list[dict]:
        """records: [{"id": ..., "fields": {...}}]. PATCH only touches the given fields."""
        out = []
        for i in range(0, len(records), BATCH):
            chunk = records[i:i + BATCH]
            out += self.http.request("PATCH", self._path, body={"records": chunk, "typecast": True})["records"]
        return out

    def delete(self, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self.http.request("DELETE", self._path, params=[("records[]", x) for x in ids[i:i + BATCH]])
