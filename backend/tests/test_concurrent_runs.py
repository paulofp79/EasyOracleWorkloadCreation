"""Several runs at the same time, with per-PDB conflict rules."""
from dataclasses import replace

from fastapi.testclient import TestClient

from app.engine.runs import clash
from app.main import create_app

from .test_runs import wait
from .test_workload import fast

PW, SYS, ADMIN = "Conc-Soe-Pw-31", "Conc-Sys-Pw-32", "Conc-Admin-Pw-33"
SECRETS = {"check": {"soe_password": PW}, "workload": {"soe_password": PW},
           "load": {"soe_password": PW, "sys_password": SYS}, "create": {"pdb_admin_password": ADMIN}, "drop": {}}


def test_rules():
    soe, b1 = "SOE", "BATCH1_SOE"
    assert clash(("workload", soe), ("workload", soe)) is None
    assert clash(("check", soe), ("load", soe)) is None
    assert clash(("check", soe), ("workload", b1)) is None
    assert clash(("load", soe), ("load", b1)) is None
    assert clash(("load", b1), ("workload", soe)) is None
    assert clash(("load", soe), ("load", soe)) == "SOE is being loaded there"
    assert clash(("workload", b1), ("load", b1)) == "loading BATCH1_SOE and a workload on BATCH1_SOE cannot overlap"
    for t in ("check", "workload", "load", "create", "drop"):
        assert clash(("drop", soe), (t, b1)) and clash((t, soe), ("create", soe))


def post(c, type_, params):
    return c.post("/api/runs", json={"type": type_, "params": params, "secrets": SECRETS[type_]})


def test_runs_overlap_unless_they_clash(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(sim_settings)) as c:
        long = post(c, "workload", {"first": 20, "last": 22, "runtime": "00:00:06", "check_timeout": 5})
        assert long.status_code == 201, long.text
        busy = long.json()["run_id"]
        ok = [post(c, "workload", {"first": 21, "last": 22, "runtime": "00:00:01", "check_timeout": 5}),
              post(c, "check", {"first": 20, "last": 30}),
              post(c, "load", {"first": 21, "last": 21, "schema_prefix": "B1", "tablespace": "B1_SOETBS"}),
              post(c, "create", {"first": 251, "last": 252, "use_wallet": False})]
        assert [r.status_code for r in ok] == [201] * 4, [r.text for r in ok]

        r = post(c, "load", {"pdbs": ["PDBPP_022", "PDBPP_040"]})
        assert r.status_code == 409 and r.json()["run_id"] == busy
        assert r.json()["error"] == (f"run {busy} (workload) is still in progress on PDBPP_022: loading SOE and a "
                                     "workload on SOE cannot overlap. Wait for it, stop it, or choose other PDBs.")
        plan = c.post("/api/runs", json={"type": "drop", "params": {"pdbs": ["PDBPP_020"]}, "dry_run": True}).json()["plan"]
        r = post(c, "drop", {"pdbs": ["PDBPP_020"], "confirm_text": "DELETE", "confirm_token": plan["drop_token"]})
        assert r.status_code == 409 and "creating or dropping a PDB needs it to itself" in r.json()["error"]

        active = c.get("/api/runs/active").json()
        ids = [x["id"] for x in active["runs"]]
        assert ids[0] == busy and len(ids) >= 2 and all(x["live"] for x in active["runs"])
        assert active["run"]["id"] == busy and active["max_runs"] == 10

        for rid in [busy, *(x.json()["run_id"] for x in ok)]:
            assert wait(c, rid, 60)["status"] in ("ok", "failed"), rid      # check has PDBPP_023 (exit 1 scenario)
        assert c.get("/api/runs/active").json()["runs"] == []
        # the PDB is free again
        r = post(c, "drop", {"pdbs": ["PDBPP_020"], "confirm_text": "DELETE", "confirm_token": plan["drop_token"]})
        assert r.status_code == 201 and wait(c, r.json()["run_id"])["status"] == "ok"


def test_limit_and_cancel_one_of_many(sim_settings):
    fast(sim_settings)
    with TestClient(create_app(replace(sim_settings, max_runs=2))) as c:
        a = post(c, "workload", {"first": 30, "last": 30, "runtime": "00:00:30", "check_timeout": 5}).json()["run_id"]
        b = post(c, "workload", {"first": 31, "last": 31, "runtime": "00:00:30", "check_timeout": 5}).json()["run_id"]
        r = post(c, "check", {"first": 40, "last": 40})
        assert r.status_code == 409 and "the limit is 2, ORCH_MAX_RUNS" in r.json()["error"]
        assert c.post(f"/api/runs/{a}/cancel").status_code == 202
        assert wait(c, a)["status"] == "cancelled"
        assert c.get(f"/api/runs/{b}").json()["status"] == "running"        # the other one is untouched
        assert post(c, "check", {"first": 40, "last": 40}).status_code == 201
        c.post(f"/api/runs/{b}/cancel")
        assert wait(c, b)["status"] == "cancelled"


def test_increase_clashes_with_load_and_workload_on_its_schemas():
    both, soe, b1 = frozenset({"SOE", "BATCH1_SOE"}), "SOE", "BATCH1_SOE"
    assert clash(("increase", both), ("increase", soe)) == "SOE is being increased there"
    assert clash(("load", b1), ("increase", both)) == \
        "loading BATCH1_SOE and increasing BATCH1_SOE cannot overlap"
    assert clash(("workload", soe), ("increase", both)) == "increasing SOE and a workload on SOE cannot overlap"
    assert clash(("increase", frozenset({"B2_SOE"})), ("workload", soe)) is None
    assert clash(("check", soe), ("increase", both)) is None


def test_increase_with_several_schemas_is_refused_on_a_busy_pdb(sim_settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .test_runs import scenario, wait
    scenario(sim_settings, delays={"sbutil": 30})
    with TestClient(create_app(sim_settings)) as c:
        r = c.post("/api/runs", json={"type": "increase", "params": {"first": 1, "last": 2, "schemas": ["", "B1"]},
                                      "secrets": {"soe_password": "pw-inc-1"}})
        assert r.status_code == 201, r.text
        first = r.json()["run_id"]
        r = c.post("/api/runs", json={"type": "load", "params": {"first": 2, "last": 3},
                                      "secrets": {"soe_password": "pw-inc-1", "sys_password": "sys-inc-2"}})
        assert r.status_code == 409 and r.json()["run_id"] == first
        assert "on PDBPP_002: loading SOE and increasing SOE cannot overlap" in r.json()["error"]
        r = c.post("/api/runs", json={"type": "check", "params": {"first": 1, "last": 2},
                                      "secrets": {"soe_password": "pw-inc-1"}})
        assert r.status_code == 201                       # checks still run alongside
        wait(c, r.json()["run_id"])
        c.post(f"/api/runs/{first}/cancel")
        assert wait(c, first)["status"] == "cancelled"
