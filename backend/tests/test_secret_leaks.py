"""M6 secret-leak suite: every secret the app ever handles must stay out of files, the database,
logs and API responses.

Runs the full lifecycle in remote CDB mode with login on, each secret a unique string:
login password, SYS (browser session), PDB admin, TDE wallet, SOE.
"""
import io
import json
import time
import zipfile
from dataclasses import replace

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import Database
from app.main import create_app

from .test_runs import scenario
from .test_workload import fast

SECRETS = {
    "login": "Login-Leak-Canary-7311",
    "sys": "Sys-Leak-Canary-9042",
    "admin": "Admin-Leak-Canary-5518",
    "wallet": "Wallet-Leak-Canary-2667",
    "soe": "Soe-Leak-Canary-8125",
}


def test_no_secret_anywhere(sim_settings, tmp_path):
    fast(sim_settings)
    cdb = json.loads(sim_settings.sim_scenario.read_text())["cdb"]
    scenario(sim_settings, cdb={**cdb, "sys_password": SECRETS["sys"]})
    db = Database(sim_settings.db_path)
    db.set_user("admin", hash_password(SECRETS["login"]))
    db.close()
    responses: list[bytes] = []

    with TestClient(create_app(replace(sim_settings, auth=True))) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": SECRETS["login"]})
        responses.append(r.content)
        h = {"X-CSRF-Token": r.json()["csrf"]}

        def post(url, body):
            resp = c.post(url, json=body, headers=h)
            responses.append(resp.content)
            assert resp.status_code in (200, 201), resp.text
            return resp.json()

        def put(url, body):
            resp = c.put(url, json=body, headers=h)
            responses.append(resp.content)
            assert resp.status_code == 200, resp.text
            return resp.json()

        def get(url):
            resp = c.get(url)
            responses.append(resp.content)
            return resp

        def run(type_, params, secrets):
            rid = post("/api/runs", {"type": type_, "params": params, "secrets": secrets})["run_id"]
            for _ in range(300):
                st = get(f"/api/runs/{rid}").json()
                if st["status"] != "running":
                    break
                time.sleep(0.1)
            assert st["status"] == "ok", (type_, st.get("message"))
            return rid

        prof = get("/api/profiles/active").json()
        put(f"/api/profiles/{prof['id']}", {"name": prof["name"], "params": {**prof["params"], "cdb_connect": "remote"}})
        put("/api/session/sys-password", {"sys_password": SECRETS["sys"], "hours": 1})

        ids = [
            run("create", {"first": 41, "last": 42, "use_wallet": True},
                {"pdb_admin_password": SECRETS["admin"], "wallet_password": SECRETS["wallet"]}),
            run("load", {"first": 41, "last": 42}, {"soe_password": SECRETS["soe"]}),
            run("check", {"first": 41, "last": 42}, {"soe_password": SECRETS["soe"]}),
            run("workload", {"first": 41, "last": 42, "runtime": "00:00:01"}, {"soe_password": SECRETS["soe"]}),
        ]
        plan = post("/api/runs", {"type": "drop", "params": {"pdbs": ["PDBPP_042"]}, "dry_run": True})["plan"]
        ids.append(run("drop", {"pdbs": ["PDBPP_042"], "confirm_text": "DELETE", "confirm_token": plan["drop_token"]}, {}))

        for rid in ids:
            for url in (f"/api/runs/{rid}", f"/api/runs/{rid}/items", f"/api/runs/{rid}/log",
                        f"/api/runs/{rid}/events?last_id=0", f"/api/runs/{rid}/summary.csv",
                        f"/api/runs/{rid}/artifacts"):
                get(url)
            for f in get(f"/api/runs/{rid}/artifacts").json()["files"]:
                get(f"/api/runs/{rid}/files/{f['path']}")
            for pdb in ("PDBPP_041", "PDBPP_042"):
                for i in range(2):
                    get(f"/api/runs/{rid}/items/{pdb}/log?file={i}")
            zipped = get(f"/api/runs/{rid}/artifacts.zip").content
            with zipfile.ZipFile(io.BytesIO(zipped)) as zf:
                responses.extend(zf.read(n) for n in zf.namelist())
        for url in ("/api/runs", "/api/audit?limit=1000", "/api/session", "/api/pdbs", "/api/env-check",
                    "/api/profiles", "/api/auth/state", f"/api/runs/compare?a={ids[3]}&b={ids[3]}"):
            get(url)
        audit = get("/api/audit?limit=1000").json()
        assert {"login", "sys_password_set", "start", "drop_confirmed"} <= set(audit["actions"])

    leaks = []
    for name, secret in SECRETS.items():
        needle = secret.encode()
        for i, body in enumerate(responses):
            if needle in body:
                leaks.append(f"{name} in API response #{i}")
        for f in tmp_path.rglob("*"):
            if f == sim_settings.sim_scenario:      # test input: the simulated database's own SYS password
                continue
            if f.is_file() and needle in f.read_bytes():
                leaks.append(f"{name} in {f.relative_to(tmp_path)}")
    assert not leaks, leaks
    assert len(responses) > 100
