"""M5: run history filters, artifacts, CSV, compare, and recovery after a backend crash (real kill -9)."""
import csv
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT
from app.db import Database
from app.main import create_app

from .conftest import pid_gone
from .test_runs import items, scenario, wait
from .test_workload import fast

PW = "Hist-UniquePw-2207"


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def check(c, params):
    r = c.post("/api/runs", json={"type": "check", "params": params, "secrets": {"soe_password": PW}})
    assert r.status_code == 201, r.text
    return wait(c, r.json()["run_id"])


def test_history_filters_and_paging(client):
    ok = check(client, {"first": 14, "last": 15})
    bad = check(client, {"first": 3, "last": 3, "check_timeout": 1})
    third = check(client, {"first": 16, "last": 16, "prefix": "pdbpp"})
    all_runs = client.get("/api/runs").json()
    assert all_runs["total"] == 3 and [r["id"] for r in all_runs["runs"]] == [third["id"], bad["id"], ok["id"]]
    assert [r["id"] for r in client.get("/api/runs?status=failed").json()["runs"]] == [bad["id"]]
    assert client.get("/api/runs?type=load").json() == {"runs": [], "total": 0}
    page = client.get("/api/runs?limit=2&offset=2").json()
    assert page["total"] == 3 and [r["id"] for r in page["runs"]] == [ok["id"]]
    assert [r["id"] for r in client.get(f"/api/runs?q={bad['id'][-6:]}").json()["runs"]] == [bad["id"]]
    assert client.get("/api/runs?status=nope").status_code == 422
    assert client.get(f"/api/runs?since={ok['created_at']}").json()["total"] == 3


def test_artifacts_list_download_zip_and_csv(client, sim_settings):
    r = check(client, {"first": 1, "last": 4, "check_timeout": 1})
    rid = r["id"]
    art = client.get(f"/api/runs/{rid}/artifacts").json()
    paths = [f["path"] for f in art["files"]]
    assert paths[:2] == ["connections.tsv", "run.log"] and "connection-check/PDBPP_003.log" in paths
    assert art["total_bytes"] > 0
    f = client.get(f"/api/runs/{rid}/files/connections.tsv")
    assert f.status_code == 200 and f.text.startswith("pdb\tstatus\tdetail")
    for bad in ("../orch.db", "..%2F..%2Forch.db", "/etc/passwd", "connection-check/../../orch.db"):
        assert client.get(f"/api/runs/{rid}/files/{bad}").status_code == 404, bad
    z = client.get(f"/api/runs/{rid}/artifacts.zip")
    assert z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert f"run-{rid}/connections.tsv" in names and f"run-{rid}/connection-check/PDBPP_001.log" in names
    assert not list(Path("/tmp").glob(f"run-{rid}-*.zip"))           # temporary zip removed after sending
    # CSV, with a formula-looking detail neutralised
    db = Database(sim_settings.db_path)
    db.conn.execute("UPDATE run_items SET detail = '=HYPERLINK(\"http://x\")' WHERE run_id = ? AND pdb = 'PDBPP_002'",
                    (rid,))
    db.close()
    text = client.get(f"/api/runs/{rid}/summary.csv").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["pdb"] for row in rows] == ["PDBPP_001", "PDBPP_002", "PDBPP_003", "PDBPP_004"]
    assert rows[2]["state"] == "failed" and rows[2]["check_detail"].startswith("ORA-12514")
    assert rows[1]["detail"].startswith("'=HYPERLINK")
    assert PW not in text and PW not in z.content.decode("latin-1")


def test_compare_two_workload_runs(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        ids = []
        for users in (4, 8):
            r = c.post("/api/runs", json={"type": "workload", "params": {"first": 18, "last": 21, "users": users,
                                                                           "runtime": "00:00:01"},
                                          "secrets": {"soe_password": PW}})
            ids.append(wait(c, r.json()["run_id"])["id"])
        cmp = c.get(f"/api/runs/compare?a={ids[0]}&b={ids[1]}").json()
        chk = check(c, {"first": 1, "last": 1})
        assert c.get(f"/api/runs/compare?a={ids[0]}&b={chk['id']}").status_code == 422
    users = next(p for p in cmp["params"] if p["key"] == "users")
    assert users == {"key": "users", "label": "Users per PDB", "a": 4, "b": 8, "differs": True}
    assert not next(p for p in cmp["params"] if p["key"] == "runtime")["differs"]
    m = {x["key"]: x for x in cmp["metrics"]}
    assert m["started"]["a"] == m["started"]["b"] == 4
    assert m["sessions"]["a"] == 16 and m["sessions"]["b"] == 32
    assert m["with_errors"]["a"] == 1 and m["error_lines"]["a"] >= 1          # PDBPP_019 prints ORA-00942
    assert m["dur_avg"]["a"] is not None
    assert [p["pdb"] for p in cmp["pdbs"]] == ["PDBPP_018", "PDBPP_019", "PDBPP_020", "PDBPP_021"]


# ---- crash recovery with a real backend process --------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_backend_kill9_mid_workload_then_kill_leftovers(sim_settings):
    fast(sim_settings, PDBPP_016={"workload_seconds": 3})        # ends while the backend is down
    port = free_port()
    env = {**os.environ, "ORCH_MODE": "simulate", "ORCH_DB": str(sim_settings.db_path), "ORCH_PORT": str(port),
           "ORCH_SIM_ROOT": str(sim_settings.sim_root), "ORCH_SIM_SCENARIO": str(sim_settings.sim_scenario),
           "ORCH_SIM_STATE": str(sim_settings.sim_state)}
    backend = subprocess.Popen([sys.executable, "-m", "app"], cwd=REPO_ROOT / "backend", env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    children: list[int] = []
    pids: list[int] = []
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        rid = httpx.post(f"{base}/api/runs", json={"type": "workload",
                                                   "params": {"first": 14, "last": 18, "runtime": "10:00"},
                                                   "secrets": {"soe_password": PW}}).json()["run_id"]
        for _ in range(150):
            run = httpx.get(f"{base}/api/runs/{rid}").json()
            if run["counts"].get("running", 0) == 5:
                break
            time.sleep(0.1)
        assert run["counts"].get("running") == 5
        time.sleep(0.6)                                       # let the 250 ms flush persist the pids
        backend.send_signal(signal.SIGKILL)                   # crash: no shutdown hook runs
        backend.wait(5)
        args_dir = Path(sim_settings.sim_state).parent / "charbench-args"
        children = [int((args_dir / f"PDBPP_{n:03d}.child").read_text()) for n in range(14, 19)]
        time.sleep(3.5)                                       # PDBPP_016 finishes meanwhile

        with TestClient(create_app(sim_settings)) as c:       # the backend comes back
            run = c.get(f"/api/runs/{rid}").json()
            it = items(c, rid)
            pids = [i["pid"] for i in it.values()]
            assert run["status"] == "interrupted" and "still running" in run["message"]
            assert run["leftovers"] == 4
            assert it["PDBPP_016"]["state"] == "cancelled" and "result unknown" in it["PDBPP_016"]["detail"]
            assert it["PDBPP_014"]["state"] == "running" and "no longer monitored" in it["PDBPP_014"]["detail"]
            left = c.get(f"/api/runs/{rid}/leftovers").json()["processes"]
            assert [p["pdb"] for p in left] == ["PDBPP_014", "PDBPP_015", "PDBPP_017", "PDBPP_018"]
            # logs written before the crash are kept
            log = c.get(f"/api/runs/{rid}/items/PDBPP_014/log").json()["lines"]
            assert any(ln.startswith("Swingbench") for ln in log)
            # still alive although their stdout pipe broke with the backend
            assert all(not pid_gone(p["pid"]) for p in left)
            res = c.post(f"/api/runs/{rid}/kill-leftovers").json()
            assert res == {"stopped": ["PDBPP_014", "PDBPP_015", "PDBPP_017", "PDBPP_018"], "still_alive": []}
            it = items(c, rid)
            assert {it[p]["detail"] for p in res["stopped"]} == {"stopped with Kill leftovers"}
            assert c.get(f"/api/runs/{rid}").json()["leftovers"] == 0
            assert c.post(f"/api/runs/{rid}/kill-leftovers").json() == {"stopped": [], "still_alive": []}
        assert all(pid_gone(p) for p in pids + children)
    finally:
        if backend.poll() is None:
            backend.kill()
        for p in pids + children:
            try:
                os.killpg(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def test_kill_leftovers_only_for_interrupted_runs(client):
    r = check(client, {"first": 14, "last": 14})
    assert client.post(f"/api/runs/{r['id']}/kill-leftovers").status_code == 409
    assert client.get(f"/api/runs/{r['id']}/leftovers").json() == {"processes": []}
