"""Process-wide settings, read once from the environment.

ORCH_HOME      installation directory (default: the source checkout containing this file)
ORCH_MODE      real (default) | simulate
ORCH_DB        SQLite file (default ~/.pdb-orchestrator/orchestrator.db; simulate: <repo>/var/sim/orchestrator.db)
ORCH_HOST      bind address (default 127.0.0.1; anything else needs auth, which arrives in M6)
ORCH_PORT      default 8080
ORCH_SIM_SCENARIO / ORCH_SIM_STATE   simulation scenario and state files (simulate mode only)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ORCH_HOME points at the installation directory (holding frontend/dist and tools/stubs) when the
# backend is installed as a wheel; from a source checkout it is found relative to this file.
REPO_ROOT = Path(os.environ.get("ORCH_HOME") or Path(__file__).resolve().parents[2]).resolve()
STUB_DIR = REPO_ROOT / "tools" / "stubs"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


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

    @property
    def simulate(self) -> bool:
        return self.mode == "simulate"

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
    return Settings(
        mode=mode,
        db_path=Path(os.environ.get("ORCH_DB", default_db)),
        host=os.environ.get("ORCH_HOST", "127.0.0.1"),
        port=int(os.environ.get("ORCH_PORT", "8080")),
        sim_scenario=Path(os.environ.get("ORCH_SIM_SCENARIO", STUB_DIR / "scenario.json")),
        sim_state=Path(os.environ.get("ORCH_SIM_STATE", sim_root / "state.json")),
        sim_root=sim_root,
    )
