"""Two way sync. Stateless: the link between records lives in both tables.

Each run reads both tables in full, pairs records by their link fields,
compares the mapped fields in one plain form, and writes only the difference.
When a pair differs, the record edited last wins (Airtable's Last modified
time field against SmartSuite's last_updated.on).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .airtable import Airtable
from .fields import parse_time
from .mapping import Config
from .smartsuite import SmartSuite


class SetupError(Exception):
    pass


class SafetyStop(Exception):
    pass


def _short(v) -> str:
    s = repr(v)
    return s if len(s) <= 40 else s[:37] + "..."


class Syncer:
    def __init__(self, at: Airtable, ss: SmartSuite, cfg: Config, log=print):
        self.at = at
        self.ss = ss
        self.cfg = cfg
        self.log = log

    # -- setup ---------------------------------------------------------------
    def setup(self, fix: bool = True) -> list[str]:
        """Check both schemas. With fix, add the two link fields if missing.
        It never changes a field that exists."""
        at_table = self.at.table_schema()
        at_problems = self.cfg.check_airtable(at_table)
        if fix and any(self.cfg.airtable_link_field in p and "setup adds it" in p for p in at_problems):
            self.at.create_field(at_table["id"], self.cfg.airtable_link_field)
            self.log(f"added Airtable field {self.cfg.airtable_link_field!r}")
            at_problems = self.cfg.check_airtable(self.at.table_schema())
        ss_problems = self.cfg.resolve(self.ss.table())
        if fix and self.cfg.ss_link_slug is None:
            self.ss.add_text_field(self.cfg.smartsuite_link_field)
            self.log(f"added SmartSuite field {self.cfg.smartsuite_link_field!r}")
            ss_problems = self.cfg.resolve(self.ss.table())
        return at_problems + ss_problems

    # -- sync ----------------------------------------------------------------
    def run(self, dry_run: bool = False, allow_mass_delete: bool = False) -> dict:
        problems = self.cfg.resolve(self.ss.table())
        if problems:
            raise SetupError("; ".join(problems) + ". Run `python3 sync.py setup`.")
        cfg = self.cfg
        at_recs = list(self.at.iter_records())
        ss_recs = list(self.ss.iter_records())
        at_by = {r["id"]: r for r in at_recs}
        ss_by = {r["id"]: r for r in ss_recs}

        # 1. pair by links, from either side
        pairs: dict[str, str] = {}
        ss_taken: dict[str, str] = {}
        for a in at_recs:
            link = cfg.at_link(a)
            if link in ss_by:
                if link in ss_taken:
                    self.log(f"warning: Airtable {ss_taken[link]} and {a['id']} both link SmartSuite {link}")
                    continue
                pairs[a["id"]] = link
                ss_taken[link] = a["id"]
        for s in ss_recs:
            link = cfg.ss_link(s)
            if link in at_by and link not in pairs and s["id"] not in ss_taken:
                pairs[link] = s["id"]
                ss_taken[s["id"]] = link

        at_new = [a for a in at_recs if a["id"] not in pairs and not cfg.at_link(a)]
        ss_new = [s for s in ss_recs if s["id"] not in ss_taken and not cfg.ss_link(s)]
        at_orphans = [a for a in at_recs if a["id"] not in pairs and cfg.at_link(a)]
        ss_orphans = [s for s in ss_recs if s["id"] not in ss_taken and cfg.ss_link(s)]

        # 2. first run: pair unlinked records that share a key, so an earlier
        # one way import doesn't turn into duplicates
        if cfg.match_unlinked_on:
            key = cfg.match_unlinked_on
            at_keys = defaultdict(list)
            for a in at_new:
                at_keys[cfg.at_plain(a)[key]].append(a)
            ss_keys = defaultdict(list)
            for s in ss_new:
                ss_keys[cfg.ss_plain(s)[key]].append(s)
            for k, alist in at_keys.items():
                if k and len(alist) == 1 and len(ss_keys.get(k, [])) == 1:
                    a, s = alist[0], ss_keys[k][0]
                    pairs[a["id"]] = s["id"]
                    ss_taken[s["id"]] = a["id"]
                    at_new.remove(a)
                    ss_new.remove(s)
                    self.log(f"matched   Airtable {a['id']} = SmartSuite {s['id']} on {key} {_short(k)}")

        counts = {"created": 0, "updated": 0, "unchanged": 0, "linked": 0, "orphaned": 0, "deleted": 0,
                  "warnings": 0}
        at_updates: dict[str, dict] = defaultdict(dict)
        ss_updates: dict[str, dict] = defaultdict(dict)

        # 3. compare pairs
        for a_id, s_id in pairs.items():
            a, s = at_by[a_id], ss_by[s_id]
            pa, ps = cfg.at_plain(a), cfg.ss_plain(s)
            changed = [n for n in pa if pa[n] != ps[n]]
            relinked = False
            if cfg.at_link(a) != s_id:
                at_updates[a_id][cfg.airtable_link_field] = s_id
                relinked = True
            if cfg.ss_link(s) != a_id:
                ss_updates[s_id][cfg.ss_link_slug] = a_id
                relinked = True
            if not changed:
                counts["linked" if relinked else "unchanged"] += 1
                continue
            winner, why = self._winner(a, s)
            to_ss, to_at = {}, {}
            for n in changed:
                f = cfg._by_at(n)
                source = {"both": winner, "to_smartsuite": "airtable", "to_airtable": "smartsuite"}[f.direction]
                (to_ss if source == "airtable" else to_at)[n] = (pa if source == "airtable" else ps)[n]
            if to_ss:
                fields, errs = cfg.to_smartsuite_fields(to_ss)
                for e in errs:
                    counts["warnings"] += 1
                    self.log(f"warning   SmartSuite {s_id}: {e}, field left as is")
                if fields:
                    ss_updates[s_id].update(fields)
                    counts["updated"] += 1
                    self._log_change("SmartSuite", s_id, "Airtable", why, to_ss, ps)
                elif not relinked and not to_at:
                    counts["unchanged"] += 1
            if to_at:
                at_updates[a_id].update(cfg.to_airtable_fields(to_at))
                counts["updated"] += 1
                self._log_change("Airtable", a_id, "SmartSuite", why, to_at, pa)

        # 4. deletions
        if cfg.deletions == "mirror":
            n = len(at_orphans) + len(ss_orphans)
            if n > cfg.max_deletes and not allow_mass_delete:
                raise SafetyStop(f"{n} records would be deleted, more than max_deletes={cfg.max_deletes}. "
                                 "Check the table ids, then rerun with --allow-mass-delete if it's right.")
        for a in at_orphans:
            self._orphan("Airtable", a["id"], "SmartSuite", cfg.at_link(a), counts)
        for s in ss_orphans:
            self._orphan("SmartSuite", s["id"], "Airtable", cfg.ss_link(s), counts)

        for a in at_new:
            self.log(f"create    SmartSuite record from Airtable {a['id']}  {_short(self._title(cfg.at_plain(a)))}")
        for s in ss_new:
            self.log(f"create    Airtable record from SmartSuite {s['id']}  {_short(self._title(cfg.ss_plain(s)))}")
        counts["created"] = len(at_new) + len(ss_new)
        if dry_run:
            self.log("dry run, nothing written")
            return counts

        # 5. write: creates first, then link write backs and updates, then deletes
        if at_new:
            items = []
            for a in at_new:
                fields, errs = cfg.to_smartsuite_fields(cfg.at_plain(a))
                for e in errs:
                    counts["warnings"] += 1
                    self.log(f"warning   new SmartSuite record from {a['id']}: {e}")
                items.append({**fields, cfg.ss_link_slug: a["id"]})
            for rec in self.ss.create(items):
                at_updates[rec[cfg.ss_link_slug]][cfg.airtable_link_field] = rec["id"]
        if ss_new:
            items = []
            for s in ss_new:
                fields = {k: v for k, v in cfg.to_airtable_fields(cfg.ss_plain(s)).items()
                          if v not in (None, [], False)}
                items.append({**fields, cfg.airtable_link_field: s["id"]})
            for rec in self.at.create(items):
                ss_updates[rec["fields"][cfg.airtable_link_field]][cfg.ss_link_slug] = rec["id"]
        if ss_updates:
            sent = [{"id": k, **v} for k, v in ss_updates.items()]
            got = {r["id"]: r for r in self.ss.update(sent)}
            for item in sent:
                if item["id"] not in got:
                    counts["warnings"] += 1
                    self.log(f"warning   SmartSuite skipped update for {item['id']} (record gone?)")
                else:
                    self._check_echo(item, got[item["id"]], counts)
        if at_updates:
            self.at.update([{"id": k, "fields": v} for k, v in at_updates.items()])
        if cfg.deletions == "mirror":
            if at_orphans:
                self.at.delete([a["id"] for a in at_orphans])
            if ss_orphans:
                self.ss.delete([s["id"] for s in ss_orphans])
        return counts

    # -- helpers -------------------------------------------------------------
    def _winner(self, a: dict, s: dict) -> tuple[str, str]:
        ta, ts = parse_time(self.cfg.at_modified(a)), parse_time(self.cfg.ss_modified(s))
        if ta and ts and ta != ts:
            gap = _gap(ta, ts)
            return ("airtable", f"Airtable newer by {gap}") if ta > ts else ("smartsuite", f"SmartSuite newer by {gap}")
        return self.cfg.tie_winner, f"same time, {self.cfg.tie_winner} wins ties"

    def _orphan(self, side: str, rid: str, other: str, other_id: str, counts: dict) -> None:
        if self.cfg.deletions == "mirror":
            counts["deleted"] += 1
            self.log(f"delete    {side} {rid} (its {other} record {other_id} was deleted)")
        else:
            counts["orphaned"] += 1
            self.log(f"orphaned  {side} {rid} (its {other} record {other_id} is gone), left alone")

    def _log_change(self, side, rid, source, why, new: dict, old: dict) -> None:
        parts = [f"{n}: {_short(old[n])} -> {_short(v)}" for n, v in list(new.items())[:3]]
        more = ", ..." if len(new) > 3 else ""
        self.log(f"updated   {side} {rid} from {source} ({why})  " + ", ".join(parts) + more)

    def _title(self, plain: dict):
        return plain.get(self.cfg.fields[0].airtable)

    def _check_echo(self, sent: dict, got: dict, counts: dict) -> None:
        """Warn when SmartSuite stores something other than what was sent,
        because that field would flip back and forth on every run."""
        cfg = self.cfg
        back = cfg.ss_plain(got)
        for f in cfg.fields:
            if f.slug in sent:
                from .fields import from_smartsuite
                want = from_smartsuite(f.type, sent[f.slug], f.ss_def)
                if back[f.airtable] != want:
                    counts["warnings"] += 1
                    self.log(f"warning   SmartSuite {got['id']} {f.smartsuite!r} came back as "
                             f"{_short(back[f.airtable])}, not {_short(want)}")


def _gap(a: datetime, b: datetime) -> str:
    secs = abs((a - b).total_seconds())
    if secs < 120:
        return f"{secs:.0f}s"
    if secs < 7200:
        return f"{secs / 60:.0f}m"
    if secs < 172800:
        return f"{secs / 3600:.0f}h"
    return f"{secs / 86400:.0f}d"
