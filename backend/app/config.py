"""Process-wide settings, read once from the environment.

ORCH_HOME      installation directory (default: the source checkout containing this file)
ORCH_MODE      real (default) | simulate
ORCH_DB        SQLite file (default ~/.pdb-orchestrator/orchestrator.db; simulate: <repo>/var/sim/orchestrator.db)
ORCH_HOST      bind address (default 127.0.0.1). Any other address (e.g. 0.0.0.0) turns on login and HTTPS.
ORCH_PORT      default 8080
ORCH_AUTH      on: require login even on 127.0.0.1 (default: only when ORCH_HOST is not loopback)
ORCH_TLS       auto (default: HTTPS when ORCH_HOST is not loopback) | on | off (plain HTTP behind a TLS proxy)
ORCH_TLS_CERT / ORCH_TLS_KEY   your certificate and key (PEM); default: a self-signed pair generated
               once in <db dir>/tls/
ORCH_SIM_SCENARIO / ORCH_SIM_STATE   simulation scenario and state files (simulate mode only)
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

# ORCH_HOME points at the installation directory (holding frontend/dist and tools/stubs) when the
# backend is installed as a wheel; from a source checkout it is found relative to this file.
REPO_ROOT = Path(os.environ.get("ORCH_HOME") or Path(__file__).resolve().parents[2]).resolve()
STUB_DIR = REPO_ROOT / "tools" / "stubs"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    mode: str = "real"
    db_path: Path = Path.home() / ".pdb-orchestrator" / "orchestrator.db"
    host: str = "127.0.0.1"
    port: int = 8080
    sim_scenario: Path = STUB_DIR / "scenario.json"
    sim_state: Path = REPO_ROOT / "var" / "sim" / "state.json"
    sim_root: Path = REPO_ROOT / "var" / "sim"
    extra_env: dict = field(default_factory=dict)
    auth: bool = False                 # login required for the API
    tls: bool = False                  # serve HTTPS
    secure_cookies: bool = False       # browser reaches us over HTTPS (directly or through a proxy)
    tls_cert: Path | None = None
    tls_key: Path | None = None
    max_runs: int = 10                 # runs active at the same time (ORCH_MAX_RUNS)

    @property
    def simulate(self) -> bool:
        return self.mode == "simulate"

    @property
    def tls_dir(self) -> Path:
        return self.db_path.parent / "tls"

    def tool_env(self) -> dict[str, str]:
        """Environment for every child process (sqlplus, oewizard, charbench)."""
        env = dict(os.environ)
        env.update(self.extra_env)
        if self.simulate:
            env["PATH"] = f"{STUB_DIR}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}"
            env["ORCH_SIM_SCENARIO"] = str(self.sim_scenario)
            env["ORCH_SIM_STATE"] = str(self.sim_state)
        return env


def load_settings() -> Settings:
    mode = os.environ.get("ORCH_MODE", "real").strip().lower()
    if mode not in ("real", "simulate"):
        raise SystemExit(f"ORCH_MODE must be 'real' or 'simulate', not {mode!r}")
    sim_root = Path(os.environ.get("ORCH_SIM_ROOT", REPO_ROOT / "var" / "sim"))
    default_db = (sim_root / "orchestrator.db") if mode == "simulate" else \
        Path.home() / ".pdb-orchestrator" / "orchestrator.db"
    host = os.environ.get("ORCH_HOST", "127.0.0.1")
    exposed = not is_loopback(host)
    auth = exposed or os.environ.get("ORCH_AUTH", "").strip().lower() in ("1", "on", "true", "yes")
    tls_mode = os.environ.get("ORCH_TLS", "auto").strip().lower()
    if tls_mode not in ("auto", "on", "off"):
        raise SystemExit(f"ORCH_TLS must be auto, on or off, not {tls_mode!r}")
    tls = tls_mode == "on" or (tls_mode == "auto" and exposed)
    cert, key = os.environ.get("ORCH_TLS_CERT"), os.environ.get("ORCH_TLS_KEY")
    if bool(cert) != bool(key):
        raise SystemExit("Set both ORCH_TLS_CERT and ORCH_TLS_KEY, or neither.")
    return Settings(
        mode=mode,
        db_path=Path(os.environ.get("ORCH_DB", default_db)),
        host=host,
        port=int(os.environ.get("ORCH_PORT", "8080")),
        auth=auth,
        tls=tls or bool(cert),
        # behind a TLS proxy (ORCH_TLS=off on a network address) the browser still uses https
        secure_cookies=tls or bool(cert) or exposed,
        tls_cert=Path(cert) if cert else None,
        tls_key=Path(key) if key else None,
        sim_scenario=Path(os.environ.get("ORCH_SIM_SCENARIO", STUB_DIR / "scenario.json")),
        sim_state=Path(os.environ.get("ORCH_SIM_STATE", sim_root / "state.json")),
        sim_root=sim_root,
        max_runs=_max_runs(),
    )


def _max_runs() -> int:
    raw = os.environ.get("ORCH_MAX_RUNS", "10").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 100:
        raise SystemExit(f"ORCH_MAX_RUNS must be a number from 1 to 100, not {raw!r}")
    return int(raw)
