import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ssat.airtable import Airtable  # noqa: E402
from ssat.fakes import Clock, build_demo, ss_values  # noqa: E402
from ssat.mapping import Config  # noqa: E402
from ssat.smartsuite import SmartSuite  # noqa: E402
from ssat.sync import Syncer  # noqa: E402


class Env:
    def __init__(self, cfg=None, setup=True):
        self.clock = Clock()
        self.at_fake, self.ss_fake = build_demo(ROOT / "samples", self.clock)
        self.cfg = cfg or Config.load(ROOT / "samples/mapping.example.json")
        self.slept = []
        http = {"sleep": self.slept.append}
        self.at = Airtable("pat_demo", self.at_fake.base_id, "Projects", transport=self.at_fake, **http)
        self.ss = SmartSuite("ss_demo_key", self.ss_fake.workspace_id, self.ss_fake.table_id,
                             transport=self.ss_fake, **http)
        self.lines = []
        self.syncer = Syncer(self.at, self.ss, self.cfg, log=self.lines.append)
        if setup:
            assert self.syncer.setup() == []

    def at_by_name(self, name):
        return next(r for r in self.at_fake.records.values() if r["fields"].get("Name") == name)

    def ss_of(self, at_rec):
        return self.ss_fake.records[at_rec["fields"]["SmartSuite ID"]]

    def ss_set(self, ss_id, row):
        self.ss_fake.ui_update(ss_id, ss_values(self.ss_fake, row))

    def plain_pairs(self):
        out = []
        for a in self.at_fake.records.values():
            s = self.ss_fake.records[a["fields"]["SmartSuite ID"]]
            out.append((self.cfg.at_plain(a), self.cfg.ss_plain(s)))
        return out


@pytest.fixture
def env():
    return Env()


@pytest.fixture
def synced():
    e = Env()
    e.syncer.run()
    e.lines.clear()
    return e


@pytest.fixture
def samples():
    return lambda name: json.loads((ROOT / f"samples/{name}.json").read_text())
