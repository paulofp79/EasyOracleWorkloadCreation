"""Load SOE end-to-end against the simulated CDB (PDBPP_001..040; 017 fails to load; 029 is MOUNTED)."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app

from .test_runs import items, scenario, wait

SOE = "S0e-UniqueLoadPw-19"
SYS = "Sys-UniqueLoadPw-83"
SECRETS = {"soe_password": SOE, "sys_password": SYS}


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def load(c, params, secrets=SECRETS, retry_of=None, expect=201):
    body = {"type": "load", "params": params, "secrets": secrets}
    if retry_of:
        body["retry_of"] = retry_of
    r = c.post("/api/runs", json=body)
    assert r.status_code == expect, r.text
    return wait(c, r.json()["run_id"]) if expect == 201 else r.json()


def plan(c, params):
    r = c.post("/api/runs", json={"type": "load", "params": params, "dry_run": True})
    assert r.status_code == 200, r.text
    return r.json()["plan"]


def markers(settings):
    db = Database(settings.db_path)
    try:
        return db.soe_markers("PDBPP")
    finally:
        db.close()


def test_load_records_markers_and_logs(client, sim_settings):
    r = load(client, {"first": 1, "last": 4, "parallel": 2})
    assert r["status"] == "ok" and r["counts"] == {"ok": 4} and r["summary"]["loaded"] == 4
    inv = {p["name"]: p for p in client.get("/api/pdbs").json()["pdbs"]}
    m = markers(sim_settings)
    assert set(m) == {"PDBPP_001", "PDBPP_002", "PDBPP_003", "PDBPP_004"}
    assert m["PDBPP_001"]["con_uid"] == inv["PDBPP_001"]["con_uid"] and m["PDBPP_001"]["run_id"] == r["id"]
    assert inv["PDBPP_001"]["soe_loaded"] is not None
    run_dir = Path(r["run_dir"])
    assert "Grant succeeded." in (run_dir / "PDBPP_001-soe-setup.log").read_text()
    assert "Schema Created" in (run_dir / "PDBPP_001-oewizard.log").read_text()
    for f in [*run_dir.rglob("*"), *sim_settings.db_path.parent.glob("orch.db*")]:
        if f.is_file():
            data = f.read_bytes()
            assert SOE.encode() not in data and SYS.encode() not in data, f


def test_preflight_blocks_missing_and_not_open(client, sim_settings):
    p = plan(client, {"first": 27, "last": 42})
    assert p["blocked"] and p["missing"] == ["PDBPP_041", "PDBPP_042"] and p["not_open"] == ["PDBPP_029"]
    r = load(client, {"first": 27, "last": 42})
    assert r["status"] == "blocked"
    assert "PDBs that do not exist (2): PDBPP_041 PDBPP_042" in r["message"]
    assert "PDBs not open READ WRITE (1): PDBPP_029" in r["message"]
    assert r["counts"] == {"not_started": 16}
    assert markers(sim_settings) == {}


def test_dry_run_shows_plan_and_masked_command(client):
    load(client, {"first": 1, "last": 2})
    p = plan(client, {"first": 1, "last": 5, "parallel": 3})
    assert p["already_loaded"] == ["PDBPP_001", "PDBPP_002"] and p["to_load"] == ["PDBPP_003", "PDBPP_004", "PDBPP_005"]
    assert p["command_preview"].count("'********'") == 2 and "-cs //sim-scan.example.com:1521/pdbpp_003" in p["command_preview"]
    assert p["equivalent_command"].startswith("SOE_PASSWORD='********' DBA_PASSWORD='********' create_swingbench.sh")
    assert "-n 3" in p["equivalent_command"]


def test_sliding_window_never_exceeds_parallel(sim_settings):
    scenario(sim_settings, delays={"oewizard": 0.5})
    with TestClient(create_app(sim_settings)) as c:
        r = load(c, {"first": 1, "last": 10, "parallel": 3})
    assert r["status"] == "ok" and r["summary"]["peak_running"] == 3


def test_failure_stops_new_starts_but_running_finish(sim_settings):
    # 017 fails early; with parallel 2 the run must stop starting new PDBs after that.
    scenario(sim_settings, delays={"oewizard": 0.6})
    with TestClient(create_app(sim_settings)) as c:
        r = load(c, {"first": 14, "last": 28, "parallel": 2})
        it = items(c, r["id"])
    assert r["status"] == "failed"
    assert it["PDBPP_017"]["state"] == "failed" and "ORA-01652" in it["PDBPP_017"]["detail"]
    assert r["summary"]["failed"] == 1 and r["summary"]["not_started"] >= 8
    assert "Retry runs only those" in r["message"]
    states = {k: v["state"] for k, v in it.items()}
    assert "cancelled" not in states.values()                  # running loads were allowed to finish
    assert states["PDBPP_028"] == "not_started"


def test_keep_going_loads_the_rest(sim_settings):
    scenario(sim_settings, delays={"oewizard": 0.1})
    with TestClient(create_app(sim_settings)) as c:
        r = load(c, {"first": 14, "last": 22, "parallel": 2, "keep_going": True})
    assert r["status"] == "failed" and r["counts"] == {"ok": 8, "failed": 1} and r["summary"]["not_started"] == 0


def test_rerun_resumes_and_retry_targets_only_unfinished(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        first = load(c, {"first": 15, "last": 20, "parallel": 1})          # 015, 016 ok; 017 fails; rest not started
        assert first["counts"] == {"ok": 2, "failed": 1, "not_started": 3}
        again = load(c, {"first": 15, "last": 20, "parallel": 1, "keep_going": True})   # same request
        it = items(c, again["id"])
        assert it["PDBPP_015"]["state"] == "skipped" and "already loaded" in it["PDBPP_015"]["detail"]
        assert again["counts"] == {"skipped": 2, "failed": 1, "ok": 3}
    # fix the failing PDB, then "Retry failed" on the second run loads only 017
    scenario(sim_settings, pdb_scenarios={})
    with TestClient(create_app(sim_settings)) as c:
        retry = load(c, {}, retry_of=again["id"])
        assert retry["retry_of"] == again["id"] and set(items(c, retry["id"])) == {"PDBPP_017"}
        assert retry["status"] == "ok"
        nothing = c.post("/api/runs", json={"type": "load", "params": {}, "secrets": SECRETS, "retry_of": retry["id"]})
        assert nothing.status_code == 422 and "nothing to retry" in nothing.json()["errors"]["retry_of"]


def test_marker_keyed_by_con_uid_and_force(client, sim_settings):
    load(client, {"first": 1, "last": 3})
    db = Database(sim_settings.db_path)
    db.conn.execute("UPDATE soe_markers SET con_uid = '1' WHERE pdb = 'PDBPP_002'")   # PDB recreated elsewhere
    db.close()
    p = plan(client, {"first": 1, "last": 3})
    assert p["already_loaded"] == ["PDBPP_001", "PDBPP_003"] and p["to_load"] == ["PDBPP_002"]
    inv = {x["name"]: x for x in client.get("/api/pdbs").json()["pdbs"]}
    assert inv["PDBPP_002"]["soe_marker_stale"] and inv["PDBPP_002"]["soe_loaded"] is None
    assert plan(client, {"first": 1, "last": 3, "force": True})["to_load"] == ["PDBPP_001", "PDBPP_002", "PDBPP_003"]
    r = load(client, {"first": 1, "last": 3, "force": True})
    assert r["counts"] == {"ok": 3}


def test_secrets_required(client):
    r = client.post("/api/runs", json={"type": "load", "params": {"first": 1, "last": 2}, "secrets": {"soe_password": "a"}})
    assert r.status_code == 422 and r.json()["errors"] == {"secrets.sys_password": "is required"}


def test_cancel_stops_loads(sim_settings):
    scenario(sim_settings, delays={"oewizard": 30})
    with TestClient(create_app(sim_settings)) as c:
        rid = c.post("/api/runs", json={"type": "load", "params": {"first": 1, "last": 8, "parallel": 3},
                                        "secrets": SECRETS}).json()["run_id"]
        deadline = time.time() + 10
        while time.time() < deadline and c.get(f"/api/runs/{rid}").json()["counts"].get("running", 0) < 3:
            time.sleep(0.05)
        t0 = time.time()
        c.post(f"/api/runs/{rid}/cancel")
        r = wait(c, rid)
        assert time.time() - t0 < 8
    assert r["status"] == "cancelled" and r["counts"] == {"cancelled": 3, "not_started": 5}
    assert markers(sim_settings) == {}


def test_import_done_markers(client, sim_settings, tmp_path):
    inv = {p["name"]: p for p in client.get("/api/pdbs").json()["pdbs"]}
    d = tmp_path / "swingbench-load-logs"
    d.mkdir()
    (d / "PDBPP_005.done").write_text(inv["PDBPP_005"]["con_uid"] + "\n")
    (d / "PDBPP_006.done").write_text("123\n")                         # older incarnation
    (d / "notes.done").write_text("x")
    r = client.post("/api/pdbs/import-markers", json={"directory": str(d)}).json()
    assert r == {"imported": ["PDBPP_005"], "stale": ["PDBPP_006"], "ignored": ["notes.done"]}
    assert plan(client, {"first": 5, "last": 6})["already_loaded"] == ["PDBPP_005"]
