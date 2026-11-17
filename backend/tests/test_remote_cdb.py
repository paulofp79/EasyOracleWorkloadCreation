"""Remote CDB mode: SYSDBA over the network with the SYS password from the browser session."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sessions import COOKIE
from app.sysdba import SysdbaConn, SysPasswordMissing

from .test_runs import items, scenario, wait

SYS = "Sys-Remote-UniquePw-77"
ADMIN = "Adm-Remote-UniquePw-12"
SOE = "Soe-Remote-UniquePw-40"


def test_conn_local_and_remote():
    local = SysdbaConn.from_params({"cdb_connect": "local"}, None)
    assert local.argv() == ["sqlplus", "-L", "-s", "/", "as", "sysdba"] and local.stdin("X") == "X"
    assert local.logon_failure("anything") is None
    remote = SysdbaConn.from_params({"cdb_connect": "remote", "scan_host": "scan1", "scan_port": 1521,
                                     "cdb_service": "cdb1.example.com"}, "p@ss w#rd")
    assert remote.argv() == ["sqlplus", "-L", "-s", "/nolog"]
    assert "p@ss" not in " ".join(remote.argv())                     # password never on a command line
    text = remote.stdin("SELECT 1 FROM dual;\n")
    assert text.startswith('CONNECT sys/"p@ss w#rd"@//scan1:1521/cdb1.example.com AS SYSDBA\n')
    assert text.endswith("SELECT 1 FROM dual;\n") and "SYSDBA_' || 'SESSION_OK" in text
    assert remote.logon_failure("C\n---\nSYSDBA_SESSION_OK\n") is None
    assert remote.logon_failure("ERROR:\nORA-01017: invalid username/password; logon denied\nSP2-0640: Not connected") \
        == "ORA-01017: invalid username/password; logon denied"
    assert remote.describe() == "sys@//scan1:1521/cdb1.example.com as sysdba"
    with pytest.raises(SysPasswordMissing):
        SysdbaConn.from_params({"cdb_connect": "remote"}, None).require()


def test_connect_syntax_matches_the_soe_check_in_run_charbench():
    """Same CONNECT form the user's script runs successfully: user/"pw"@//scan:port/service."""
    from app.sqlbuild import check_sql
    soe = [ln for ln in check_sql("pw", "//scan1:1521/svc").splitlines() if ln.startswith("CONNECT")][0]
    sys_ = SysdbaConn.from_params({"cdb_connect": "remote", "scan_host": "scan1", "scan_port": 1521,
                                   "cdb_service": "svc"}, "pw").stdin("").splitlines()[0]
    assert soe == 'CONNECT soe/"pw"@//scan1:1521/svc'
    assert sys_ == 'CONNECT sys/"pw"@//scan1:1521/svc AS SYSDBA'


@pytest.fixture
def remote(sim_settings):
    cdb = json.loads(sim_settings.sim_scenario.read_text())["cdb"]
    scenario(sim_settings, cdb={**cdb, "sys_password": SYS})
    with TestClient(create_app(sim_settings)) as c:
        prof = c.get("/api/profiles/active").json()
        r = c.put(f"/api/profiles/{prof['id']}", json={"name": prof["name"],
                                                       "params": {**prof["params"], "cdb_connect": "remote"}})
        assert r.status_code == 200, r.text
        yield c


def login(c, pw=SYS):
    return c.put("/api/session/sys-password", json={"sys_password": pw, "hours": 1})


def test_remote_requires_cdb_service(remote):
    prof = remote.get("/api/profiles/active").json()
    r = remote.put(f"/api/profiles/{prof['id']}", json={"name": prof["name"],
                                                        "params": {**prof["params"], "cdb_service": ""}})
    assert r.status_code == 422 and "params.cdb_service" in r.json()["errors"]


def test_without_sys_password(remote):
    r = remote.get("/api/pdbs")
    assert r.status_code == 428 and r.json()["need_sys_password"]
    env = {c["id"]: c for c in remote.get("/api/env-check").json()["checks"]}
    assert env["sysdba"]["status"] == "warn" and "SYS password" in env["sysdba"]["error"]
    assert env["sysdba"]["label"] == "Connect sys@//sim-scan.example.com:1521/simcdb.example.com as sysdba (CDB root)"
    assert env["oracle_env"]["status"] == "skip"
    # a check only needs SOE; its plan says what it could not verify
    plan = remote.post("/api/runs", json={"type": "check", "params": {"first": 1, "last": 2}, "dry_run": True}).json()
    assert "not verified" in plan["plan"]["warnings"][0]
    r = remote.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41, "use_wallet": False},
                                       "dry_run": True})
    assert r.status_code == 428
    r = remote.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41, "use_wallet": False},
                                       "secrets": {"pdb_admin_password": ADMIN}})
    assert r.status_code == 422 and r.json()["errors"] == {"secrets.sys_password": "is required"}


def test_wrong_password_is_rejected_and_not_kept(remote):
    r = login(remote, "wrong-password")
    assert r.status_code == 422 and r.json()["errors"]["sys_password"].startswith("ORA-01017")
    assert COOKIE not in r.cookies
    assert remote.get("/api/session").json()["sys_password_set"] is False


def test_wrong_service_reported(remote):
    prof = remote.get("/api/profiles/active").json()
    remote.put(f"/api/profiles/{prof['id']}", json={"name": prof["name"],
                                                    "params": {**prof["params"], "cdb_service": "pdbpp_001.example.com"}})
    r = login(remote)
    assert r.status_code == 422 and r.json()["errors"]["sys_password"].startswith("ORA-12514")


def test_session_password_unlocks_everything(remote, sim_settings):
    r = login(remote)
    assert r.status_code == 200 and r.json()["sys_password_set"] and r.json()["cdb_connect"] == "remote"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert len(remote.get("/api/pdbs").json()["pdbs"]) == 40
    env = {c["id"]: c for c in remote.get("/api/env-check").json()["checks"]}
    assert env["sysdba"]["status"] == "pass" and "CDB SIMCDB" in env["sysdba"]["detail"]

    # create + load + drop in remote mode with SYS taken from the session
    run = remote.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 42, "use_wallet": False},
                                         "secrets": {"pdb_admin_password": ADMIN}})
    assert run.status_code == 201, run.text
    created = wait(remote, run.json()["run_id"])
    assert created["status"] == "ok"
    run = remote.post("/api/runs", json={"type": "load", "params": {"first": 41, "last": 42},
                                         "secrets": {"soe_password": SOE}})
    loaded = wait(remote, run.json()["run_id"])
    assert loaded["status"] == "ok" and "Grant succeeded." in (Path(loaded["run_dir"]) / "PDBPP_041-soe-setup.log").read_text()
    plan = remote.post("/api/runs", json={"type": "drop", "params": {"pdbs": ["PDBPP_042"]}, "dry_run": True}).json()["plan"]
    run = remote.post("/api/runs", json={"type": "drop", "params": {"pdbs": ["PDBPP_042"], "confirm_text": "DELETE",
                                                                    "confirm_token": plan["drop_token"]}, "secrets": {}})
    assert wait(remote, run.json()["run_id"])["status"] == "ok"
    assert "PDBPP_042" not in {p["name"] for p in remote.get("/api/pdbs").json()["pdbs"]}

    # the SYS password never reaches responses, logs or the database
    for rid in (created["id"], loaded["id"]):
        for f in Path(remote.get(f"/api/runs/{rid}").json()["run_dir"]).rglob("*"):
            if f.is_file():
                assert SYS.encode() not in f.read_bytes(), f
    for f in sim_settings.db_path.parent.glob("orch.db*"):
        assert SYS.encode() not in f.read_bytes(), f
    for url in ("/api/session", "/api/runs", f"/api/runs/{created['id']}/items", "/api/env-check"):
        assert SYS not in remote.get(url).text


def test_session_is_per_browser_and_expires(remote, sim_settings):
    assert login(remote).status_code == 200
    other = TestClient(remote.app)                   # another browser on the same server: no cookie
    assert other.get("/api/pdbs").status_code == 428
    store = remote.app.state.sessions
    store._clock = lambda: 10 ** 12                  # far in the future: expired
    assert remote.get("/api/pdbs").status_code == 428
    assert remote.get("/api/session").json()["sys_password_set"] is False


def test_forget(remote):
    login(remote)
    assert remote.delete("/api/session/sys-password").json()["sys_password_set"] is False
    assert remote.get("/api/pdbs").status_code == 428


def test_failed_logon_with_exit_0_is_not_success(remote, sim_settings):
    """A wrong SYS password reaching a run (e.g. changed in the database meanwhile) must not look like success."""
    from app.sessions import COOKIE as SID
    assert login(remote).status_code == 200
    scen = json.loads(sim_settings.sim_scenario.read_text())
    scenario(sim_settings, cdb={**scen["cdb"], "sys_password": "changed-in-the-db"})
    r = remote.get("/api/pdbs")
    assert r.status_code == 502 and "ORA-01017" in r.json()["error"]          # not an empty inventory
    r = remote.post("/api/runs", json={"type": "create", "params": {"first": 41, "last": 41, "use_wallet": False},
                                       "secrets": {"pdb_admin_password": ADMIN, "sys_password": SYS}})
    run = wait(remote, r.json()["run_id"])
    assert run["status"] == "failed" and "ORA-01017" in run["message"]       # pre-flight refused, nothing created
    assert SID in remote.cookies


def test_double_quote_rejected_for_remote_sys(remote):
    r = remote.put("/api/session/sys-password", json={"sys_password": 'a"b', "hours": 1})
    assert r.status_code == 422 and "double quote" in r.json()["errors"]["sys_password"]
