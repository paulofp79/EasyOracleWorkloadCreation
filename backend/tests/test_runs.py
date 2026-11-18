"""Run engine + connection check end-to-end through the API, in simulation mode."""
import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.engine.events import EventBus
from app.main import create_app

from .conftest import pid_gone

PW = "Zq9-UniqueSecret-77"


def scenario(settings, **changes):
    scen = json.loads(settings.sim_scenario.read_text())
    for k, val in changes.items():
        scen[k] = val
    settings.sim_scenario.write_text(json.dumps(scen))
    return settings


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def start(c, params=None, secrets=None, expect=201):
    r = c.post("/api/runs", json={"type": "check", "params": params or {},
                                  "secrets": {"soe_password": PW} if secrets is None else secrets})
    assert r.status_code == expect, r.text
    return r.json().get("run_id")


def wait(c, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = c.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def items(c, run_id):
    return {i["pdb"]: i for i in c.get(f"/api/runs/{run_id}/items").json()["items"]}


# ---- inventory -----------------------------------------------------------------------------

def test_inventory_lists_all_250_pdbs(sim_settings):
    scenario(sim_settings, initial_pdbs=[{"prefix": "PDBPP", "first": 1, "last": 250, "soe_loaded": True},
                                         {"prefix": "OTHER", "first": 1, "last": 3}])
    with TestClient(create_app(sim_settings)) as c:
        inv = c.get("/api/pdbs").json()
    assert len(inv["pdbs"]) == 250                       # 500 output lines must all be read
    assert inv["pdbs"][0]["name"] == "PDBPP_001" and inv["pdbs"][-1]["name"] == "PDBPP_250"
    assert [i["instance"] for i in inv["instances"]] == ["simcdb1", "simcdb2"]
    p29 = next(p for p in inv["pdbs"] if p["name"] == "PDBPP_029")
    assert p29["rw_instances"] == 0 and {i["open_mode"] for i in p29["instances"]} == {"MOUNTED"}


def test_inventory_error_is_reported(sim_settings):
    scenario(sim_settings, cdb={**json.loads(sim_settings.sim_scenario.read_text())["cdb"], "sysdba": "ora01031"})
    with TestClient(create_app(sim_settings)) as c:
        r = c.get("/api/pdbs")
    assert r.status_code == 502 and "ORA-01031" in r.json()["error"]


# ---- connection check ------------------------------------------------------------------------

def test_check_run_reasons_artifacts_and_last_checks(client):
    rid = start(client, {"first": 1, "last": 40, "check_timeout": 1, "check_parallel": 8})
    run = wait(client, rid)
    assert run["status"] == "failed"
    assert run["counts"] == {"ok": 35, "failed": 5}
    assert run["summary"]["peak_running"] <= 8
    it = items(client, rid)
    assert it["PDBPP_001"]["state"] == "ok" and it["PDBPP_001"]["instance"].startswith("simcdb")
    assert it["PDBPP_003"]["check_detail"].startswith("ORA-12514")
    assert it["PDBPP_007"]["check_detail"] == "timed out after 1s"
    assert it["PDBPP_011"]["check_detail"] == "connected to container CDB$ROOT, expected PDBPP_011"
    assert it["PDBPP_013"]["check_detail"] == "SOE schema missing or incomplete (0/4 core tables)"
    assert it["PDBPP_029"]["check_detail"].startswith("ORA-12514")      # mounted
    run_dir = Path(run["run_dir"])
    tsv = (run_dir / "connections.tsv").read_text().splitlines()
    assert tsv[0] == "pdb\tstatus\tdetail" and len(tsv) == 41
    assert "PDBPP_007\tFAIL\ttimed out after 1s" in tsv
    assert oct(run_dir.stat().st_mode & 0o777) == "0o700"
    assert all(oct(f.stat().st_mode & 0o777) == "0o600" for f in run_dir.rglob("*") if f.is_file())
    assert (run_dir.parent / "latest").resolve() == run_dir.resolve()
    inv = {p["name"]: p for p in client.get("/api/pdbs").json()["pdbs"]}
    assert inv["PDBPP_013"]["last_check"]["status"] == "FAIL"
    assert inv["PDBPP_002"]["last_check"]["run_id"] == rid
    log = client.get(f"/api/runs/{rid}/items/PDBPP_003/log").json()
    assert "SP2-0640: Not connected" in log["lines"]


def test_parallelism_limit_is_respected(sim_settings):
    scenario(sim_settings, delays={"check_ok": 0.3})
    with TestClient(create_app(sim_settings)) as c:
        rid = start(c, {"first": 14, "last": 40, "check_parallel": 3})
        run = wait(c, rid)
    assert run["summary"]["peak_running"] == 3


def test_selection_only_checks_selected(client):
    rid = start(client, {"pdbs": ["pdbpp_002", "PDBPP_013", "PDBPP_002"]})
    run = wait(client, rid)
    assert set(items(client, rid)) == {"PDBPP_002", "PDBPP_013"}
    assert run["counts"] == {"ok": 1, "failed": 1}


def test_all_ok_run_is_ok(client):
    run = wait(client, start(client, {"first": 14, "last": 20}))
    assert run["status"] == "ok" and run["summary"]["failed"] == 0


def test_validation_errors(client):
    r = client.post("/api/runs", json={"type": "check", "params": {"first": 5, "last": 2, "bogus": 1},
                                       "secrets": {}})
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"last", "bogus", "secrets.soe_password"}
    r = client.post("/api/runs", json={"type": "check", "params": {}, "secrets": {"soe_password": 'a"b'}})
    assert r.json()["errors"] == {"secrets.soe_password": 'must not contain a double quote (")'}
    r = client.post("/api/runs", json={"type": "check", "params": {"pdbs": ["OTHER_001"]},
                                       "secrets": {"soe_password": "x"}})
    assert "pdbs" in r.json()["errors"]
    r = client.post("/api/runs", json={"type": "nope", "params": {}})
    assert r.status_code == 422 and "type" in r.json()["errors"]


def test_dry_run_needs_no_secret_and_reports_plan(client):
    r = client.post("/api/runs", json={"type": "check", "params": {"first": 38, "last": 42}, "dry_run": True})
    plan = r.json()["plan"]
    assert plan["count"] == 5 and plan["missing"] == ["PDBPP_041", "PDBPP_042"]
    assert plan["equivalent_command"].startswith("SOE_PASSWORD='********' run_charbench_all_pdbs.sh --check-only")
    assert client.get("/api/runs").json()["runs"] == []      # nothing started


def test_cancel_kills_hanging_checks(sim_settings):
    hang = {f"PDBPP_{i:03d}": {"check": "hang"} for i in range(1, 41)}
    scenario(sim_settings, pdb_scenarios=hang)
    with TestClient(create_app(sim_settings)) as c:
        rid = start(c, {"first": 1, "last": 40, "check_parallel": 8, "check_timeout": 120})
        deadline = time.time() + 10
        while time.time() < deadline and c.get(f"/api/runs/{rid}").json()["counts"].get("checking", 0) < 8:
            time.sleep(0.1)
        pids = [i["pid"] for i in items(c, rid).values() if i["state"] == "checking"]
        assert len(pids) == 8
        t0 = time.time()
        assert c.post(f"/api/runs/{rid}/cancel").status_code == 202
        run = wait(c, rid)
        assert time.time() - t0 < 8
    assert run["status"] == "cancelled" and run["message"] == "Stopped by the user."
    assert run["counts"] == {"cancelled": 8, "not_started": 32}
    assert all(pid_gone(p) for p in pids)


def test_secret_never_leaks(client, sim_settings):
    rid = start(client, {"first": 1, "last": 15, "check_timeout": 1})
    run = wait(client, rid)
    bodies = [client.get(u).text for u in (f"/api/runs/{rid}", f"/api/runs/{rid}/items", "/api/runs",
                                           f"/api/runs/{rid}/log", f"/api/runs/{rid}/items/PDBPP_001/log",
                                           f"/api/runs/{rid}/events?last_id=0", "/api/pdbs")]
    assert not any(PW in b for b in bodies)
    for f in Path(run["run_dir"]).rglob("*"):
        if f.is_file():
            assert PW.encode() not in f.read_bytes(), f
    for f in sim_settings.db_path.parent.glob("orch.db*"):
        assert PW.encode() not in f.read_bytes(), f


def test_events_replay_after_finish(client):
    rid = start(client, {"first": 1, "last": 5})
    wait(client, rid)
    time.sleep(0.1)
    body = client.get(f"/api/runs/{rid}/events?last_id=0").text
    events = [blk for blk in body.split("\n\n") if blk.strip()]
    kinds = [ln.split(": ", 1)[1] for blk in events for ln in blk.splitlines() if ln.startswith("event: ")]
    assert kinds[0] == "log.run" and kinds[-1] == "end"
    assert "items.patch" in kinds and "run.updated" in kinds
    ids = [int(ln[4:]) for blk in events for ln in blk.splitlines() if ln.startswith("id: ")]
    assert ids == sorted(ids) and ids[0] == 1
    # reconnect from the middle: only later events
    mid = ids[len(ids) // 2]
    later = client.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": str(mid)}).text
    later_ids = [int(ln[4:]) for ln in later.splitlines() if ln.startswith("id: ")]
    assert later_ids and min(later_ids) == mid + 1


def test_restart_marks_running_runs_interrupted(sim_settings):
    db = Database(sim_settings.db_path)
    db.insert_run({"id": "r1", "type": "check", "status": "running", "params": {}, "created_at": "x"},
                  ["PDBPP_001", "PDBPP_002"])
    db.update_items("r1", [{"pdb": "PDBPP_001", "state": "checking", "pid": 4242}])
    db.close()
    with TestClient(create_app(sim_settings)) as c:
        run = c.get("/api/runs/r1").json()
        it = items(c, "r1")
    assert run["status"] == "interrupted" and "backend stopped" in run["message"]
    assert it["PDBPP_001"]["state"] == "cancelled" and "pid 4242" in it["PDBPP_001"]["detail"]
    assert it["PDBPP_002"]["state"] == "not_started"
    assert run["event_seq"] is None


# ---- event bus ------------------------------------------------------------------------------------

async def _collect(gen, n):
    out = []
    async for chunk in gen:
        out.append(chunk)
        if len(out) == n:
            break
    return out


async def test_bus_replay_resync_and_end():
    bus = EventBus("r", buffer=3)
    for i in range(5):
        bus.publish("x", {"i": i})
    # seq 3..5 are buffered; asking from 1 is too old
    first = await _collect(bus.stream(1), 1)
    assert first[0].startswith("event: resync")
    replay = await _collect(bus.stream(3), 2)
    assert [c.split("\n")[0] for c in replay] == ["id: 4", "id: 5"]
    bus.close()
    tail = await _collect(bus.stream(5), 1)
    assert tail[0].startswith("event: end")


async def test_bus_live_delivery_without_duplicates():
    bus = EventBus("r")
    bus.publish("a", 1)
    gen = bus.stream(0)
    got = [await gen.__anext__()]             # replay of seq 1
    bus.publish("b", 2)
    got.append(await asyncio.wait_for(gen.__anext__(), 1))
    bus.close()
    got.append(await asyncio.wait_for(gen.__anext__(), 1))
    assert [g.split("\n")[0] for g in got] == ["id: 1", "id: 2", "event: end"]
    await gen.aclose()
    assert bus.subscribers == 0
