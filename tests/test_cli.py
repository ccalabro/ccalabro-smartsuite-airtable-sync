import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env=None):
    return subprocess.run([sys.executable, str(ROOT / "sync.py"), *args], cwd=ROOT,
                          env=env or {**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=60)


def test_demo_runs_end_to_end():
    proc = run("demo")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = proc.stdout
    assert "8 created, 0 updated, 0 unchanged, 3 linked" in out
    assert "0 created, 0 updated, 11 unchanged, writes: 0" in out
    assert "SmartSuite newer by 2m" in out
    assert "has no option 'On hold'" in out
    assert "1 orphaned" in out and "1 deleted" in out
    assert "stopped before writing" in out


def test_demo_never_prints_keys():
    out = run("demo").stdout
    assert "pat_demo" not in out and "ss_demo_key" not in out


def test_missing_env_is_named():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIRTABLE_", "SMARTSUITE_"))}
    proc = run("sync", env=env)
    assert proc.returncode != 0
    assert "missing env vars: AIRTABLE_TOKEN" in proc.stderr
