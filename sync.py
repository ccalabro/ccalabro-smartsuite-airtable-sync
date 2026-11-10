#!/usr/bin/env python3
"""Two way record sync between SmartSuite and Airtable.

  python3 sync.py setup [--check]   check both tables, add the two link fields
  python3 sync.py sync [--dry-run]  one two way pass (alias: backfill)
  python3 sync.py watch             run sync every few minutes
  python3 sync.py demo              run everything on fake data, no keys

Config comes from env vars: AIRTABLE_TOKEN, AIRTABLE_BASE_ID, AIRTABLE_TABLE,
SMARTSUITE_API_KEY, SMARTSUITE_WORKSPACE_ID, SMARTSUITE_TABLE_ID, and
optionally MAPPING_FILE (default mapping.json). Values are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ssat.airtable import Airtable
from ssat.http import ApiError
from ssat.mapping import Config
from ssat.smartsuite import SmartSuite
from ssat.sync import SafetyStop, SetupError, Syncer

ROOT = Path(__file__).resolve().parent


def need(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit("missing env vars: " + ", ".join(missing))
    return {n: os.environ[n] for n in names}


def syncer_from_env(log=print) -> Syncer:
    env = need("AIRTABLE_TOKEN", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE",
               "SMARTSUITE_API_KEY", "SMARTSUITE_WORKSPACE_ID", "SMARTSUITE_TABLE_ID")
    mapping = Path(os.environ.get("MAPPING_FILE") or ROOT / "mapping.json")
    if not mapping.exists():
        sys.exit(f"{mapping.name} not found. Copy the example first: cp samples/mapping.example.json mapping.json")
    cfg = Config.load(mapping)
    at = Airtable(env["AIRTABLE_TOKEN"], env["AIRTABLE_BASE_ID"], env["AIRTABLE_TABLE"])
    ss = SmartSuite(env["SMARTSUITE_API_KEY"], env["SMARTSUITE_WORKSPACE_ID"], env["SMARTSUITE_TABLE_ID"])
    return Syncer(at, ss, cfg, log=log)


def summary(c: dict) -> str:
    out = f"{c['created']} created, {c['updated']} updated, {c['unchanged']} unchanged"
    for k in ("linked", "orphaned", "deleted", "warnings"):
        if c.get(k):
            out += f", {c[k]} {k}"
    return out


def cmd_setup(args) -> None:
    s = syncer_from_env()
    problems = s.setup(fix=not args.check)
    if problems:
        sys.exit("not ready:\n  " + "\n  ".join(problems))
    print("Both tables match mapping.json. Ready to sync.")


def cmd_sync(args) -> None:
    s = syncer_from_env()
    c = s.run(dry_run=args.dry_run, allow_mass_delete=args.allow_mass_delete)
    print(summary(c) + f"  (API calls: Airtable {s.at.http.calls}, SmartSuite {s.ss.http.calls})")


def cmd_watch(args) -> None:
    s = syncer_from_env()
    print(f"syncing every {args.every} minutes, Ctrl+C to stop")
    while True:
        try:
            print(time.strftime("%H:%M ") + summary(s.run()))
        except ApiError as e:
            print(f"{time.strftime('%H:%M')} error, will retry next round: {e}")
        try:
            time.sleep(args.every * 60)
        except KeyboardInterrupt:
            return


def cmd_demo(args) -> None:
    from ssat.fakes import Clock, FakeAirtable, build_demo, ss_values

    clock = Clock()
    at_fake, ss_fake = build_demo(ROOT / "samples", clock)
    cfg = Config.load(ROOT / "samples/mapping.example.json")
    fast = {"sleep": lambda s: None}
    at = Airtable("pat_demo", at_fake.base_id, "Projects", transport=at_fake, **fast)
    ss = SmartSuite("ss_demo_key", ss_fake.workspace_id, ss_fake.table_id, transport=ss_fake, **fast)
    log = lambda s: print("  " + s)
    s = Syncer(at, ss, cfg, log=log)
    by_name = lambda name: next(r for r in at_fake.records.values() if r["fields"].get("Name") == name)
    ss_by_title = lambda t: next(r for r in ss_fake.records.values() if r.get("title") == t)

    print("== 1. setup: check both tables and add the link fields")
    problems = s.setup()
    print("  problems: " + ("; ".join(problems) or "none"))

    print(f"\n== 2. first sync ({len(at_fake.records)} Airtable rows, {len(ss_fake.records)} SmartSuite rows,"
          " 3 of them imported by hand earlier)")
    at_fake.rate_limit_next = ss_fake.rate_limit_next = 1
    print("  " + summary(s.run()) + "  (one 429 on each side was retried)")

    print("\n== 3. sync again, nothing should change")
    w = at_fake.writes + ss_fake.writes
    c = s.run()
    print(f"  {summary(c)}, writes: {at_fake.writes + ss_fake.writes - w}")

    print("\n== 4. someone moves a project in Airtable, someone else edits a budget in SmartSuite")
    clock.tick(minutes=5)
    at_fake.ui_update(by_name("Fleetly driver app")["id"], {"Status": "In progress"})
    clock.tick(minutes=2)
    ss_fake.ui_update(ss_by_title("Vendor security reviews")["id"],
                      ss_values(ss_fake, {"Budget": 2250, "Status": "In progress"}))
    print("  " + summary(s.run()))

    print("\n== 5. both sides edit the same record, the later edit wins")
    rec = by_name("Harbor Dental site refresh")
    clock.tick(minutes=1)
    at_fake.ui_update(rec["id"], {"Notes": "Airtable note, typed first"})
    clock.tick(minutes=2)
    ss_fake.ui_update(rec["fields"]["SmartSuite ID"], ss_values(ss_fake, {"Notes": "SmartSuite note, typed later"}))
    print("  " + summary(s.run()))

    print("\n== 6. an Airtable option SmartSuite doesn't have")
    clock.tick(minutes=1)
    at_fake.ui_update(by_name("Internal wiki cleanup")["id"], {"Status": "On hold"})
    print("  " + summary(s.run()))
    at_fake.ui_update(by_name("Internal wiki cleanup")["id"], {"Status": "Planned"})

    print("\n== 7. a record deleted in Airtable")
    gone = by_name("Northwind analytics audit")
    at_fake.ui_delete(gone["id"])
    print("  default (report): " + summary(s.run()))
    cfg.deletions = "mirror"
    print("  deletions=mirror: " + summary(s.run()))

    print("\n== 8. safety stop: pointed at the wrong, empty Airtable table with deletions=mirror")
    empty = FakeAirtable(at_fake.base_id, "Projects", at_fake.table["fields"], clock)
    wrong = Syncer(Airtable("pat_demo", at_fake.base_id, "Projects", transport=empty, **fast), ss, cfg,
                   log=lambda _: None)
    try:
        wrong.run()
    except SafetyStop as e:
        print(f"  stopped before writing: {e}")
    cfg.deletions = "report"

    print("\n== 9. both tables now")
    rows = sorted(at_fake.records.values(), key=lambda r: r["fields"]["Name"])
    print(f"  {'Name':<28} {'Status':<12} {'Budget':>8}  SmartSuite status")
    for r in rows:
        f = r["fields"]
        srec = ss_fake.records[f["SmartSuite ID"]]
        sstatus = next(c["label"] for c in ss_fake.structure[1]["params"]["choices"]
                       if c["value"] == (srec.get("status") or {}).get("value"))
        print(f"  {f['Name']:<28} {f.get('Status', ''):<12} {f.get('Budget', ''):>8}  {sstatus}")
    print(f"  {len(at_fake.records)} Airtable rows, {len(ss_fake.records)} SmartSuite rows, all linked."
          " No calls to the real Airtable or SmartSuite")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="check both tables and add the link fields")
    s.add_argument("--check", action="store_true", help="only check, change nothing")
    s.set_defaults(fn=cmd_setup)
    for name in ("sync", "backfill"):
        s = sub.add_parser(name, help="one two way pass" if name == "sync" else "same as sync")
        s.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
        s.add_argument("--allow-mass-delete", action="store_true",
                       help="with deletions=mirror, allow more than max_deletes deletions")
        s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("watch", help="run sync every few minutes")
    s.add_argument("--every", type=float, default=60, help="minutes between runs (default 60)")
    s.set_defaults(fn=cmd_watch)
    sub.add_parser("demo", help="run on fake data, no keys needed").set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except (SetupError, SafetyStop, ValueError, LookupError, ApiError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
