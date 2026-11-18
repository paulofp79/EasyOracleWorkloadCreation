"""Increase SOE: sbutil -dup on one or many existing schemas, several at a time."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cmdbuild import display, sbutil_dup_argv
from app.main import create_app

from .test_runs import items, scenario, wait

PW = "Increase-Pw-1"
SYS = "Increase-Sys-2"


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def state(settings):
    return json.loads(Path(settings.sim_state).read_text())["pdbs"]


def plan(c, params, status=200):
    r = c.post("/api/runs", json={"type": "increase", "params": params, "dry_run": True})
    assert r.status_code == status, r.text
    return r.json()


def start(c, params, expect="ok", secrets=None):
    r = c.post("/api/runs", json={"type": "increase", "params": params, "secrets": secrets or {"soe_password": PW}})
    assert r.status_code == 201, r.text
    run = wait(c, r.json()["run_id"])
    assert run["status"] == expect, run["message"]
    return run


def test_command_is_the_sbutil_example():
    argv = sbutil_dup_argv("/home/oracle/swingbench/bin/sbutil", "//scan3.example.com/pdbsantand.example.com",
                           "soe", "PaSiMa#2023#", 32, 2)
    assert argv == ["/home/oracle/swingbench/bin/sbutil", "-u", "soe", "-p", "PaSiMa#2023#", "-cs",
                    "//scan3.example.com/pdbsantand.example.com", "-soe", "-parallel", "32", "-dup", "2"]
    assert "PaSiMa" not in display(argv) and "-p '********'" in display(argv)


def test_one_schema_in_a_range(client, sim_settings):
    p = plan(client, {"first": 1, "last": 3, "schemas": [""], "dup": 2, "sbutil_parallel": 32})["plan"]
    assert p["pdbs"] == ["PDBPP_001", "PDBPP_002", "PDBPP_003"] and p["schemas"] == ["SOE"] and not p["blocked"]
    assert p["command_preview"].endswith("-u soe -p '********' -cs //sim-scan.example.com:1521/pdbpp_001 "
                                         "-soe -parallel 32 -dup 2")
    run = start(client, {"first": 1, "last": 2, "schemas": [""], "dup": 2, "sbutil_parallel": 32})
    it = items(client, run["id"])
    assert set(it) == {"PDBPP_001", "PDBPP_002"}
    assert it["PDBPP_001"]["detail"] == "SOE increased (sbutil -dup 2)" and it["PDBPP_001"]["check_status"] == "OK"
    assert run["summary"]["increased"] == 2
    log = (Path(run["run_dir"]) / "PDBPP_001-sbutil.log").read_text()
    assert "Completed Run." in log and PW not in log
    s = state(sim_settings)
    assert s["PDBPP_001"]["soe_dup"] == {"SOE": 2} and "soe_dup" not in s["PDBPP_003"]
    files = client.get(f"/api/runs/{run['id']}/items/PDBPP_001/log").json()["files"]
    assert [f["label"] for f in files] == ["sbutil", "schema check"] and all(f["exists"] for f in files)


def test_many_schemas_in_parallel(client, sim_settings):
    # a second schema in PDB 4 and 5
    r = client.post("/api/runs", json={"type": "load", "params": {"first": 4, "last": 5, "schema_prefix": "B1",
                                                                  "tablespace": "B1_SOETBS"},
                                       "secrets": {"soe_password": PW, "sys_password": SYS}})
    assert wait(client, r.json()["run_id"])["status"] == "ok"
    params = {"first": 4, "last": 5, "schemas": ["", "b1", "B1"], "dup": 3, "parallel": 4}
    p = plan(client, params)["plan"]
    assert p["pdbs"] == ["PDBPP_004:SOE", "PDBPP_004:B1_SOE", "PDBPP_005:SOE", "PDBPP_005:B1_SOE"]
    assert p["count"] == 4 and p["pdb_count"] == 2 and p["schemas"] == ["SOE", "B1_SOE"]
    assert p["equivalent_command"].startswith("# 4 schema(s) (SOE, B1_SOE) in 2 PDB(s), 4 at a time")
    run = start(client, params)
    assert run["summary"]["increased"] == 4
    s = state(sim_settings)
    assert s["PDBPP_004"]["soe_dup"] == {"SOE": 3, "B1_SOE": 3}
    sb = (Path(run["run_dir"]) / "PDBPP_005-B1_SOE-sbutil.log").read_text()
    assert "Completed Run." in sb
    assert client.get(f"/api/runs/{run['id']}/items/PDBPP_005:B1_SOE/log").json()["lines"]
    # all four ran at the same time
    assert client.app.state.runs.contexts[run["id"]].peak_running == 4


def test_failures_keep_going_and_retry_only_failed(client, sim_settings):
    # 013: schema check finds no tables; 037: sbutil fails half way
    params = {"pdbs": ["PDBPP_012", "PDBPP_013", "PDBPP_037"], "schemas": [""], "dup": 1, "keep_going": True,
              "parallel": 2}
    run = start(client, params, expect="failed")
    it = items(client, run["id"])
    assert it["PDBPP_012"]["state"] == "ok"
    assert it["PDBPP_013"]["detail"].startswith("SOE check failed: SOE schema missing")
    assert it["PDBPP_037"]["detail"].startswith("sbutil failed: java.sql.SQLException: ORA-01653")
    assert {f["pdb"] for f in run["summary"]["failed_pdbs"]} == {"PDBPP_013", "PDBPP_037"}
    # no sbutil was started for the schema that failed the check
    assert not (Path(run["run_dir"]) / "PDBPP_013-sbutil.log").exists()

    r = client.post("/api/runs", json={"type": "increase", "retry_of": run["id"], "secrets": {"soe_password": PW}})
    assert r.status_code == 201, r.text
    retry = wait(client, r.json()["run_id"])
    assert set(items(client, retry["id"])) == {"PDBPP_013", "PDBPP_037"}
    assert retry["params"]["targets"] == ["PDBPP_013:SOE", "PDBPP_037:SOE"]
    assert state(sim_settings)["PDBPP_012"]["soe_dup"] == {"SOE": 1}          # not grown twice


def test_stop_after_first_failure(client, sim_settings):
    scenario(sim_settings, delays={"sbutil": 0.2})
    run = start(client, {"pdbs": ["PDBPP_037", "PDBPP_038", "PDBPP_039"], "schemas": [""], "dup": 1},
                expect="failed")
    assert run["summary"]["failed"] == 1 and run["summary"]["not_started"] == 2
    assert run["counts"].get("failed") == 1


def test_retry_of_one_schema_among_many(client):
    from app.ops.grow_soe import IncreaseSoe
    p = IncreaseSoe.retry_params({"schemas": ["", "B1"], "pdbs": ["PDBPP_001"]}, ["PDBPP_001:B1_SOE"])
    assert p["targets"] == ["PDBPP_001:B1_SOE"] and "pdbs" not in p
    p = IncreaseSoe.retry_params({"schemas": ["B1"]}, ["PDBPP_001"])
    assert p["targets"] == ["PDBPP_001:B1_SOE"]


def test_blocked_and_validation(client):
    p = plan(client, {"first": 28, "last": 30, "schemas": [""]})["plan"]
    assert p["blocked"] and p["not_open"] == ["PDBPP_029"]
    p = plan(client, {"first": 1, "last": 1, "schemas": ["NEW"]})["plan"]
    assert any("Not loaded by this tool (1): PDBPP_001:NEW_SOE" in w for w in p["warnings"])
    for bad, field in (({"schemas": []}, "schemas"), ({"schemas": ["1x"]}, "schemas"),
                       ({"schemas": [""], "dup": 0}, "dup"), ({"schemas": [""], "targets": ["OTHER_001:SOE"]}, "targets")):
        r = client.post("/api/runs", json={"type": "increase", "params": bad, "dry_run": True})
        assert r.status_code == 422 and field in r.json()["errors"], r.text
    # local CDB: no SYS password needed
    r = client.post("/api/runs", json={"type": "increase", "params": {"first": 1, "last": 1, "schemas": [""]},
                                       "secrets": {}})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"secrets.soe_password"}
