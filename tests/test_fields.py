import pytest

from ssat.fields import Unwritable, from_airtable, from_smartsuite, to_airtable, to_smartsuite

STATUS = {"label": "Status", "field_type": "statusfield",
          "params": {"choices": [{"value": "st0", "label": "Planned"}, {"value": "st1", "label": "Done"}]}}
SELECT = {"label": "Kind", "field_type": "singleselectfield",
          "params": {"choices": [{"value": "a1", "label": "A"}, {"value": "b2", "label": "B"}]}}
MULTI = {"label": "Tags", "field_type": "multipleselectfield",
         "params": {"choices": [{"value": "t0", "label": "Web"}, {"value": "t1", "label": "Data"}]}}


def test_numbers_compare_as_numbers():
    assert from_airtable("number", 9500.5) == from_smartsuite("number", "9500.50", {}) == "9500.5"
    assert from_airtable("number", 3000) == from_smartsuite("number", "3000.0", {}) == "3000"
    assert to_airtable("number", "3000") == 3000 and to_airtable("number", "9500.5") == 9500.5
    assert from_smartsuite("number", None, {}) is None


def test_datetime_compares_to_the_minute():
    a = from_airtable("datetime", "2026-08-20T17:30:00.000Z")
    s = from_smartsuite("datetime", {"date": "2026-08-20T17:30:41.123000Z", "include_time": True}, {})
    assert a == s == "2026-08-20T17:30Z"
    assert to_airtable("datetime", a) == "2026-08-20T17:30:00.000Z"
    assert to_smartsuite("datetime", a, {}) == {"date": "2026-08-20T17:30:00Z", "include_time": True}


def test_date_only_round_trip():
    assert from_smartsuite("date", {"date": "2026-06-02T00:00:00Z", "include_time": False}, {}) == "2026-06-02"
    assert to_smartsuite("date", "2026-06-02", {}) == {"date": "2026-06-02T00:00:00Z", "include_time": False}
    assert from_airtable("date", "2026-06-02") == "2026-06-02"


def test_status_and_select_map_ids_to_labels():
    assert from_smartsuite("select", {"value": "st1", "updated_on": None}, STATUS) == "Done"
    assert to_smartsuite("select", "Done", STATUS) == {"value": "st1"}
    assert from_smartsuite("select", "b2", SELECT) == "B"
    assert to_smartsuite("select", "B", SELECT) == "b2"
    assert to_smartsuite("select", None, SELECT) == ""


def test_unknown_option_is_unwritable():
    with pytest.raises(Unwritable):
        to_smartsuite("select", "On hold", STATUS)


def test_multiselect_is_order_free():
    assert from_smartsuite("multiselect", ["t1", "t0"], MULTI) == from_airtable("multiselect", ["Web", "Data"])
    assert to_smartsuite("multiselect", ("Data", "Web"), MULTI) == ["t1", "t0"]


def test_missing_values_line_up():
    assert from_airtable("checkbox", None) is from_smartsuite("checkbox", None, {}) is False
    assert from_airtable("text", "") is from_smartsuite("text", None, {}) is None
    assert from_airtable("multiselect", None) == from_smartsuite("multiselect", None, MULTI) == ()
    assert from_smartsuite("email", ["a@x.com", "b@x.com"], {}) == "a@x.com"


def test_smartdoc_reads_preview_text():
    doc = {"data": {}, "html": "<p>hi</p>", "preview": "hi there "}
    assert from_smartsuite("textarea", doc, {"field_type": "richtextareafield"}) == "hi there"
