from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from app.config import REPO_ROOT, STUB_DIR, Settings

VECTORS = json.loads((REPO_ROOT / "shared" / "validation-vectors.json").read_text())


@pytest.fixture
def sim_settings(tmp_path: Path) -> Settings:
    """Simulation settings with an isolated state file and a fast clock."""
    scenario = json.loads((STUB_DIR / "scenario.json").read_text())
    scenario["initial_pdbs"] = [{"prefix": "PDBPP", "first": 1, "last": 40, "soe_loaded": True}]
    scenario["delays"] = {k: 0 for k in scenario["delays"]}
    scen_path = tmp_path / "scenario.json"
    scen_path.write_text(json.dumps(scenario))
    return Settings(mode="simulate", db_path=tmp_path / "orch.db", sim_scenario=scen_path,
                    sim_state=tmp_path / "sim" / "state.json", sim_root=tmp_path / "sim")


@pytest.fixture
def stub_env(sim_settings: Settings) -> dict[str, str]:
    return sim_settings.tool_env()


def run_stub(env: dict[str, str], name: str, args: list[str], stdin: str = "", timeout: float = 20):
    return subprocess.run([str(STUB_DIR / name), *args], input=stdin, env=env, capture_output=True,
                          text=True, timeout=timeout)


def pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A zombie still answers kill(0); treat it as gone.
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


@pytest.fixture
def reap_strays():
    pids: list[int] = []
    yield pids
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
