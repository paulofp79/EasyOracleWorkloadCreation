# PDB Swingbench Orchestrator

Web application for Oracle DBAs to create numbered PDBs, load the Swingbench SOE schema into
them, check SOE connections and run one `charbench` workload per PDB at the same time —
everything `reference/*.sh` does, from a browser, with live monitoring and history.

- Requirements: [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)
- Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Installing on Oracle Linux: [`docs/INSTALL.md`](docs/INSTALL.md)
- Status: **M5** — everything in the scripts plus inventory, live monitoring, run history with filters,
  downloads (files, zip, CSV), compare of workload runs, crash recovery with Kill leftovers, remote CDB
  mode and login/HTTPS for network access. M6 (systemd unit, audit page, packaging) follows.

## Quick start in simulation mode (no Oracle needed)

```bash
make venv          # python3.11 venv in .venv with backend + test deps
# frontend/dist (the built UI) is committed; `make frontend` rebuilds it after UI changes (Node 20+)
make sim           # http://127.0.0.1:8080
```

Simulation (`ORCH_MODE=simulate`) puts the stub `sqlplus`, `oewizard` and `charbench` from
`tools/stubs/` first in the child processes' `PATH`. They emulate a 2-instance RAC CDB `SIMCDB`
with `PDBPP_001`…`PDBPP_250`; behaviour per PDB (ORA-12514, hang, wrong container, missing
schema, load failure, workload errors, exit codes) is set in `tools/stubs/scenario.json`.
State (created/dropped PDBs, loaded schemas) lives in `var/sim/state.json`; `make sim-reset`
starts over.

Live-reload development: `make dev-backend` and `make dev-frontend` in two terminals, then
open http://127.0.0.1:5173.

## Tests

```bash
make test          # pytest (backend) + vitest (frontend)
```

`shared/validation-vectors.json` is used by both test suites so the browser and server
validation rules cannot drift apart.

## Installing on the Oracle host (offline)

The backend runs as the `oracle` OS user (OS-authenticated `sqlplus / as sysdba`) with
**python3.11** (on OL8: `dnf install python3.11`). The host needs no internet access and no Node.js.

On a build machine with internet access:

```bash
make frontend
make wheels        # dist/wheels/*.whl (linux x86_64, cp311) + dist/requirements.lock
```

Copy the repository (including `frontend/dist` and `dist/`) to the host, then as `oracle`:

```bash
python3.11 -m venv ~/pdb-orchestrator/venv
~/pdb-orchestrator/venv/bin/pip install --no-index --find-links dist/wheels -r dist/requirements.lock
~/pdb-orchestrator/venv/bin/pip install --no-index --find-links dist/wheels --no-deps pdb-orchestrator
# ORACLE_HOME, ORACLE_SID and PATH (sqlplus) must be set; ORCH_HOME is the copied repository
ORCH_HOME=$PWD ~/pdb-orchestrator/venv/bin/python -m app
```

A systemd unit and install script follow in M6.

## Access and security

The server binds to `127.0.0.1:8080` by default. For direct access set `ORCH_HOST=0.0.0.0`: that
requires a login (`python -m app set-password`) and serves HTTPS (see `docs/INSTALL.md`, step 7).
Or keep it on localhost and use an SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 oracle@<db-host>
```

Passwords are never stored: they are entered per run, kept in memory, sent to `sqlplus`
on stdin and masked in every log line. `oewizard` and `charbench` only accept passwords as
command-line arguments, so those are visible in `ps` on the host while they run.

| Variable | Default | Meaning |
|---|---|---|
| `ORCH_HOME` | source checkout | directory holding `frontend/dist` and `tools/stubs` |
| `ORCH_MODE` | `real` | `simulate` uses the stubs in `tools/stubs` |
| `ORCH_DB` | `~/.pdb-orchestrator/orchestrator.db` (`var/sim/orchestrator.db` in simulation) | SQLite file |
| `ORCH_HOST` / `ORCH_PORT` | `127.0.0.1` / `8080` | bind address; any non-loopback address (e.g. `0.0.0.0`) turns on login and HTTPS |
| `ORCH_AUTH` | off on 127.0.0.1 | `on` requires login also on localhost |
| `ORCH_TLS` | `auto` | HTTPS on network addresses; `on`, or `off` behind a TLS reverse proxy |
| `ORCH_TLS_CERT` / `ORCH_TLS_KEY` | self-signed in `<db dir>/tls/` | your own certificate and key (PEM) |
| `ORCH_SIM_SCENARIO` | `tools/stubs/scenario.json` | simulation scenario |
