"""Login, CSRF, lockout, settings rules, and a real HTTPS server on 0.0.0.0."""
import os
import socket
import stat
import subprocess
import sys
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import AUTH_COOKIE, CSRF_COOKIE, MAX_FAILURES, hash_password, verify_password
from app.config import REPO_ROOT, load_settings
from app.db import Database
from app.main import create_app

PW = "Correct-Horse-41"


def test_hash_and_verify():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h and h != hash_password(PW)    # salted
    assert verify_password(PW, h) and not verify_password("wrong", h)
    assert not verify_password(PW, None) and not verify_password(PW, "garbage")


@pytest.mark.parametrize("env,expect", [
    ({}, (False, False, False)),
    ({"ORCH_HOST": "0.0.0.0"}, (True, True, True)),
    ({"ORCH_HOST": "0.0.0.0", "ORCH_TLS": "off"}, (True, False, True)),       # behind a TLS proxy
    ({"ORCH_AUTH": "on"}, (True, False, False)),                               # login through an SSH tunnel
    ({"ORCH_TLS": "on"}, (False, True, True)),
])
def test_settings_rules(monkeypatch, env, expect):
    for k in ("ORCH_HOST", "ORCH_TLS", "ORCH_AUTH", "ORCH_TLS_CERT", "ORCH_TLS_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    s = load_settings()
    assert (s.auth, s.tls, s.secure_cookies) == expect


@pytest.fixture
def client(sim_settings):
    db = Database(sim_settings.db_path)
    db.set_user("admin", hash_password(PW))
    db.close()
    with TestClient(create_app(replace(sim_settings, auth=True))) as c:
        yield c


def login(c, user="admin", pw=PW):
    return c.post("/api/auth/login", json={"username": user, "password": pw})


def test_api_needs_login_and_csrf(client, sim_settings):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/state").json() == {"auth_enabled": True, "user": None, "tls": False}
    assert client.get("/api/profiles").status_code == 401
    assert client.get("/").status_code != 401                         # the UI itself loads (login page)
    assert login(client, pw="nope").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json()["user"] == "admin"
    cookies = r.headers.get_list("set-cookie")
    auth_c = next(c for c in cookies if c.startswith(AUTH_COOKIE))
    assert "httponly" in auth_c.lower() and "samesite=strict" in auth_c.lower()
    assert any(c.startswith(CSRF_COOKIE) and "httponly" not in c.lower() for c in cookies)
    csrf = r.json()["csrf"]
    assert client.get("/api/profiles").status_code == 200
    assert client.get("/api/auth/state").json()["user"] == "admin"
    body = {"name": "lab", "params": {}}
    assert client.post("/api/profiles", json=body).status_code == 403                          # no token
    assert client.post("/api/profiles", json=body, headers={"X-CSRF-Token": "x" * 32}).status_code == 403
    assert client.post("/api/profiles", json=body, headers={"X-CSRF-Token": csrf}).status_code == 201
    db = Database(sim_settings.db_path)
    actors = [r["actor"] for r in db.conn.execute("SELECT actor FROM audit_log WHERE action = 'profile_create'")]
    db.close()
    assert actors == ["admin"]
    # runs and their live events work with the session cookie
    rid = client.post("/api/runs", json={"type": "check", "params": {"first": 14, "last": 15},
                                         "secrets": {"soe_password": "soe-pw-1"}},
                      headers={"X-CSRF-Token": csrf}).json()["run_id"]
    for _ in range(100):
        if client.get(f"/api/runs/{rid}").json()["status"] != "running":
            break
        time.sleep(0.1)
    assert "event: end" in client.get(f"/api/runs/{rid}/events?last_id=0").text
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/profiles").status_code == 401


def test_lockout_after_failures(client):
    for _ in range(MAX_FAILURES):
        assert login(client, pw="wrong").status_code == 401
    r = login(client)                                      # even the right password waits now
    assert r.status_code == 429 and "try again" in r.json()["error"]


def test_session_expires(client):
    login(client)
    client.app.state.auth.clock = lambda: time.time() + 9 * 3600     # idle longer than 8 h
    assert client.get("/api/profiles").status_code == 401


def test_without_auth_nothing_changes(sim_settings):
    with TestClient(create_app(sim_settings)) as c:
        assert c.get("/api/profiles").status_code == 200
        assert c.post("/api/profiles", json={"name": "x1", "params": {}}).status_code == 201


# ---- real server ---------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_cli(args, env, stdin=""):
    return subprocess.run([sys.executable, "-m", "app", *args], cwd=REPO_ROOT / "backend", env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)


def test_https_server_on_all_interfaces(sim_settings, tmp_path):
    port = free_port()
    env = {**os.environ, "ORCH_MODE": "simulate", "ORCH_HOST": "0.0.0.0", "ORCH_PORT": str(port),
           "ORCH_DB": str(tmp_path / "net" / "orch.db"), "ORCH_SIM_ROOT": str(sim_settings.sim_root),
           "ORCH_SIM_SCENARIO": str(sim_settings.sim_scenario), "ORCH_SIM_STATE": str(sim_settings.sim_state)}
    for k in ("ORCH_TLS", "ORCH_AUTH", "ORCH_TLS_CERT", "ORCH_TLS_KEY"):
        env.pop(k, None)
    # no login yet: refuses to start
    r = run_cli([], env)
    assert r.returncode == 2 and "set-password" in r.stderr
    assert run_cli(["set-password"], env, "short\n").returncode == 2
    r = run_cli(["set-password"], env, PW + "\n")
    assert r.returncode == 0 and "Created login 'admin'" in r.stdout
    assert run_cli(["users"], env).stdout.strip() == "admin"

    server = subprocess.Popen([sys.executable, "-m", "app"], cwd=REPO_ROOT / "backend", env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"https://127.0.0.1:{port}"
    try:
        with httpx.Client(verify=False, timeout=5) as h:
            for _ in range(100):
                try:
                    if h.get(f"{base}/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.1)
            assert h.get(f"{base}/api/profiles").status_code == 401
            r = h.post(f"{base}/api/auth/login", json={"username": "admin", "password": PW})
            assert r.status_code == 200
            assert all("secure" in c.lower() for c in r.headers.get_list("set-cookie"))
            assert h.get(f"{base}/api/profiles").status_code == 200
            assert h.get(f"{base}/").status_code == 200
        with pytest.raises(httpx.TransportError):
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=3)    # plain HTTP is not served
        key = tmp_path / "net" / "tls" / "key.pem"
        assert key.is_file() and stat.S_IMODE(key.stat().st_mode) == 0o600
        assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
        cert_text = subprocess.run(["openssl", "x509", "-in", str(key.parent / "cert.pem"), "-noout", "-text"],
                                   capture_output=True, text=True).stdout
        assert f"DNS:{socket.gethostname()}" in cert_text
    finally:
        server.terminate()
        server.wait(10)
