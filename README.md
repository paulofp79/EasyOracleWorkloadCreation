# PDB Swingbench Orchestrator

Web application for Oracle DBAs to create numbered PDBs, load the Swingbench SOE schema into them,
check SOE connections and run one `charbench` workload per PDB at the same time: everything
`reference/*.sh` does, from a browser, with live monitoring, history and safety rails.

**Status: v1 complete (milestones M0–M6).**

| Document | Contents |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | installing on Oracle Linux 8/9 (online or offline bundle), service, direct HTTPS access |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | service, logins, backups, disk, upgrades, troubleshooting, security notes |
| [`docs/IT-ARCHITECTURE.md`](docs/IT-ARCHITECTURE.md) | IT architecture with diagrams: context, deployment, network, components, data, runtime, security, resilience |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | design decisions, process management, parity notes with the scripts |
| [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) | original requirements |

## What it does

* **PDBs**: inventory from `gv$pdbs` (open mode per instance, last connection check, SOE loaded),
  filters, range selection and bulk actions.
* **Create / drop PDBs**: TDE wallet toggle, drop-recreate / skip / abort for existing PDBs, every drop
  confirmed by typing `DELETE` against the exact list.
* **Load SOE**: pre-flight, parallel loads with a limit, resume keyed by `con_uid`, keep-going, force,
  retry failed, import of `create_swingbench.sh` markers.
* **Increase SOE**: grow existing schemas with `sbutil -soe -dup N -parallel P`, one schema or many
  (SOE, BATCH1_SOE, …) across a PDB range or selection, several sbutil processes at a time.
* **Check connections** and **Run workload**: parallel SOE login check, then one charbench per PDB,
  live 250-tile grid with per-PDB logs, Stop all, log scan for ORA-/TNS-/exception text.
* **Runs**: history with filters, downloads (files, zip, CSV), comparison of two workload runs, crash
  recovery with Kill leftovers, audit log.
* **Two layouts**: on a database node (`sqlplus / as sysdba`) or on a separate server (remote SYSDBA,
  SYS password kept in memory per browser session).
* **Access**: localhost + SSH tunnel by default; direct access with login and HTTPS.

SQL and command lines are generated exactly as the reference scripts do (tests compare against the
script text).

## Install (short version)

```bash
# as root
dnf install -y git python3.11 python3.11-pip
# as oracle
git clone -b claude/gracious-ptolemy-uu1i4i https://github.com/paulofp79/EasyOracleWorkloadCreation.git ~/pdb-orchestrator
cd ~/pdb-orchestrator && scripts/install.sh
# as root
~oracle/pdb-orchestrator/scripts/install-service.sh --user oracle
```

Then `ssh -N -L 8080:127.0.0.1:8080 <user>@<host>` and open http://localhost:8080. Offline servers:
`scripts/bundle.sh` on a machine with internet, `scripts/install.sh --offline` on the server. Details,
remote layout and direct HTTPS access: [`docs/INSTALL.md`](docs/INSTALL.md).

## Development

```bash
make venv          # python3.11 venv in .venv with backend + test deps
make sim           # simulation mode on http://127.0.0.1:8080 (no Oracle needed)
make test          # pytest (backend) + vitest (frontend)
```

Simulation (`ORCH_MODE=simulate`) puts stub `sqlplus`, `oewizard` and `charbench` (`tools/stubs/`)
first in the child processes' `PATH`. They emulate a 2-instance RAC CDB `SIMCDB` with
`PDBPP_001`…`PDBPP_250`; per-PDB behaviour (ORA-12514, hang, wrong container, missing schema, load
failure, workload errors, exit codes) is set in `tools/stubs/scenario.json`. `make sim-reset` starts
over.

The built UI (`frontend/dist`) is committed so servers need no Node.js. After changing UI source run
`make frontend` (Node 20+) and commit `frontend/dist`; a frontend test fails if it is stale.
Live-reload development: `make dev-backend` and `make dev-frontend`, then http://127.0.0.1:5173.
`shared/validation-vectors.json` keeps browser and server validation identical.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ORCH_HOST` / `ORCH_PORT` | `127.0.0.1` / `8080` | bind address; a non-loopback address (e.g. `0.0.0.0`) turns on login and HTTPS |
| `ORCH_TLS` | `auto` | HTTPS on network addresses; `on`, or `off` behind a TLS reverse proxy |
| `ORCH_TLS_CERT` / `ORCH_TLS_KEY` | self-signed in `<db dir>/tls/` | your own certificate and key (PEM) |
| `ORCH_AUTH` | off on 127.0.0.1 | `on` requires login also on localhost |
| `ORCH_DB` | `~/.pdb-orchestrator/orchestrator.db` | SQLite database |
| `ORCH_MODE` | `real` | `simulate` uses the stubs in `tools/stubs` |
| `ORCH_HOME` | source checkout | directory holding `frontend/dist` and `tools/stubs` (installed-wheel layouts only) |
| `ORCH_SIM_SCENARIO` | `tools/stubs/scenario.json` | simulation scenario |
