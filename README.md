# SmartSuite and Airtable two way sync

I built this because the "2-way connection with AirTable" request on SmartSuite's feedback board has 83 votes and has been Planned since December 2022. SmartSuite already has a one way Airtable import. What people keep asking for is the ongoing part: a record edited in either tool shows up in the other, without paying for a Zapier or Make plan to do it.

This keeps one Airtable table and one SmartSuite table in step. It links records by id on both sides, copies new records across, copies edits in the right direction, and only writes what changed.

## What it does

1. `setup` checks both tables against `mapping.json` and adds two plain text link fields if they're missing: `SmartSuite ID` in Airtable and `Airtable ID` in SmartSuite. `setup --check` only checks.
2. `sync` does one two way pass. Running it twice writes nothing the second time. `--dry-run` shows what it would do. `backfill` is the same command.
3. `watch` runs `sync` every 60 minutes.
4. `demo` runs all of it on fake data, with no keys.

A few choices, and why:

* **The link lives in both tables.** There is no local database. Each record holds the other side's id, so you can move the tool to another machine, or lose it, and it picks up where it left off. If a run dies between creating a record and writing its id back, the next run repairs the link from the other side instead of making a duplicate.
* **The last edit wins.** When a linked pair differs, I compare Airtable's Last modified time field with SmartSuite's `last_updated.on`, and the newer record's values win for the fields that differ. On an exact tie, SmartSuite wins by default (`tie_winner`). This is per record, not per field. Per field merging would need a stored snapshot of the last sync, and I chose no local state for now. The log says who won and by how much.
* **Fields can be one way.** Set `"direction": "to_airtable"` or `"to_smartsuite"` on a field and that side owns it. The other side gets overwritten, blanks included.
* **Values are compared in one plain form.** Numbers compare as numbers (`"3000.0"` and `3000` are the same), date and time fields compare to the minute, multi selects ignore order, and an empty value is the same on both sides. I learned the minute rule the hard way on a Notion sync: without it, a tool keeps "updating" rows that didn't change.
* **First run won't duplicate an earlier import.** With `match_unlinked_on` set (the example uses `Name`), unlinked records with the same unique value on both sides are paired instead of copied. That covers people who already used SmartSuite's one way import.
* **Deletions are reported, not copied, by default.** If a linked record disappears, the run says `orphaned` and leaves its partner alone. Set `"deletions": "mirror"` to delete the partner too. Both tools keep deleted records in a trash for a while. Even in mirror mode, a run that would delete more than `max_deletes` records stops before writing anything, unless you add `--allow-mass-delete`. That guards against a wrong table id wiping the other side.
* **An option SmartSuite doesn't have is skipped with a warning.** Airtable can add a select option on the fly (`typecast`). SmartSuite's API wants the option id, and I didn't want to edit someone's field options without asking. Add the option in SmartSuite and the next run copies it.
* **It respects the rate limits.** Both APIs allow 5 requests per second and ask for a 30 second pause after a 429. The client spaces calls out, waits 30 seconds on a 429, and batches writes (10 records per Airtable call, 25 per SmartSuite call).
* **Secrets stay in env vars.** Nothing prints a key. Errors show the status and the API's message, never the headers.
* **Standard library only.** Pytest is the only dependency, pinned, and only for the tests.

## Supported field types

| `type` in mapping.json | Airtable | SmartSuite |
| --- | --- | --- |
| `text` | Single line text, Long text | Title, Text, Text Area |
| `textarea` | Long text, Rich text (as text) | Text Area, Text, SmartDoc (as plain text) |
| `number` | Number, Currency | Number, Currency |
| `date` | Date | Date (without time) |
| `datetime` | Date and time | Date (with time), compared to the minute |
| `checkbox` | Checkbox | Yes / No |
| `select` | Single select | Single Select, Status |
| `multiselect` | Multiple select | Multiple Select |
| `email` | Email | Email (first address) |
| `url` | URL | Link (first link) |

Linked records, attachments, people, and formulas aren't synced. A SmartDoc field is read as its plain text, so formatting written from Airtable is lost.

## Setup for a live test

You need Python 3.10 or newer, and two free accounts. Neither asks for a card.

**Airtable**

1. Sign up at [airtable.com](https://airtable.com). The Free plan includes the API. It allows 1,000 API calls per workspace per month and 1,000 records per base, which is plenty for a test.
2. Create a base with a table called `Projects`. Add the fields from `samples/airtable_fields.json`, or rename the left side of `mapping.json` to match a table you already have.
3. Add a field called `Last Modified` of type **Last modified time**, watching all fields. The API can't create that type, so this one is by hand.
4. Create a personal access token at [airtable.com/create/tokens](https://airtable.com/create/tokens) with the scopes `data.records:read`, `data.records:write`, `schema.bases:read`, and `schema.bases:write` (the last one only so `setup` can add the link field), and give it access to your base.
5. The base id starts with `app` and is in the base URL.

**SmartSuite**

6. Start the 14 day trial at [smartsuite.com](https://www.smartsuite.com). It says no card and no auto charge.
7. Create a Solution with a table that has fields labeled like the right side of `mapping.json` (Title, Status, Owner, Budget, Start, Kickoff call, Billable, Tags, Notes, Website). Give Status and Tags the same options as in Airtable.
8. Open your profile menu, then API Key, and copy it. The workspace id is the 8 characters after `app.smartsuite.com/` in the address bar. The table id is in the table's URL, and the Solution's generated API docs (from the Solution menu) list it too.

**Run it**

```
cp samples/mapping.example.json mapping.json
export AIRTABLE_TOKEN=...
export AIRTABLE_BASE_ID=app...
export AIRTABLE_TABLE=Projects
export SMARTSUITE_API_KEY=...
export SMARTSUITE_WORKSPACE_ID=...
export SMARTSUITE_TABLE_ID=...
python3 sync.py setup
python3 sync.py sync --dry-run
python3 sync.py sync
python3 sync.py sync          # should say 0 created, 0 updated, N unchanged
```

Then edit a record in each tool, wait a few seconds, and run `sync` again. The log shows which side won and why.

On the Airtable Free plan, keep `watch` at 60 minutes or more. A run with up to 100 records costs one Airtable call plus one call per 10 records written, and setup costs a few more. Hourly is about 750 calls a month.

## Demo run

```
python3 sync.py demo
```

It uses two fake tables built from `samples/`, a fake Airtable API and a fake SmartSuite API, both in memory, sharing a fake clock so last modified times are real. Trimmed output:

```
== 2. first sync (8 Airtable rows, 6 SmartSuite rows, 3 of them imported by hand earlier)
  matched   Airtable rec00000000000006 = SmartSuite 6740a4eb96b9e63d00000001 on Name 'Pine Street Bakery shop'
  create    SmartSuite record from Airtable rec00000000000001  'Harbor Dental site refresh'
  create    Airtable record from SmartSuite 6740a4eb96b9e63d00000004  'Onboarding checklist'
  8 created, 0 updated, 0 unchanged, 3 linked  (one 429 on each side was retried)

== 3. sync again, nothing should change
  0 created, 0 updated, 11 unchanged, writes: 0

== 4. someone moves a project in Airtable, someone else edits a budget in SmartSuite
  updated   SmartSuite 6740a4eb96b9e63d00000008 from Airtable (Airtable newer by 5m)  Status: 'Planned' -> 'In progress'
  updated   Airtable rec00000000000011 from SmartSuite (SmartSuite newer by 7m)  Status: 'Blocked' -> 'In progress', Budget: '1500' -> '2250'

== 5. both sides edit the same record, the later edit wins
  updated   Airtable rec00000000000001 from SmartSuite (SmartSuite newer by 2m)  Notes: 'Airtable note, typed first' -> 'SmartSuite note, typed later'

== 6. an Airtable option SmartSuite doesn't have
  warning   SmartSuite 6740a4eb96b9e63d0000000b: SmartSuite field 'Status' has no option 'On hold', field left as is

== 7. a record deleted in Airtable
  default (report): 0 created, 0 updated, 10 unchanged, 1 orphaned
  deletions=mirror: 0 created, 0 updated, 10 unchanged, 1 deleted

== 8. safety stop: pointed at the wrong, empty Airtable table with deletions=mirror
  stopped before writing: 10 records would be deleted, more than max_deletes=5. ...
```

To rebuild the samples, run `python3 samples/generate.py`.

To run the tests:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

The tests cover value conversion for every type, the first run match, idempotent reruns, edits in each direction, last edit wins, ties, one way fields, unknown options, both deletion modes, the mass delete guard, dry runs, link repair, batch limits, the 30 second backoff, setup checks, and the CLI. They mock every HTTP call.

## What is verified and what is assumed

Verified against the published docs:

* **SmartSuite** ([developers.smartsuite.com](https://developers.smartsuite.com), read November 9, 2026): `Authorization: Token` plus `ACCOUNT-ID`, base `https://app.smartsuite.com/api/v1`, `GET /applications/{table}/` for the field structure with choice ids and labels, `POST .../records/list/` with `offset` and `limit` (max 1000) and `total`, bulk create and `PATCH` update at `.../records/bulk/` (25 max, unknown ids skipped silently), bulk delete at `PATCH .../records/bulk_delete/` with bare id strings, `POST .../add_field/` with a required `field_position`, empty values left out of responses, `last_updated.on`, the value shapes for each field type, and 5 requests per second with a 30 second wait after a 429.
* **Airtable** ([airtable.com/developers/web/api](https://airtable.com/developers/web/api)): Bearer personal access token, `GET /v0/{base}/{table}` with `pageSize` 100 and `offset`, create and `PATCH` with up to 10 records and `typecast`, delete with `records[]`, `GET /v0/meta/bases/{base}/tables`, `POST .../fields`, the cell formats per field type, 5 requests per second per base with a 30 second wait after a 429, and the Free plan limit of 1,000 calls a month.

Assumed, and worth checking on the first live run:

1. **Clearing a select in SmartSuite.** I send `""` for a single select and `{"value": ""}` for a status. The docs show how to set them, not how to clear them.
2. **SmartDoc writes.** I send plain text to a SmartDoc field. The bulk add example does that and gets a document back, but the single record docs don't say.
3. **Bulk create returns the link field.** I match created records back by the link value I sent, not by position. That relies on the response including that field, which the doc examples show.
4. **Clock skew.** Last edit wins compares timestamps from two companies' servers. Both are UTC and should be close, but edits made within a few seconds of each other on both sides are a coin flip. The tie rule only covers exact ties.
5. **Hydrated responses.** I read without `hydrated=true` and map choice ids to labels myself, so `last_updated.by` stays an id.

## Limits

* This is not affiliated with SmartSuite or Airtable. It is a small helper I wrote from their public API docs.
* The demo and tests run against fakes built from the docs. I have not run this against a live SmartSuite workspace or a live Airtable base yet.
* It polls. Both tools have webhooks, and SmartSuite's (`webhooks.smartsuite.com`, with a List Events call) would make it near real time. I left that out of the first version because polling is easier to trust and to run.
* It reads both tables in full each run. That is fine up to a few thousand records. Past that it should filter on last modified time.
* Last edit wins is per record. If two people edit different fields of the same record between runs, the older edit is lost. A stored snapshot would fix that, and it's the first thing I'd add if this gets real use.
* One table pair per config file. Run it once per pair for more.
