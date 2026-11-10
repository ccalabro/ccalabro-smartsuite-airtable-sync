"""Field values: read each side into one plain form, compare, write back.

The plain form per mapping type:
  text, textarea, email, url -> str or None
  number                     -> canonical decimal string or None
  date                       -> "YYYY-MM-DD" or None
  datetime                   -> "YYYY-MM-DDTHH:MMZ" in UTC, to the minute, or None
  checkbox                   -> True or False
  select                     -> option label or None
  multiselect                -> sorted tuple of labels
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

TYPES = {
    #  type          SmartSuite field types                                    Airtable field types
    "text":        ({"textfield", "recordtitlefield", "textareafield"}, {"singleLineText", "multilineText", "email", "url", "phoneNumber"}),
    "textarea":    ({"textareafield", "textfield", "richtextareafield"}, {"multilineText", "singleLineText", "richText"}),
    "number":      ({"numberfield", "currencyfield"}, {"number", "currency"}),
    "date":        ({"datefield"}, {"date"}),
    "datetime":    ({"datefield"}, {"dateTime"}),
    "checkbox":    ({"yesnofield"}, {"checkbox"}),
    "select":      ({"singleselectfield", "statusfield"}, {"singleSelect"}),
    "multiselect": ({"multipleselectfield"}, {"multipleSelects"}),
    "email":       ({"emailfield"}, {"email", "singleLineText"}),
    "url":         ({"linkfield"}, {"url", "singleLineText"}),
}


class Unwritable(Exception):
    """A value the other side can't hold, like a select option it doesn't have."""


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minute(text: str | None) -> str | None:
    dt = parse_time(text)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _number(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value)).normalize()
    except InvalidOperation:
        return None
    return format(d, "f")


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).rstrip()
    return value or None


def _choices(field_def: dict) -> list[dict]:
    return ((field_def or {}).get("params") or {}).get("choices") or []


# -- Airtable -----------------------------------------------------------------
def from_airtable(kind: str, value):
    if kind in ("text", "textarea", "email", "url"):
        return _text(value)
    if kind == "number":
        return _number(value)
    if kind == "date":
        return value[:10] if value else None
    if kind == "datetime":
        return _minute(value)
    if kind == "checkbox":
        return bool(value)
    if kind == "select":
        return _text(value)
    if kind == "multiselect":
        return tuple(sorted(value or []))
    raise ValueError(kind)


def to_airtable(kind: str, plain):
    if kind == "number":
        if plain is None:
            return None
        d = Decimal(plain)
        return int(d) if d == d.to_integral_value() else float(d)
    if kind == "datetime":
        return plain.replace("Z", ":00.000Z") if plain else None
    if kind == "checkbox":
        return bool(plain)
    if kind == "multiselect":
        return list(plain)
    return plain


# -- SmartSuite ---------------------------------------------------------------
def from_smartsuite(kind: str, value, field_def: dict):
    ftype = (field_def or {}).get("field_type")
    if kind in ("text", "textarea"):
        if isinstance(value, dict):  # SmartDoc
            value = value.get("preview") or ""
        return _text(value)
    if kind in ("email", "url"):
        if isinstance(value, list):
            value = value[0] if value else None
        return _text(value)
    if kind == "number":
        return _number(value)
    if kind in ("date", "datetime"):
        date = (value or {}).get("date") if isinstance(value, dict) else None
        if not date:
            return None
        return date[:10] if kind == "date" else _minute(date)
    if kind == "checkbox":
        return bool(value)
    if kind == "select":
        if ftype == "statusfield":
            value = (value or {}).get("value") if isinstance(value, dict) else value
        if not value:
            return None
        label = next((c["label"] for c in _choices(field_def) if c["value"] == value), None)
        return _text(label if label is not None else value)
    if kind == "multiselect":
        labels = {c["value"]: c["label"] for c in _choices(field_def)}
        return tuple(sorted(labels.get(v, v) for v in value or []))
    raise ValueError(kind)


def to_smartsuite(kind: str, plain, field_def: dict):
    ftype = (field_def or {}).get("field_type")
    if kind in ("text", "textarea"):
        return plain or ""
    if kind in ("email", "url"):
        return [plain] if plain else []
    if kind == "number":
        return plain
    if kind == "date":
        return {"date": f"{plain}T00:00:00Z", "include_time": False} if plain else None
    if kind == "datetime":
        return {"date": plain.replace("Z", ":00Z"), "include_time": True} if plain else None
    if kind == "checkbox":
        return bool(plain)
    if kind == "select":
        if not plain:
            return {"value": ""} if ftype == "statusfield" else ""
        cid = _choice_id(field_def, plain)
        return {"value": cid} if ftype == "statusfield" else cid
    if kind == "multiselect":
        return [_choice_id(field_def, label) for label in plain]
    raise ValueError(kind)


def _choice_id(field_def: dict, label: str) -> str:
    for c in _choices(field_def):
        if c["label"] == label:
            return c["value"]
    raise Unwritable(f"SmartSuite field {field_def.get('label')!r} has no option {label!r}")
