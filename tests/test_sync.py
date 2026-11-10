import pytest

from ssat.mapping import Config
from ssat.sync import SafetyStop, SetupError


def test_first_run_matches_imports_and_creates_the_rest(env):
    c = env.syncer.run()
    assert (c["created"], c["linked"]) == (8, 3)
    assert len(env.at_fake.records) == len(env.ss_fake.records) == 11
    for a, s in env.plain_pairs():
        assert a == s


def test_second_run_writes_nothing(synced):
    w = synced.at_fake.writes + synced.ss_fake.writes
    c = synced.syncer.run()
    assert (c["created"], c["updated"], c["unchanged"]) == (0, 0, 11)
    assert synced.at_fake.writes + synced.ss_fake.writes == w


def test_links_are_written_on_both_sides(synced):
    for a in synced.at_fake.records.values():
        s = synced.ss_of(a)
        assert s[synced.cfg.ss_link_slug] == a["id"]


def test_airtable_edit_flows_to_smartsuite(synced):
    rec = synced.at_by_name("Fleetly driver app")
    synced.clock.tick(minutes=5)
    synced.at_fake.ui_update(rec["id"], {"Status": "Done", "Tags": ["Mobile"]})
    c = synced.syncer.run()
    assert c["updated"] == 1
    s = synced.ss_of(rec)
    assert synced.cfg.ss_plain(s)["Status"] == "Done"
    assert synced.cfg.ss_plain(s)["Tags"] == ("Mobile",)
    assert synced.syncer.run()["updated"] == 0


def test_smartsuite_edit_flows_to_airtable(synced):
    rec = synced.at_by_name("Greenline retainer")
    synced.clock.tick(minutes=5)
    synced.ss_set(rec["fields"]["SmartSuite ID"], {"Budget": 3500, "Billable": False, "Notes": "Renewed"})
    synced.syncer.run()
    f = synced.at_fake.records[rec["id"]]["fields"]
    assert f["Budget"] == 3500 and "Billable" not in f and f["Notes"] == "Renewed"


def test_last_edit_wins_a_conflict(synced):
    rec = synced.at_by_name("Harbor Dental site refresh")
    synced.clock.tick(minutes=1)
    synced.ss_set(rec["fields"]["SmartSuite ID"], {"Notes": "SmartSuite first"})
    synced.clock.tick(minutes=3)
    synced.at_fake.ui_update(rec["id"], {"Notes": "Airtable later"})
    synced.syncer.run()
    assert synced.cfg.ss_plain(synced.ss_of(rec))["Notes"] == "Airtable later"
    assert any("Airtable newer by 3m" in l for l in synced.lines)


def test_tie_goes_to_the_configured_side(synced):
    rec = synced.at_by_name("Harbor Dental site refresh")
    synced.clock.tick(minutes=1)
    synced.ss_set(rec["fields"]["SmartSuite ID"], {"Notes": "from SmartSuite"})
    synced.at_fake.ui_update(rec["id"], {"Notes": "from Airtable"})  # same second
    synced.syncer.run()
    assert synced.at_fake.records[rec["id"]]["fields"]["Notes"] == "from SmartSuite"


def test_one_way_field_ignores_the_other_side(samples):
    from conftest import Env
    data = samples("mapping.example")
    for f in data["fields"]:
        if f["airtable"] == "Budget":
            f["direction"] = "to_airtable"
    import json, tempfile, os
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    open(path, "w").write(json.dumps(data))
    e = Env(cfg=Config.load(path))
    e.syncer.run()
    rec = e.at_by_name("Vendor security reviews")  # started in SmartSuite with a budget of 1500
    e.clock.tick(minutes=5)
    e.at_fake.ui_update(rec["id"], {"Budget": 1, "Notes": "edited in Airtable"})
    e.syncer.run()
    assert e.at_fake.records[rec["id"]]["fields"]["Budget"] == 1500  # SmartSuite owns Budget
    assert e.cfg.ss_plain(e.ss_of(rec))["Notes"] == "edited in Airtable"  # the rest is still two way


def test_unknown_option_warns_and_does_not_loop(synced):
    rec = synced.at_by_name("Internal wiki cleanup")
    synced.clock.tick(minutes=1)
    synced.at_fake.ui_update(rec["id"], {"Status": "On hold"})
    w = synced.ss_fake.writes
    c = synced.syncer.run()
    assert c["warnings"] == 1 and c["updated"] == 0
    assert synced.ss_fake.writes == w


def test_deleted_record_reported_by_default_then_mirrored(synced):
    rec = synced.at_by_name("Northwind analytics audit")
    ss_id = rec["fields"]["SmartSuite ID"]
    synced.at_fake.ui_delete(rec["id"])
    c = synced.syncer.run()
    assert c["orphaned"] == 1 and ss_id in synced.ss_fake.records
    synced.cfg.deletions = "mirror"
    c = synced.syncer.run()
    assert c["deleted"] == 1 and ss_id not in synced.ss_fake.records


def test_smartsuite_delete_mirrors_to_airtable(synced):
    synced.cfg.deletions = "mirror"
    rec = synced.at_by_name("Q1 hiring plan")
    synced.ss_fake.ui_delete(rec["fields"]["SmartSuite ID"])
    synced.syncer.run()
    assert rec["id"] not in synced.at_fake.records


def test_mass_delete_needs_a_flag(synced):
    synced.cfg.deletions = "mirror"
    for rid in list(synced.at_fake.records)[:6]:
        synced.at_fake.ui_delete(rid)
    before = dict(synced.ss_fake.records)
    with pytest.raises(SafetyStop):
        synced.syncer.run()
    assert synced.ss_fake.records.keys() == before.keys()
    assert synced.syncer.run(allow_mass_delete=True)["deleted"] == 6


def test_dry_run_writes_nothing(env):
    w = env.at_fake.writes + env.ss_fake.writes
    c = env.syncer.run(dry_run=True)
    assert c["created"] == 8
    assert env.at_fake.writes + env.ss_fake.writes == w


def test_lost_link_write_back_is_repaired(synced):
    rec = synced.at_by_name("Fleetly driver app")
    synced.at_fake.records[rec["id"]]["fields"].pop("SmartSuite ID")  # as if a crash hit mid run
    c = synced.syncer.run()
    assert c["linked"] == 1 and c["created"] == 0
    assert "SmartSuite ID" in synced.at_fake.records[rec["id"]]["fields"]


def test_new_records_after_first_run(synced):
    synced.clock.tick(minutes=1)
    synced.at_fake.ui_create({"Name": "New from Airtable", "Status": "Planned"})
    synced.ss_fake.ui_create({"title": "New from SmartSuite"})
    c = synced.syncer.run()
    assert c["created"] == 2
    assert synced.syncer.run()["unchanged"] == 13


def test_batches_respect_limits(env):
    for i in range(30):
        env.at_fake.ui_create({"Name": f"Bulk {i}", "Status": "Planned"})
    env.syncer.run()  # the fakes reject more than 10 (Airtable) or 25 (SmartSuite) per call
    assert len(env.ss_fake.records) == 6 + 8 + 30 - 3


def test_429_waits_30_seconds(env):
    env.ss_fake.rate_limit_next = 1
    env.syncer.run()
    assert 30.0 in env.slept


def test_missing_mapping_field_stops_before_writing(env):
    env.ss_fake.structure = [f for f in env.ss_fake.structure if f["label"] != "Budget"]
    with pytest.raises(SetupError) as e:
        env.syncer.run()
    assert "Budget" in str(e.value)
    assert env.ss_fake.writes == 0


def test_setup_check_reports_missing_modified_field():
    from conftest import Env
    e = Env(setup=False)
    e.at_fake.table["fields"] = [f for f in e.at_fake.table["fields"] if f["name"] != "Last Modified"]
    problems = e.syncer.setup(fix=False)
    assert any("Last modified time" in p for p in problems)
    assert any("SmartSuite ID" in p for p in problems)
    assert e.at_fake.writes == 0


def test_config_validation(samples, tmp_path):
    data = samples("mapping.example")
    data["fields"][0]["type"] = "colour"
    p = tmp_path / "m.json"
    p.write_text(__import__("json").dumps(data))
    with pytest.raises(ValueError):
        Config.load(p)
