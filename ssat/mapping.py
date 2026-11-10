"""The field map from mapping.json, resolved against the real SmartSuite table."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .fields import TYPES, Unwritable, from_airtable, from_smartsuite, to_airtable, to_smartsuite


@dataclass
class FieldMap:
    airtable: str           # Airtable field name
    smartsuite: str         # SmartSuite field label
    type: str
    direction: str = "both"  # both, to_airtable, to_smartsuite
    slug: str | None = None  # filled in by resolve()
    ss_def: dict | None = None


@dataclass
class Config:
    fields: list[FieldMap]
    airtable_link_field: str = "SmartSuite ID"
    airtable_modified_field: str = "Last Modified"
    smartsuite_link_field: str = "Airtable ID"
    tie_winner: str = "smartsuite"
    deletions: str = "report"        # report or mirror
    match_unlinked_on: str | None = None
    max_deletes: int = 10
    ss_link_slug: str | None = field(default=None, repr=False)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        data = {k: v for k, v in json.loads(Path(path).read_text()).items() if not k.startswith("_")}
        data["fields"] = [FieldMap(**f) for f in data["fields"]]
        cfg = cls(**data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.tie_winner not in ("smartsuite", "airtable"):
            raise ValueError("tie_winner must be smartsuite or airtable")
        if self.deletions not in ("report", "mirror"):
            raise ValueError("deletions must be report or mirror")
        names = [f.airtable for f in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("each Airtable field can be mapped once")
        for f in self.fields:
            if f.type not in TYPES:
                raise ValueError(f"{f.airtable}: unknown type {f.type!r}, use one of {sorted(TYPES)}")
            if f.direction not in ("both", "to_airtable", "to_smartsuite"):
                raise ValueError(f"{f.airtable}: direction must be both, to_airtable, or to_smartsuite")
        if self.match_unlinked_on and self.match_unlinked_on not in names:
            raise ValueError("match_unlinked_on must be the Airtable name of a mapped field")

    # -- schema --------------------------------------------------------------
    def resolve(self, ss_table: dict) -> list[str]:
        """Attach SmartSuite slugs and field definitions. Returns problems."""
        by_label = {f["label"]: f for f in ss_table.get("structure") or []}
        problems = []
        for f in self.fields:
            d = by_label.get(f.smartsuite)
            if not d:
                problems.append(f"SmartSuite has no field labeled {f.smartsuite!r}")
                continue
            if d["field_type"] not in TYPES[f.type][0]:
                problems.append(f"SmartSuite field {f.smartsuite!r} is {d['field_type']}, "
                                f"which doesn't fit type {f.type!r}")
            f.slug, f.ss_def = d["slug"], d
        link = by_label.get(self.smartsuite_link_field)
        if not link:
            problems.append(f"SmartSuite has no field labeled {self.smartsuite_link_field!r} (setup adds it)")
        else:
            self.ss_link_slug = link["slug"]
        return problems

    def check_airtable(self, at_table: dict) -> list[str]:
        by_name = {f["name"]: f for f in at_table.get("fields") or []}
        problems = []
        for f in self.fields:
            d = by_name.get(f.airtable)
            if not d:
                problems.append(f"Airtable has no field {f.airtable!r}")
            elif d["type"] not in TYPES[f.type][1]:
                problems.append(f"Airtable field {f.airtable!r} is {d['type']}, which doesn't fit type {f.type!r}")
        mod = by_name.get(self.airtable_modified_field)
        if not mod:
            problems.append(f"Airtable has no field {self.airtable_modified_field!r}. Add a "
                            "'Last modified time' field in the Airtable UI.")
        elif mod["type"] not in ("lastModifiedTime", "formula"):
            problems.append(f"Airtable field {self.airtable_modified_field!r} must be a Last modified time field")
        if self.airtable_link_field not in by_name:
            problems.append(f"Airtable has no field {self.airtable_link_field!r} (setup adds it)")
        return problems

    # -- records -------------------------------------------------------------
    def at_plain(self, rec: dict) -> dict:
        fields = rec.get("fields") or {}
        return {f.airtable: from_airtable(f.type, fields.get(f.airtable)) for f in self.fields}

    def ss_plain(self, rec: dict) -> dict:
        return {f.airtable: from_smartsuite(f.type, rec.get(f.slug), f.ss_def) for f in self.fields}

    def at_link(self, rec: dict) -> str | None:
        return ((rec.get("fields") or {}).get(self.airtable_link_field) or "").strip() or None

    def ss_link(self, rec: dict) -> str | None:
        return (rec.get(self.ss_link_slug) or "").strip() or None

    def at_modified(self, rec: dict) -> str | None:
        return (rec.get("fields") or {}).get(self.airtable_modified_field) or rec.get("createdTime")

    @staticmethod
    def ss_modified(rec: dict) -> str | None:
        return ((rec.get("last_updated") or {}).get("on")) or ((rec.get("first_created") or {}).get("on"))

    def writable(self, name: str, target: str) -> bool:
        f = self._by_at(name)
        return f.direction == "both" or f.direction == f"to_{target}"

    def to_airtable_fields(self, plain: dict) -> dict:
        return {name: to_airtable(self._by_at(name).type, v) for name, v in plain.items()
                if self.writable(name, "airtable")}

    def to_smartsuite_fields(self, plain: dict) -> tuple[dict, list[str]]:
        out, problems = {}, []
        for name, v in plain.items():
            if not self.writable(name, "smartsuite"):
                continue
            f = self._by_at(name)
            try:
                out[f.slug] = to_smartsuite(f.type, v, f.ss_def)
            except Unwritable as e:
                problems.append(str(e))
        return out, problems

    def _by_at(self, name: str) -> FieldMap:
        return next(f for f in self.fields if f.airtable == name)
