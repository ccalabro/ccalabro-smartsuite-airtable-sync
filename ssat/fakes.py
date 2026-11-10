"""In-memory stand-ins for the Airtable and SmartSuite APIs.

They plug in as transports, so the real client code runs end to end in the
demo and the tests. Both keep a last modified time from a shared clock, leave
empty values out of responses the way the real APIs do, and enforce the
documented batch limits.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit


def _resp(payload, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode() if payload is not None else b""


class Clock:
    def __init__(self, start="2026-11-09T12:00:00+00:00"):
        self.now = datetime.fromisoformat(start)

    def tick(self, **kw):
        self.now += timedelta(**kw) if kw else timedelta(seconds=1)
        return self.now

    def airtable(self):
        return self.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def smartsuite(self):
        return self.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")[:-4] + "Z"


def _empty(v):
    return v is None or v == "" or v == [] or v is False


class FakeAirtable:
    def __init__(self, base_id, table_name, fields: list[dict], clock: Clock, token="pat_demo"):
        self.base_id = base_id
        self.table = {"id": "tblDemo00000001", "name": table_name, "primaryFieldId": "fld0",
                      "fields": [{"id": f"fld{i}", **copy.deepcopy(f)} for i, f in enumerate(fields)]}
        self.clock = clock
        self.token = token
        self.records: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.calls: list[str] = []
        self.writes = 0
        self.rate_limit_next = 0

    # helpers for the demo and tests: edit like a person in the UI
    def ui_create(self, fields):
        rid = f"rec{next(self._ids):014d}"
        self.records[rid] = {"id": rid, "createdTime": self.clock.airtable(), "fields": {}}
        self._set(rid, fields)
        return rid

    def ui_update(self, rid, fields):
        self._set(rid, fields)

    def ui_delete(self, rid):
        del self.records[rid]

    def _field(self, name):
        return next((f for f in self.table["fields"] if f["name"] == name), None)

    def _set(self, rid, fields, typecast=True):
        rec = self.records[rid]
        for name, v in fields.items():
            f = self._field(name)
            if f["type"] in ("singleSelect", "multipleSelects") and v:
                opts = f.setdefault("options", {}).setdefault("choices", [])
                for label in ([v] if isinstance(v, str) else v):
                    if label not in [c["name"] for c in opts]:
                        opts.append({"id": f"sel{len(opts)}", "name": label})
            if _empty(v):
                rec["fields"].pop(name, None)
            else:
                rec["fields"][name] = copy.deepcopy(v)
        mod = next((f for f in self.table["fields"] if f["type"] == "lastModifiedTime"), None)
        if mod:
            rec["fields"][mod["name"]] = self.clock.airtable()

    def _check(self, fields, typecast):
        for name, v in fields.items():
            f = self._field(name)
            if not f:
                return f'Unknown field name: "{name}"'
            if f["type"] in ("lastModifiedTime", "createdTime", "formula", "autoNumber"):
                return f'Field "{name}" cannot accept a value because the field is computed'
            if f["type"] in ("singleSelect", "multipleSelects") and v and not typecast:
                known = [c["name"] for c in (f.get("options") or {}).get("choices", [])]
                for label in ([v] if isinstance(v, str) else v):
                    if label not in known:
                        return f'Insufficient permissions to create new select option ""{label}""'
            if f["type"] == "number" and v is not None and not isinstance(v, (int, float)):
                return f'Field "{name}" cannot accept the provided value'
        return None

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        path = parts.path.removeprefix("/v0")
        self.calls.append(f"{method} {path}")
        if headers.get("Authorization") != f"Bearer {self.token}":
            return _resp({"error": {"type": "AUTHENTICATION_REQUIRED", "message": "Authentication required"}}, 401)
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return _resp({"errors": [{"error": "RATE_LIMIT_REACHED"}]}, 429)
        data = json.loads(body) if body else {}
        if path == f"/meta/bases/{self.base_id}/tables" and method == "GET":
            return _resp({"tables": [copy.deepcopy(self.table)]})
        if path == f"/meta/bases/{self.base_id}/tables/{self.table['id']}/fields" and method == "POST":
            f = {"id": f"fld{len(self.table['fields'])}", "name": data["name"], "type": data["type"]}
            self.table["fields"].append(f)
            return _resp(f)
        if unquote(path) not in (f"/{self.base_id}/{self.table['name']}", f"/{self.base_id}/{self.table['id']}"):
            return _resp({"error": {"type": "NOT_FOUND", "message": "Could not find table"}}, 404)
        q = parse_qs(parts.query)
        if method == "GET":
            rows = list(self.records.values())
            size = int(q.get("pageSize", ["100"])[0])
            start = int(q.get("offset", ["0"])[0])
            out = {"records": copy.deepcopy(rows[start:start + size])}
            if start + size < len(rows):
                out["offset"] = str(start + size)
            return _resp(out)
        if method in ("POST", "PATCH"):
            recs = data.get("records") or []
            if len(recs) > 10:
                return _resp({"error": {"type": "INVALID_RECORDS", "message": "max 10 records"}}, 422)
            for r in recs:
                err = self._check(r.get("fields") or {}, data.get("typecast"))
                if err:
                    return _resp({"error": {"type": "INVALID_VALUE_FOR_COLUMN", "message": err}}, 422)
                if method == "PATCH" and r.get("id") not in self.records:
                    return _resp({"error": {"type": "ROW_DOES_NOT_EXIST", "message": r.get("id")}}, 404)
            out = []
            for r in recs:
                if method == "POST":
                    rid = self.ui_create(r["fields"])
                else:
                    rid = r["id"]
                    self._set(rid, r["fields"])
                out.append(copy.deepcopy(self.records[rid]))
            self.writes += 1
            return _resp({"records": out})
        if method == "DELETE":
            ids = q.get("records[]", [])
            if len(ids) > 10:
                return _resp({"error": {"type": "INVALID_RECORDS", "message": "max 10 records"}}, 422)
            for rid in ids:
                self.records.pop(rid, None)
            self.writes += 1
            return _resp({"records": [{"id": r, "deleted": True} for r in ids]})
        return _resp({"error": {"type": "NOT_FOUND"}}, 404)


SYSTEM = {"first_created", "last_updated", "autonumber", "id", "application_id", "application_slug",
          "deleted_date", "deleted_by", "comments_count", "ranking", "followed_by"}


class FakeSmartSuite:
    def __init__(self, workspace_id, table_id, structure: list[dict], clock: Clock, api_key="ss_demo_key"):
        self.workspace_id = workspace_id
        self.table_id = table_id
        self.structure = copy.deepcopy(structure)
        self.clock = clock
        self.api_key = api_key
        self.records: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.calls: list[str] = []
        self.writes = 0
        self.rate_limit_next = 0

    def ui_create(self, values):
        rid = f"6740a4eb96b9e63d{next(self._ids):08x}"
        now = self.clock.smartsuite()
        self.records[rid] = {"id": rid, "application_id": self.table_id, "title": "",
                             "first_created": {"on": now, "by": "member1"}, "last_updated": {"on": now, "by": "member1"},
                             "autonumber": len(self.records) + 1,
                             "deleted_date": {"date": None, "include_time": False}, "deleted_by": None}
        self._set(rid, values)
        return rid

    def ui_update(self, rid, values):
        self._set(rid, values)

    def ui_delete(self, rid):
        del self.records[rid]

    def label_to_slug(self, label):
        return next(f["slug"] for f in self.structure if f["label"] == label)

    def choice(self, label, option):
        f = next(f for f in self.structure if f["label"] == label)
        return next(c["value"] for c in f["params"]["choices"] if c["label"] == option)

    def _set(self, rid, values):
        rec = self.records[rid]
        for slug, v in values.items():
            f = next(x for x in self.structure if x["slug"] == slug)
            if f["field_type"] in ("numberfield", "currencyfield") and v is not None:
                v = str(v)
            rec[slug] = copy.deepcopy(v)
        rec["last_updated"] = {"on": self.clock.smartsuite(), "by": "member1"}

    def _check(self, item):
        by_slug = {f["slug"]: f for f in self.structure}
        for slug, v in item.items():
            if slug == "id":
                continue
            f = by_slug.get(slug)
            if not f:
                return {slug: ["Unknown field"]}
            if slug in SYSTEM or f["field_type"] in ("autonumberfield", "lastupdatedfield", "formulafield"):
                return {slug: ["This field is read only"]}
            choices = [c["value"] for c in (f.get("params") or {}).get("choices") or []]
            if f["field_type"] == "singleselectfield" and v and v not in choices:
                return {slug: [f'"{v}" is not a valid choice.']}
            if f["field_type"] == "statusfield" and (v or {}).get("value") and v["value"] not in choices:
                return {slug: [f'"{v["value"]}" is not a valid choice.']}
            if f["field_type"] == "multipleselectfield" and any(x not in choices for x in v or []):
                return {slug: ["not a valid choice."]}
        return None

    def _public(self, rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if k in SYSTEM or not _empty(v)}

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        path = parts.path.removeprefix("/api/v1")
        self.calls.append(f"{method} {path}")
        if headers.get("Authorization") != f"Token {self.api_key}" or headers.get("ACCOUNT-ID") != self.workspace_id:
            return _resp({"detail": "Authentication credentials were not provided."}, 401)
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return _resp({"detail": "Request was throttled."}, 429)
        base = f"/applications/{self.table_id}"
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        data = json.loads(body) if body else {}
        if method == "GET" and path == base + "/":
            return _resp({"id": self.table_id, "name": "Projects", "structure": copy.deepcopy(self.structure)})
        if method == "POST" and path == base + "/add_field/":
            if "field_position" not in data:
                return _resp({"field_position": ["This field is required."]}, 400)
            self.structure.append(copy.deepcopy(data["field"]))
            return _resp(None)
        if method == "POST" and path == base + "/records/list/":
            rows = [self._public(r) for r in self.records.values()]
            offset, limit = int(q.get("offset", 0)), int(q.get("limit", 100))
            return _resp({"total": len(rows), "offset": offset, "limit": limit,
                          "items": rows[offset:offset + limit]})
        if path == base + "/records/bulk/" and method in ("POST", "PATCH"):
            items = data.get("items") or []
            if len(items) > 25:
                return _resp({"items": ["Ensure this field has no more than 25 elements."]}, 422)
            for it in items:
                err = self._check(it)
                if err:
                    return _resp(err, 422)
            out = []
            for it in items:
                vals = {k: v for k, v in it.items() if k != "id"}
                if method == "POST":
                    rid = self.ui_create(vals)
                elif it.get("id") in self.records:
                    rid = it["id"]
                    self._set(rid, vals)
                else:
                    continue  # documented: unknown ids are skipped without an error
                out.append(self._public(self.records[rid]))
            self.writes += 1
            return _resp(out, 201 if method == "POST" else 200)
        if method == "PATCH" and path == base + "/records/bulk_delete/":
            items = data.get("items") or []
            out = []
            for rid in items:
                if isinstance(rid, str) and rid in self.records:  # objects are ignored, as documented
                    del self.records[rid]
                    out.append({"id": rid, "deleted_date": {"date": self.clock.smartsuite(), "include_time": True}})
            self.writes += 1
            return _resp(out)
        return _resp({"detail": "Not found."}, 404)


def build_demo(samples_dir, clock: Clock):
    """Two fake tables filled from samples/, as if people had typed them."""
    from pathlib import Path
    d = Path(samples_dir)
    load = lambda n: json.loads((d / f"{n}.json").read_text())
    at = FakeAirtable("appDemo0000000001", "Projects", load("airtable_fields"), clock)
    ss = FakeSmartSuite("sv25cxf2", "67161745a504292e31e25ad5", load("smartsuite_structure"), clock)
    for row in load("airtable_records"):
        clock.tick(minutes=1)
        at.ui_create(row)
    for row in load("smartsuite_records"):
        clock.tick(minutes=1)
        ss.ui_create(ss_values(ss, row))
    return at, ss


def ss_values(ss: FakeSmartSuite, row: dict) -> dict:
    """SmartSuite values from {label: plain value}, in the API's stored shapes."""
    out = {}
    for label, v in row.items():
        f = next(x for x in ss.structure if x["label"] == label)
        t, slug = f["field_type"], f["slug"]
        if t == "statusfield":
            out[slug] = {"value": ss.choice(label, v), "updated_on": ss.clock.smartsuite()}
        elif t == "singleselectfield":
            out[slug] = ss.choice(label, v)
        elif t == "multipleselectfield":
            out[slug] = [ss.choice(label, x) for x in v]
        elif t in ("emailfield", "linkfield"):
            out[slug] = [v]
        elif t == "datefield":
            out[slug] = {"date": v if "T" in v else f"{v}T00:00:00Z", "include_time": "T" in v}
        else:
            out[slug] = v
    return out
