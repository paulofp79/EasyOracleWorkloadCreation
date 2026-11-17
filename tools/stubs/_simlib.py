"""Shared helpers for the simulation stubs (sqlplus, oewizard, charbench).

The stubs emulate just enough of the Oracle / Swingbench tools for the
orchestrator to be developed and tested without a database.

Configuration (read-only):  ORCH_SIM_SCENARIO  (default: scenario.json next to this file)
Mutable state (PDB list):   ORCH_SIM_STATE     (default: <repo>/var/sim/state.json)

The state file is created from the scenario on first use and protected by an
flock, because many stub processes run at the same time.
"""
import contextlib
import fcntl
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def scenario_path():
    return os.environ.get("ORCH_SIM_SCENARIO") or os.path.join(HERE, "scenario.json")


def state_path():
    return os.environ.get("ORCH_SIM_STATE") or os.path.join(REPO, "var", "sim", "state.json")


def load_scenario():
    with open(scenario_path()) as fh:
        return json.load(fh)


SCENARIO = load_scenario()


def time_scale():
    try:
        return float(os.environ.get("ORCH_SIM_TIME_SCALE", SCENARIO.get("time_scale", 1.0)))
    except ValueError:
        return 1.0


def sim_sleep(seconds):
    seconds = float(seconds) * time_scale()
    if seconds > 0:
        time.sleep(seconds)


def delay(name, default=0.0):
    sim_sleep(SCENARIO.get("delays", {}).get(name, default))


def pdb_scenario(pdb, key):
    return SCENARIO.get("pdb_scenarios", {}).get(pdb.upper(), {}).get(key)


def new_con_uid():
    return random.randint(1_000_000_000, 4_294_967_295)


def initial_state():
    cdb = SCENARIO["cdb"]
    n_inst = len(cdb.get("instances", ["inst1"]))
    pdbs = {}
    for group in SCENARIO.get("initial_pdbs", []):
        for i in range(int(group["first"]), int(group["last"]) + 1):
            name = "%s_%03d" % (group["prefix"].upper(), i)
            mode = "READ WRITE"
            if pdb_scenario(name, "open") == "mounted":
                mode = "MOUNTED"
            pdbs[name] = {
                "con_uid": new_con_uid(),
                "open": [mode] * n_inst,
                "soe_loaded": bool(group.get("soe_loaded", False)),
            }
    return {"pdbs": pdbs}


@contextlib.contextmanager
def locked_state(write=False):
    """Yield the state dict under an exclusive lock; save it if write=True."""
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if os.path.exists(path):
                with open(path) as fh:
                    state = json.load(fh)
            else:
                state = initial_state()
                write = True
            yield state
            if write:
                tmp = path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(state, fh, indent=1, sort_keys=True)
                os.replace(tmp, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reset_state():
    with locked_state(write=True) as state:
        state.clear()
        state.update(initial_state())


SERVICE_RE = re.compile(r"//([A-Za-z0-9._-]+):(\d+)/([A-Za-z0-9._-]+)")


def pdb_from_connect(cs):
    """//host:port/pdbpp_001.us.oracle.com -> PDBPP_001"""
    m = SERVICE_RE.search(cs or "")
    if not m:
        return None
    return m.group(3).split(".", 1)[0].upper()


def arg_value(argv, flag):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def out(*lines):
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()
