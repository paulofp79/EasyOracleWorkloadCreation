"""Several Swingbench schemas per PDB: SOE plus <PREFIX>_SOE (e.g. BATCH1_SOE)."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cmdbuild import charbench_argv, oewizard_argv
from app.db import SCHEMA_VERSION, Database
from app.main import create_app
from app.naming import login_user, schema_user
from app.sqlbuild import check_sql, soe_setup_sql

from .test_runs import items, wait
from .test_workload import fast

SOE = "Schema-Prefix-Pw-1"
SYS = "Schema-Prefix-Sys-2"


def test_names():
    assert (schema_user(""), login_user("")) == ("SOE", "soe")          # unchanged: as the scripts write it
    assert (schema_user("batch1"), login_user("batch1")) == ("BATCH1_SOE", "BATCH1_SOE")
    assert len(schema_user("A" * 26)) == 30


def test_builders_take_the_user():
    assert 'GRANT DBA TO BATCH1_SOE IDENTIFIED BY "pw";' in soe_setup_sql("PDBPP_001", "pw", "BATCH1_SOE")
    assert 'CONNECT BATCH1_SOE/"pw"@//h:1/s' in check_sql("pw", "//h:1/s", "BATCH1_SOE")
    ow = oewizard_argv("/o", "//h:1/s", "pw", "0.1", 4, "sys", "B1TBS", "BATCH1_SOE")
    assert ow[ow.index("-u") + 1] == "BATCH1_SOE" and ow[ow.index("-ts") + 1] == "B1TBS"
    cb = charbench_argv("/c", "//h:1/s", "pw", "/x.xml", 8, "", "00:30", "/r.xml", "BATCH1_SOE")
    assert cb[cb.index("-u") + 1] == "BATCH1_SOE"
    # defaults stay byte-identical to the scripts (golden tests cover the full text)
    assert "GRANT DBA TO soe IDENTIFIED BY" in soe_setup_sql("PDBPP_001", "pw")


def test_migration_keeps_existing_markers(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE soe_markers (pdb TEXT PRIMARY KEY, con_uid TEXT NOT NULL, loaded_at TEXT NOT NULL,
                                  run_id TEXT, scale TEXT, tablespace TEXT);
        INSERT INTO soe_markers VALUES ('PDBPP_001', '123', '2026-01-01', 'r1', '0.1', 'SOETBS');
        CREATE TABLE last_checks (pdb TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT, instance TEXT,
                                  checked_at TEXT NOT NULL, run_id TEXT);
        INSERT INTO last_checks VALUES ('PDBPP_001', 'OK', 'i1', 'i1', '2026-01-01', 'r1');
        PRAGMA user_version = 2;
    """)
    con.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    m = db.soe_markers("PDBPP")["PDBPP_001"]
    assert m["schema_user"] == "SOE" and m["con_uid"] == "123" and m["tablespace"] == "SOETBS"
    db.set_soe_marker("PDBPP_001", "123", "r2", "0.1", "B1TBS", schema_user="BATCH1_SOE")
    assert [x["schema_user"] for x in db.soe_markers_all("PDBPP")["PDBPP_001"]] == ["BATCH1_SOE", "SOE"]
    assert db.last_checks("PDBPP")["PDBPP_001"]["schema_user"] is None
    db.close()
    Database(path).close()                                       # opening again is a no-op


@pytest.fixture
def client(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        yield c


def load(c, params, expect_status="ok"):
    r = c.post("/api/runs", json={"type": "load", "params": params,
                                  "secrets": {"soe_password": SOE, "sys_password": SYS}})
    assert r.status_code == 201, r.text
    run = wait(c, r.json()["run_id"])
    assert run["status"] == expect_status, run["message"]
    return run


def plan(c, type_, params):
    r = c.post("/api/runs", json={"type": type_, "params": params, "dry_run": True})
    assert r.status_code == 200, r.text
    return r.json()["plan"]


def test_second_schema_is_loaded_next_to_soe(client, sim_settings):
    load(client, {"first": 1, "last": 2})                                       # SOE
    p = plan(client, "load", {"first": 1, "last": 2, "schema_prefix": "batch1", "tablespace": "BATCH1_SOETBS"})
    assert p["schema_user"] == "BATCH1_SOE" and p["to_load"] == ["PDBPP_001", "PDBPP_002"]   # SOE does not count
    assert p["command_preview"].count("-u BATCH1_SOE") == 1 and "-ts BATCH1_SOETBS" in p["command_preview"]
    assert "# app: schema BATCH1_SOE (the script always loads SOE)" in p["equivalent_command"]
    assert not any("already holds" in w for w in p["warnings"])
    run = load(client, {"first": 1, "last": 2, "schema_prefix": "batch1", "tablespace": "BATCH1_SOETBS"})
    it = items(client, run["id"])
    assert it["PDBPP_001"]["detail"].startswith("BATCH1_SOE loaded")
    setup = (Path(run["run_dir"]) / "PDBPP_001-soe-setup.log").read_text()
    assert "Grant succeeded." in setup
    # re-running the same request skips both PDBs for this schema only
    again = load(client, {"first": 1, "last": 2, "schema_prefix": "BATCH1", "tablespace": "BATCH1_SOETBS"})
    assert again["counts"] == {"skipped": 2}
    inv = {x["name"]: x for x in client.get("/api/pdbs").json()["pdbs"]}
    assert [s["schema_user"] for s in inv["PDBPP_001"]["soe_schemas"]] == ["BATCH1_SOE", "SOE"]
    assert inv["PDBPP_001"]["soe_schemas"][0]["tablespace"] == "BATCH1_SOETBS"
    assert inv["PDBPP_003"]["soe_schemas"] == []
    # a third schema in the same tablespace gets a warning (still allowed)
    p = plan(client, "load", {"first": 1, "last": 2, "schema_prefix": "batch2", "tablespace": "BATCH1_SOETBS"})
    assert any("BATCH1_SOETBS already holds BATCH1_SOE in 2 PDB(s)" in w for w in p["warnings"])
    # the simulated database really has both schemas
    state = json.loads(Path(sim_settings.sim_state).read_text())
    assert state["pdbs"]["PDBPP_001"]["soe_schemas"] == ["SOE", "BATCH1_SOE"]


def test_check_and_workload_use_the_schema(client, sim_settings):
    load(client, {"first": 14, "last": 15, "schema_prefix": "BATCH1", "tablespace": "BATCH1_SOETBS"})
    for prefix, expect in (("BATCH1", "ok"), ("BATCH2", "failed")):
        r = client.post("/api/runs", json={"type": "check", "params": {"first": 14, "last": 15, "schema_prefix": prefix},
                                           "secrets": {"soe_password": SOE}})
        run = wait(client, r.json()["run_id"])
        assert run["status"] == expect
    it = items(client, run["id"])
    assert it["PDBPP_014"]["check_detail"] == "SOE schema missing or incomplete (0/4 core tables)"   # BATCH2_SOE
    inv = {x["name"]: x for x in client.get("/api/pdbs").json()["pdbs"]}
    assert inv["PDBPP_014"]["last_check"]["schema_user"] == "BATCH2_SOE"

    p = plan(client, "workload", {"first": 14, "last": 15, "schema_prefix": "batch1"})
    assert p["schema_user"] == "BATCH1_SOE" and "-u BATCH1_SOE" in p["command_preview"]
    r = client.post("/api/runs", json={"type": "workload", "params": {"first": 14, "last": 15, "schema_prefix": "batch1",
                                                                      "runtime": "00:00:01"},
                                       "secrets": {"soe_password": SOE}})
    assert wait(client, r.json()["run_id"])["status"] == "ok"
    rec = json.loads((Path(sim_settings.sim_state).parent / "charbench-args" / "PDBPP_014.json").read_text())
    assert rec["argv"][rec["argv"].index("-u") + 1] == "BATCH1_SOE"


def test_validation_and_drop_clears_all_schemas(client):
    r = client.post("/api/runs", json={"type": "load", "params": {"schema_prefix": "1batch"}, "dry_run": True})
    assert r.status_code == 422 and "schema_prefix" in r.json()["errors"]
    load(client, {"first": 3, "last": 3})
    load(client, {"first": 3, "last": 3, "schema_prefix": "B1", "tablespace": "B1_SOETBS"})
    p = plan(client, "drop", {"pdbs": ["PDBPP_003"]})
    r = client.post("/api/runs", json={"type": "drop", "params": {"pdbs": ["PDBPP_003"], "confirm_text": "DELETE",
                                                                  "confirm_token": p["drop_token"]}, "secrets": {}})
    assert wait(client, r.json()["run_id"])["status"] == "ok"
    db = client.app.state.db
    assert "PDBPP_003" not in db.soe_markers_all("PDBPP")
