"""Create / drop PDBs through the API against the simulated CDB (40 PDBs PDBPP_001..040 exist)."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app

from .test_runs import items, scenario, wait

ADMIN = "Adm1n-UniquePw-31"
WALLET = "Wa11et-UniquePw-55"


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def con_uids(c):
    return {p["name"]: p["con_uid"] for p in c.get("/api/pdbs").json()["pdbs"]}


def plan(c, type_, params):
    r = c.post("/api/runs", json={"type": type_, "params": params, "dry_run": True})
    assert r.status_code == 200, r.text
    return r.json()["plan"]


def run(c, type_, params, secrets=None, expect=201):
    r = c.post("/api/runs", json={"type": type_, "params": params,
                                  "secrets": {"pdb_admin_password": ADMIN} if secrets is None else secrets})
    assert r.status_code == expect, r.text
    return wait(c, r.json()["run_id"]) if expect == 201 else r.json()


# ---- create ----------------------------------------------------------------------------------------

def test_create_new_range_without_wallet(client, sim_settings):
    before = con_uids(client)
    r = run(client, "create", {"first": 41, "last": 45, "use_wallet": False})
    assert r["status"] == "ok" and r["counts"] == {"ok": 5}
    assert r["summary"]["created"] == 5 and r["summary"]["existing"] == 0
    after = con_uids(client)
    assert set(after) - set(before) == {f"PDBPP_{i:03d}" for i in range(41, 46)}
    assert all(after[k] == before[k] for k in before)              # nothing else touched
    it = items(client, r["id"])["PDBPP_041"]
    assert it["detail"] == "created, opened on all instances"
    log = Path(r["run_dir"]) / "PDBPP_041-create.log"
    assert "Pluggable database created." in log.read_text()
    for f in [*Path(r["run_dir"]).rglob("*"), *sim_settings.db_path.parent.glob("orch.db*")]:
        if f.is_file():
            assert ADMIN.encode() not in f.read_bytes(), f


def test_create_single_instance_with_wallet(client):
    r = run(client, "create", {"first": 41, "last": 42, "use_wallet": True, "rac_cdb": False},
            {"pdb_admin_password": ADMIN, "wallet_password": WALLET})
    assert r["status"] == "ok"
    assert items(client, r["id"])["PDBPP_041"]["detail"] == "created, opened, state saved, TDE master key set"


def test_wallet_toggle_validation(client):
    r = client.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41},
                                       "secrets": {"pdb_admin_password": ADMIN}})
    assert r.status_code == 422 and r.json()["errors"] == {"use_wallet": "is required"}
    r = client.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41, "use_wallet": True},
                                       "secrets": {"pdb_admin_password": ADMIN}})
    assert r.json()["errors"] == {"secrets.wallet_password": "is required"}
    r = client.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41, "use_wallet": False},
                                       "secrets": {"pdb_admin_password": 'x"y'}})
    assert r.json()["errors"] == {"secrets.pdb_admin_password": 'must not contain a double quote (")'}


def test_wallet_failure_stops_at_first_pdb(sim_settings):
    cdb = json.loads(sim_settings.sim_scenario.read_text())["cdb"]
    scenario(sim_settings, cdb={**cdb, "wallet": False})
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, "create", {"first": 41, "last": 44, "use_wallet": True},
                {"pdb_admin_password": ADMIN, "wallet_password": WALLET})
        it = items(c, r["id"])
    assert r["status"] == "failed" and "ORA-28365" in r["message"]
    assert it["PDBPP_041"]["state"] == "failed" and it["PDBPP_041"]["exit_code"] == 28365 % 256
    assert {it[f"PDBPP_{i:03d}"]["state"] for i in (42, 43, 44)} == {"not_started"}


def test_create_failure_stops_sequence(sim_settings):
    scenario(sim_settings, pdb_scenarios={"PDBPP_043": {"create": "fail"}})
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, "create", {"first": 41, "last": 45, "use_wallet": False})
        it = items(c, r["id"])
    assert r["status"] == "failed" and "PDBPP_043" in r["message"] and "ORA-01119" in r["message"]
    assert [it[f"PDBPP_{i:03d}"]["state"] for i in range(41, 46)] == ["ok", "ok", "failed", "not_started",
                                                                         "not_started"]


def test_existing_pdbs_need_a_decision(client):
    before = con_uids(client)
    p = plan(client, "create", {"first": 38, "last": 42, "use_wallet": False})
    assert [e["name"] for e in p["existing"]] == ["PDBPP_038", "PDBPP_039", "PDBPP_040"]
    assert p["drop_token"] and "already exist" in p["warnings"][0]
    r = run(client, "create", {"first": 38, "last": 42, "use_wallet": False})
    assert r["status"] == "blocked" and "3 PDB(s) already exist" in r["message"]
    r = run(client, "create", {"first": 38, "last": 42, "use_wallet": False, "existing_action": "abort"})
    assert r["status"] == "blocked" and r["message"].startswith("Aborted without changes")
    assert con_uids(client) == before


def test_skip_existing(client):
    before = con_uids(client)
    r = run(client, "create", {"first": 38, "last": 42, "use_wallet": False, "existing_action": "skip"})
    it = items(client, r["id"])
    assert r["status"] == "ok" and r["summary"]["skipped"] == 3 and r["summary"]["created"] == 2
    assert it["PDBPP_038"]["state"] == "skipped" and it["PDBPP_038"]["detail"] == "already exists (skipped)"
    after = con_uids(client)
    assert after["PDBPP_038"] == before["PDBPP_038"] and "PDBPP_042" in after


def test_drop_and_recreate_needs_delete_and_token(client, sim_settings):
    before = con_uids(client)
    params = {"first": 38, "last": 42, "use_wallet": False, "existing_action": "drop"}
    r = client.post("/api/runs", json={"type": "create", "params": params,
                                       "secrets": {"pdb_admin_password": ADMIN}})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"confirm_text", "confirm_token"}
    r = client.post("/api/runs", json={"type": "create", "params": {**params, "confirm_text": "delete",
                                                                    "confirm_token": "0" * 16},
                                       "secrets": {"pdb_admin_password": ADMIN}})
    assert r.status_code == 422 and "confirm_text" in r.json()["errors"]
    # a well-formed but wrong token blocks the run before anything is dropped
    r = run(client, "create", {**params, "confirm_text": "DELETE", "confirm_token": "0" * 16})
    assert r["status"] == "blocked" and "changed since the plan" in r["message"]
    assert con_uids(client) == before

    token = plan(client, "create", params)["drop_token"]
    r = run(client, "create", {**params, "confirm_text": "DELETE", "confirm_token": token})
    assert r["status"] == "ok", r["message"]
    assert r["summary"] == {**r["summary"], "dropped": 3, "created": 5, "existing": 3}
    after = con_uids(client)
    assert all(after[f"PDBPP_{i:03d}"] != before[f"PDBPP_{i:03d}"] for i in (38, 39, 40))   # new incarnations
    assert after["PDBPP_037"] == before["PDBPP_037"]
    it = items(client, r["id"])
    assert it["PDBPP_038"]["detail"].startswith("dropped and recreated: created")
    assert it["PDBPP_041"]["detail"] == "created, opened on all instances"
    assert (Path(r["run_dir"]) / "PDBPP_038-drop.log").read_text().count("Pluggable database dropped.") == 1
    db = Database(sim_settings.db_path)
    audit = db.conn.execute("SELECT action, params_json FROM audit_log WHERE run_id = ? ORDER BY id",
                            (r["id"],)).fetchall()
    db.close()
    drop_entry = [json.loads(a["params_json"]) for a in audit if a["action"] == "drop_confirmed"][0]
    assert [x.split(":")[0] for x in drop_entry["pdbs"]] == ["PDBPP_038", "PDBPP_039", "PDBPP_040"]


def test_token_is_invalidated_when_the_set_changes(client):
    params = {"first": 39, "last": 41, "use_wallet": False, "existing_action": "drop", "confirm_text": "DELETE"}
    token = plan(client, "create", params)["drop_token"]                  # existing: 039, 040
    run(client, "create", {"first": 41, "last": 41, "use_wallet": False})  # now 041 exists too
    before = con_uids(client)
    r = run(client, "create", {**params, "confirm_token": token})
    assert r["status"] == "blocked"
    assert con_uids(client) == before


# ---- drop ---------------------------------------------------------------------------------------------

def test_drop_selected(client, sim_settings):
    db = Database(sim_settings.db_path)
    uid = con_uids(client)["PDBPP_010"]
    db.conn.execute("INSERT INTO soe_markers(pdb, con_uid, loaded_at) VALUES ('PDBPP_010', ?, 'x')", (uid,))
    db.close()
    params = {"pdbs": ["PDBPP_010", "PDBPP_011", "PDBPP_099"]}
    p = plan(client, "drop", params)
    assert [e["name"] for e in p["existing"]] == ["PDBPP_010", "PDBPP_011"] and p["missing"] == ["PDBPP_099"]
    assert "CLOSE IMMEDIATE INSTANCES=ALL" in p["sql_preview"]
    r = run(client, "drop", {**params, "confirm_text": "DELETE"}, secrets={}, expect=422)
    assert "confirm_token" in r["errors"]
    r = run(client, "drop", {**params, "confirm_text": "DELETE", "confirm_token": p["drop_token"]}, secrets={})
    it = items(client, r["id"])
    assert r["status"] == "ok" and r["summary"]["dropped"] == 2
    assert it["PDBPP_099"]["state"] == "skipped" and it["PDBPP_099"]["detail"] == "does not exist"
    assert "PDBPP_010" not in con_uids(client)
    db = Database(sim_settings.db_path)
    assert db.soe_markers("PDBPP") == {}
    db.close()


def test_cancel_during_create(sim_settings):
    scenario(sim_settings, delays={"create_pdb": 1.0})
    with TestClient(create_app(sim_settings)) as c:
        rid = c.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 50, "use_wallet": False},
                                        "secrets": {"pdb_admin_password": ADMIN}}).json()["run_id"]
        deadline = time.time() + 10
        while time.time() < deadline and c.get(f"/api/runs/{rid}").json()["counts"].get("running", 0) < 1:
            time.sleep(0.05)
        c.post(f"/api/runs/{rid}/cancel")
        r = wait(c, rid)
    assert r["status"] == "cancelled"
    assert r["counts"].get("cancelled") == 1 and r["counts"].get("not_started", 0) >= 8
