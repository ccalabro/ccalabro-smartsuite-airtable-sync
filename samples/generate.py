#!/usr/bin/env python3
"""Rebuild the fake tables in samples/. All of it is made up."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

STATUSES = ["Planned", "In progress", "Blocked", "Done"]
TAGS = ["Web", "Mobile", "Data", "Retainer"]

airtable_fields = [
    {"name": "Name", "type": "singleLineText"},
    {"name": "Status", "type": "singleSelect", "options": {"choices": [{"id": f"sel{i}", "name": s} for i, s in enumerate(STATUSES)]}},
    {"name": "Owner", "type": "email"},
    {"name": "Budget", "type": "currency", "options": {"precision": 2, "symbol": "$"}},
    {"name": "Start", "type": "date", "options": {"dateFormat": {"name": "iso", "format": "YYYY-MM-DD"}}},
    {"name": "Kickoff call", "type": "dateTime", "options": {"timeZone": "utc"}},
    {"name": "Billable", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
    {"name": "Tags", "type": "multipleSelects", "options": {"choices": [{"id": f"tag{i}", "name": t} for i, t in enumerate(TAGS)]}},
    {"name": "Notes", "type": "multilineText"},
    {"name": "Website", "type": "url"},
    {"name": "Last Modified", "type": "lastModifiedTime", "options": {"isValid": True, "referencedFieldIds": None}},
]


def choices(labels, prefix):
    return [{"value": f"{prefix}{i}", "label": l, "value_color": "#0C41F3", "value_order": i} for i, l in enumerate(labels)]


smartsuite_structure = [
    {"slug": "title", "label": "Title", "field_type": "recordtitlefield", "params": {"required": True}},
    {"slug": "status", "label": "Status", "field_type": "statusfield", "params": {"choices": choices(STATUSES, "st")}},
    {"slug": "s1a2b3c4d5", "label": "Owner", "field_type": "emailfield", "params": {}},
    {"slug": "s2b3c4d5e6", "label": "Budget", "field_type": "currencyfield", "params": {}},
    {"slug": "s3c4d5e6f7", "label": "Start", "field_type": "datefield", "params": {}},
    {"slug": "s4d5e6f7a8", "label": "Kickoff call", "field_type": "datefield", "params": {}},
    {"slug": "s5e6f7a8b9", "label": "Billable", "field_type": "yesnofield", "params": {}},
    {"slug": "s6f7a8b9c0", "label": "Tags", "field_type": "multipleselectfield", "params": {"choices": choices(TAGS, "tg")}},
    {"slug": "s7a8b9c0d1", "label": "Notes", "field_type": "textareafield", "params": {}},
    {"slug": "s8b9c0d1e2", "label": "Website", "field_type": "linkfield", "params": {}},
    {"slug": "first_created", "label": "First Created", "field_type": "firstcreatedfield", "params": {}},
    {"slug": "last_updated", "label": "Last Updated", "field_type": "lastupdatedfield", "params": {}},
]

# Airtable rows, as a person would type them.
airtable_records = [
    {"Name": "Harbor Dental site refresh", "Status": "In progress", "Owner": "maya@example.com", "Budget": 12000,
     "Start": "2026-10-01", "Kickoff call": "2026-10-01T15:00:00.000Z", "Billable": True, "Tags": ["Web"],
     "Notes": "New booking flow.\nKeep the old blog URLs.", "Website": "https://harbor-dental.example.com"},
    {"Name": "Fleetly driver app", "Status": "Planned", "Owner": "sam@example.com", "Budget": 48000,
     "Start": "2026-12-01", "Billable": True, "Tags": ["Mobile", "Data"]},
    {"Name": "Quarterly metrics pipeline", "Status": "Blocked", "Owner": "li@example.com", "Budget": 9500.5,
     "Start": "2026-09-15", "Tags": ["Data"], "Notes": "Waiting on warehouse access."},
    {"Name": "Greenline retainer", "Status": "In progress", "Owner": "maya@example.com", "Budget": 3000,
     "Billable": True, "Tags": ["Retainer"]},
    {"Name": "Internal wiki cleanup", "Status": "Planned", "Owner": "sam@example.com"},
    # these three were imported into SmartSuite once, by hand
    {"Name": "Pine Street Bakery shop", "Status": "Done", "Owner": "li@example.com", "Budget": 6400,
     "Start": "2026-06-02", "Billable": True, "Tags": ["Web"], "Website": "https://pinestreet.example.com"},
    {"Name": "Atlas CRM migration", "Status": "In progress", "Owner": "maya@example.com", "Budget": 22000,
     "Start": "2026-08-20", "Kickoff call": "2026-08-20T17:30:00.000Z", "Billable": True, "Tags": ["Data"]},
    {"Name": "Northwind analytics audit", "Status": "Done", "Owner": "sam@example.com", "Budget": 4000,
     "Start": "2026-07-07", "Tags": ["Data"]},
]

# SmartSuite rows, by field label, values as a person would pick them.
smartsuite_records = [
    {"Title": "Pine Street Bakery shop", "Status": "Done", "Owner": "li@example.com", "Budget": 6400,
     "Start": "2026-06-02", "Billable": True, "Tags": ["Web"], "Website": "https://pinestreet.example.com"},
    {"Title": "Atlas CRM migration", "Status": "In progress", "Owner": "maya@example.com", "Budget": 22000,
     "Start": "2026-08-20", "Kickoff call": "2026-08-20T17:30:00Z", "Billable": True, "Tags": ["Data"]},
    {"Title": "Northwind analytics audit", "Status": "Done", "Owner": "sam@example.com", "Budget": 4000,
     "Start": "2026-07-07", "Tags": ["Data"]},
    {"Title": "Onboarding checklist", "Status": "In progress", "Owner": "li@example.com",
     "Notes": "Started in SmartSuite."},
    {"Title": "Q1 hiring plan", "Status": "Planned", "Owner": "maya@example.com", "Start": "2027-01-05"},
    {"Title": "Vendor security reviews", "Status": "Blocked", "Owner": "sam@example.com", "Budget": 1500,
     "Tags": ["Retainer"]},
]

mapping = {
    "_comment": "Left: Airtable field name. Right: SmartSuite field label. See the README for the types.",
    "fields": [
        {"airtable": "Name", "smartsuite": "Title", "type": "text"},
        {"airtable": "Status", "smartsuite": "Status", "type": "select"},
        {"airtable": "Owner", "smartsuite": "Owner", "type": "email"},
        {"airtable": "Budget", "smartsuite": "Budget", "type": "number"},
        {"airtable": "Start", "smartsuite": "Start", "type": "date"},
        {"airtable": "Kickoff call", "smartsuite": "Kickoff call", "type": "datetime"},
        {"airtable": "Billable", "smartsuite": "Billable", "type": "checkbox"},
        {"airtable": "Tags", "smartsuite": "Tags", "type": "multiselect"},
        {"airtable": "Notes", "smartsuite": "Notes", "type": "textarea"},
        {"airtable": "Website", "smartsuite": "Website", "type": "url"},
    ],
    "airtable_link_field": "SmartSuite ID",
    "airtable_modified_field": "Last Modified",
    "smartsuite_link_field": "Airtable ID",
    "tie_winner": "smartsuite",
    "deletions": "report",
    "match_unlinked_on": "Name",
    "max_deletes": 5,
}

for name, data in [("airtable_fields", airtable_fields), ("smartsuite_structure", smartsuite_structure),
                   ("airtable_records", airtable_records), ("smartsuite_records", smartsuite_records),
                   ("mapping.example", mapping)]:
    (HERE / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
print(f"{len(airtable_records)} Airtable rows, {len(smartsuite_records)} SmartSuite rows")
