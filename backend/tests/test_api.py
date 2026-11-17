import json
import stat
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["mode"] == "simulate"


def test_seeded_simulation_profile(client, sim_settings):
    profiles = client.get("/api/profiles").json()["profiles"]
    assert [p["name"] for p in profiles] == ["simulation"]
    assert profiles[0]["is_active"]
    assert profiles[0]["params"]["charbench_path"].endswith("tools/stubs/charbench")
    assert stat.S_IMODE(sim_settings.db_path.stat().st_mode) == 0o600


def test_profile_crud_and_activation(client):
    r = client.post("/api/profiles", json={"name": "exa-lab", "params": {"prefix": "pdbx", "users": "16"}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["params"]["prefix"] == "PDBX" and p["params"]["users"] == 16 and not p["is_active"]
    assert client.post(f"/api/profiles/{p['id']}/activate").json()["is_active"]
    assert client.get("/api/profiles/active").json()["name"] == "exa-lab"
    r = client.put(f"/api/profiles/{p['id']}", json={"name": "exa-lab", "params": {**p["params"], "users": 32}})
    assert r.json()["params"]["users"] == 32
    assert client.delete(f"/api/profiles/{p['id']}").status_code == 204
    # the remaining profile becomes active; the last one cannot be deleted
    remaining = client.get("/api/profiles").json()["profiles"]
    assert len(remaining) == 1 and remaining[0]["is_active"]
    assert client.delete(f"/api/profiles/{remaining[0]['id']}").status_code == 409


def test_profile_validation_errors_per_field(client):
    r = client.post("/api/profiles", json={"name": "bad/name", "params": {
        "prefix": "1x", "scan_host": "a b", "runtime": "30", "load_first": 9, "load_last": 3,
        "oewizard_path": "./oewizard", "nope": 1}})
    assert r.status_code == 422
    errs = r.json()["errors"]
    assert set(errs) == {"name", "params.prefix", "params.scan_host", "params.runtime", "params.load_last",
                         "params.oewizard_path", "params.nope"}


def test_duplicate_profile_name(client):
    r = client.post("/api/profiles", json={"name": "simulation", "params": {}})
    assert r.status_code == 422 and "name" in r.json()["errors"]


def test_env_check_simulated(client):
    r = client.get("/api/env-check").json()
    assert r["status"] == "pass"
    by_id = {c["id"]: c for c in r["checks"]}
    assert by_id["sysdba"]["status"] == "pass"
    assert "CDB SIMCDB" in by_id["sysdba"]["detail"] and "RAC" in by_id["sysdba"]["detail"]
    for cid in ("sqlplus", "oewizard", "charbench", "config_xml", "log_dir"):
        assert by_id[cid]["status"] == "pass", by_id[cid]


def test_env_check_reports_exact_errors(client, tmp_path):
    p = client.get("/api/profiles/active").json()
    params = {**p["params"], "charbench_path": str(tmp_path / "missing"), "config_xml": "/etc/hostname-nope",
              "rac_cdb": False}
    pid = client.post("/api/profiles", json={"name": "broken", "params": params}).json()["id"]
    r = client.get(f"/api/env-check?profile_id={pid}").json()
    by_id = {c["id"]: c for c in r["checks"]}
    assert r["status"] == "fail"
    assert by_id["charbench"]["status"] == "fail" and "no such file" in by_id["charbench"]["error"]
    assert by_id["config_xml"]["status"] == "fail"
    assert by_id["sysdba"]["status"] == "warn" and "cluster_database" in by_id["sysdba"]["error"]


def test_env_check_sysdba_failure(sim_settings, tmp_path):
    scen = json.loads(sim_settings.sim_scenario.read_text())
    scen["cdb"]["sysdba"] = "ora01031"
    sim_settings.sim_scenario.write_text(json.dumps(scen))
    with TestClient(create_app(sim_settings)) as c:
        sysdba = {x["id"]: x for x in c.get("/api/env-check").json()["checks"]}["sysdba"]
    assert sysdba["status"] == "fail" and sysdba["error"].startswith("ORA-01031")


def test_env_check_sqlplus_missing(sim_settings):
    s = replace(sim_settings, mode="real", extra_env={"PATH": "/nonexistent", "ORACLE_HOME": "", "ORACLE_SID": ""})
    with TestClient(create_app(s)) as c:
        by_id = {x["id"]: x for x in c.get("/api/env-check").json()["checks"]}
    assert by_id["sqlplus"]["status"] == "fail" and by_id["sqlplus"]["error"] == "sqlplus not found in PATH"
    assert by_id["sysdba"]["status"] == "skip"
    assert by_id["oracle_env"]["status"] == "warn"


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"error": "not found"}
