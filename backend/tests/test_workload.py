"""Workload end-to-end against the simulated CDB (PDBPP_001..040).

Scenarios: 003 ORA-12514, 007 hang, 011 wrong container, 013 no schema at check time;
019 prints ORA-00942 during the workload; 023 exits 1.
"""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import pid_gone
from .test_runs import items, scenario, wait

PW = "Wkld-UniquePw-4711"


def fast(settings, **extra):
    """charbench runtime in real seconds (00:00:02 = 2 s), lines every 0.2 s."""
    scen = json.loads(settings.sim_scenario.read_text())
    scenario(settings, charbench={"seconds_per_minute": 60, "line_interval": 0.2},
             pdb_scenarios={**scen["pdb_scenarios"], **extra})
    return settings


def start(c, params, expect=201):
    r = c.post("/api/runs", json={"type": "workload", "params": {"runtime": "00:00:02", "check_timeout": 1, **params},
                                  "secrets": {"soe_password": PW}})
    assert r.status_code == expect, r.text
    return r.json()["run_id"]


def run(c, params, timeout=60):
    return wait(c, start(c, params), timeout)


def tsv(path: Path):
    lines = path.read_text().splitlines()
    return [ln.split("\t") for ln in lines[1:]], lines[0]


def test_workload_runs_all_and_scans_logs(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, {"first": 14, "last": 20, "users": 4})
        it = items(c, r["id"])
        charbench = c.get("/api/profiles/active").json()["params"]["charbench_path"]
    assert r["status"] == "ok", r["message"]
    assert r["counts"] == {"ok": 7} and r["summary"]["started"] == 7
    rd = Path(r["run_dir"])
    procs, head = tsv(rd / "processes.tsv")
    assert head == "pdb\tpid\tservice" and len(procs) == 7
    status, head = tsv(rd / "status.tsv")
    assert head == "pdb\texit_code\tseconds" and {s[1] for s in status} == {"0"}
    summ, head = tsv(rd / "summary.tsv")
    assert head == "pdb\texit_code\terror_lines\tfirst_error"
    errs = {row[0]: row for row in summ}
    assert int(errs["PDBPP_019"][2]) > 0 and errs["PDBPP_019"][3].startswith("java.sql.SQLException: ORA-00942")
    assert errs["PDBPP_014"][2] == "0"
    assert it["PDBPP_019"]["state"] == "ok" and it["PDBPP_019"]["error_count"] > 0     # informational only
    assert r["summary"]["with_errors"] == 1 and r["summary"]["error_pdbs"][0]["pdb"] == "PDBPP_019"
    assert (rd / "PDBPP_014-results.xml").read_text().startswith("<?xml")
    assert (rd / "connections.tsv").is_file()
    rec = json.loads((Path(sim_settings.sim_state).parent / "charbench-args" / "PDBPP_014.json").read_text())
    argv = rec["argv"]
    assert argv[argv.index("-uc") + 1] == "4" and argv[argv.index("-di") + 1] == "SQ,WQ,WA"
    assert argv[argv.index("-r") + 1] == str(rd / "PDBPP_014-results.xml")
    assert rec["cwd"] == str(Path(charbench).parent)                  # run from the Swingbench bin directory
    for f in rd.rglob("*"):
        if f.is_file():
            assert PW.encode() not in f.read_bytes(), f


def test_all_launched_before_any_awaited_and_reported_in_completion_order(sim_settings):
    fast(sim_settings, PDBPP_016={"workload_seconds": 0.3})
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, {"first": 14, "last": 18})
    log = (Path(r["run_dir"]) / "run.log").read_text()
    assert log.index("workloads launched") < log.index("finished PDBPP_")
    status, _ = tsv(Path(r["run_dir"]) / "status.tsv")
    assert status[0][0] == "PDBPP_016"                       # launched third, finished first


def test_check_failure_blocks_run(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, {"first": 1, "last": 8})
        it = items(c, r["id"])
    assert r["status"] == "blocked" and "2 PDB(s) failed the connection check" in r["message"]
    assert it["PDBPP_003"]["state"] == "failed" and it["PDBPP_007"]["check_detail"] == "timed out after 1s"
    assert it["PDBPP_001"]["state"] == "not_started"
    assert not (Path(r["run_dir"]) / "processes.tsv").exists()


def test_skip_unreachable_runs_the_rest(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, {"first": 1, "last": 8, "skip_unreachable": True})
        it = items(c, r["id"])
    assert r["status"] == "ok" and r["counts"] == {"ok": 6, "skipped": 2}
    assert it["PDBPP_003"]["detail"].startswith("connection check failed: ORA-12514")
    assert r["summary"]["skipped"] == 2 and r["summary"]["check"]["failed"] == 2


def test_nonzero_exit_fails_run(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        r = run(c, {"first": 22, "last": 24, "disable_txns": ""})
        it = items(c, r["id"])
    assert r["status"] == "failed" and it["PDBPP_023"]["state"] == "failed" and it["PDBPP_023"]["exit_code"] == 1
    assert r["summary"]["failed_pdbs"][0]["pdb"] == "PDBPP_023"
    rec = json.loads((Path(sim_settings.sim_state).parent / "charbench-args" / "PDBPP_022.json").read_text())
    assert "-di" not in rec["argv"]                           # empty disabled list: no -di at all


def test_start_delay_spaces_launches(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        t0 = time.time()
        r = run(c, {"first": 14, "last": 17, "start_delay": "0.5", "runtime": "00:00:01"})
    assert r["status"] == "ok" and time.time() - t0 >= 2.4      # 3 gaps of 0.5 s + 1 s runtime


def test_stop_all_leaves_no_processes(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        rid = start(c, {"first": 14, "last": 21, "runtime": "01:00"})
        deadline = time.time() + 15
        while time.time() < deadline and c.get(f"/api/runs/{rid}").json()["counts"].get("running", 0) < 8:
            time.sleep(0.1)
        pids = [i["pid"] for i in items(c, rid).values()]
        args_dir = Path(sim_settings.sim_state).parent / "charbench-args"
        for _ in range(50):
            if all((args_dir / f"PDBPP_{n:03d}.child").exists() for n in range(14, 22)):
                break
            time.sleep(0.1)
        children = [int((args_dir / f"PDBPP_{n:03d}.child").read_text()) for n in range(14, 22)]
        t0 = time.time()
        c.post(f"/api/runs/{rid}/cancel")
        r = wait(c, rid)
    assert time.time() - t0 < 8
    assert r["status"] == "cancelled" and r["counts"] == {"cancelled": 8}
    assert all(pid_gone(p) for p in pids + children), "charbench or its child survived"
    status, _ = tsv(Path(r["run_dir"]) / "status.tsv")
    assert {s[1] for s in status} <= {"143", "137"}




@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def test_plan_heavy_load_and_tools(client, tmp_path):
    r = client.post("/api/runs", json={"type": "workload", "params": {"first": 1, "last": 40, "users": 32},
                                       "dry_run": True}).json()["plan"]
    assert r["load"] == {**r["load"], "processes": 40, "sessions": 1280, "heavy": True}
    assert "1280 database sessions" in r["load"]["message"]
    assert r["not_open"] == ["PDBPP_029"] and r["runtime_s"] == 1800
    assert r["command_preview"].count("'********'") == 1 and "-di SQ,WQ,WA" in r["command_preview"]
    assert r["equivalent_command"].startswith("SOE_PASSWORD='********' run_charbench_all_pdbs.sh -f 1 -l 40")
    light = client.post("/api/runs", json={"type": "workload", "params": {"first": 1, "last": 4},
                                           "dry_run": True}).json()["plan"]
    assert light["load"]["heavy"] is False and not light["blocked"]
    missing = str(tmp_path / "nope" / "charbench")
    p = client.post("/api/runs", json={"type": "workload", "params": {"first": 1, "last": 2, "charbench_path": missing},
                                       "dry_run": True}).json()["plan"]
    assert p["blocked"] and p["warnings"][0] == f"Not executable: {missing}"
    rid = client.post("/api/runs", json={"type": "workload", "params": {"first": 1, "last": 2, "charbench_path": missing},
                                         "secrets": {"soe_password": PW}}).json()["run_id"]
    r = wait(client, rid)
    assert r["status"] == "blocked" and r["message"].startswith(f"Not executable: {missing}")
