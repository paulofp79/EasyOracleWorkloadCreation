# Operations guide

For installation see [`INSTALL.md`](INSTALL.md). Commands below assume the app lives in
`~oracle/pdb-orchestrator` and runs as `oracle` under systemd.

## Service

| Task | Command |
|---|---|
| Status | `systemctl status pdb-orchestrator` |
| Follow the log | `journalctl -u pdb-orchestrator -f` |
| Start / stop / restart | `systemctl start\|stop\|restart pdb-orchestrator` |
| Change settings | edit `/etc/pdb-orchestrator/orchestrator.env`, then restart |
| Compare settings with a fresh template | `scripts/install-service.sh --dry-run` |
| Remove the service | `systemctl disable --now pdb-orchestrator && rm /etc/systemd/system/pdb-orchestrator.service && systemctl daemon-reload` |

**Stopping or restarting stops a run in progress**: the app sends TERM to every sqlplus / oewizard /
charbench process group of the active run, KILL after 2 s, and marks the run *cancelled*. Restart only
when the dashboard shows no active run. If the app crashes, systemd restarts it after 5 s and the run
it was executing is marked *interrupted*; under systemd the remaining processes of the crashed service
are stopped with it. If the app was killed outside systemd, the run page lists processes that are still
alive and offers **Kill leftovers**.

## Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `ORCH_HOST` / `ORCH_PORT` | `127.0.0.1` / `8080` | bind address; a non-loopback address turns on login and HTTPS |
| `ORCH_MAX_RUNS` | `10` | runs active at the same time (1–100); 1 = the old one-run-at-a-time behaviour |
| `ORCH_TLS` | `auto` | `on`, or `off` behind a reverse proxy that terminates HTTPS |
| `ORCH_TLS_CERT` / `ORCH_TLS_KEY` | self-signed in `~/.pdb-orchestrator/tls/` | your certificate and key (PEM) |
| `ORCH_AUTH` | off on localhost | `on` requires login on localhost too |
| `ORCH_DB` | `~/.pdb-orchestrator/orchestrator.db` | SQLite database |
| `PATH`, `LD_LIBRARY_PATH`, `ORACLE_HOME`, `ORACLE_SID` | from the login shell | sqlplus and (local layout) the CDB |

Per-run settings (prefix, SCAN, paths, defaults) live in **Profiles** in the UI.

## Logins (direct access)

```bash
cd ~/pdb-orchestrator/backend
../venv/bin/python -m app set-password --user <name>     # create, or change a password
../venv/bin/python -m app users                          # list
../venv/bin/python -m app delete-user <name>             # remove
```

Run these as the service user (same database). Passwords: at least 10 characters, stored as scrypt
hashes. Sessions end after 8 h idle, 24 h at most, and on restart. Five wrong passwords from one
address lock it out for 60 s. The **Audit log** page lists logins, failed logins, starts, stops, drops,
Kill leftovers, profile and password changes.

## Data and backups

| What | Where | Back up? |
|---|---|---|
| Profiles, logins, run history, SOE-loaded markers, audit log | `~/.pdb-orchestrator/orchestrator.db` (+ `-wal`) | yes |
| Run artifacts (logs, TSVs, `*-results.xml`) | `<log dir>/run-<id>/`, newest at `<log dir>/latest` | as needed |
| HTTPS certificate | `~/.pdb-orchestrator/tls/` | optional (regenerated if missing) |

Consistent backup of the database while the app runs:

```bash
~/pdb-orchestrator/venv/bin/python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)" \
  ~/.pdb-orchestrator/orchestrator.db /backup/orchestrator-$(date +%F).db
```

(`sqlite3 <db> ".backup '<file>'"` does the same if the sqlite CLI is installed; or stop the service and
copy the file). Secrets are never stored, so the backup holds none.

**Disk**: a workload run writes one charbench log and one results file per PDB; logs grow with the
runtime. Remove old run directories you no longer need (the run history keeps its entries; downloads of
removed runs then report "the run directory no longer exists"):

```bash
find /home/oracle/charbench-logs -maxdepth 1 -name 'run-*' -mtime +30 -exec rm -rf {} +
```

## Upgrading

```bash
cd ~/pdb-orchestrator && git pull && scripts/install.sh
sudo systemctl restart pdb-orchestrator
```

Offline: unpack the new bundle over the directory and run `scripts/install.sh --offline`. The database
schema is migrated on start; a newer database refuses to open with an older program.

## Several SOE schemas in one PDB

Load SOE → **Schema prefix** `BATCH1` creates and loads user `BATCH1_SOE` next to `SOE` (empty = `SOE`).
"Own tablespace for this schema" (default on) uses `BATCH1_SOETBS`; untick it to share `SOETBS`. Use the
same prefix on Check connections and Run workload to drive that schema. The PDBs page lists the schemas
loaded in each PDB.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| Browser: *connection refused* | App listens on 127.0.0.1 only: use the SSH tunnel, or direct access (`ORCH_HOST=0.0.0.0`, firewall port open). |
| `error while attempting to bind ... address already in use` | Another instance (or program) uses the port: `ss -ltnp \| grep :8080`. |
| Service exits: *Login is required ... no login exists yet* | Run `python -m app set-password` as the service user. |
| Environment check: *sqlplus not found in PATH* | Fix `PATH` in the env file (Instant Client or `$ORACLE_HOME/bin`). |
| `ORA-01017` in the SYS password box or a run | Wrong SYS password, or the password file does not allow remote SYSDBA. |
| `ORA-12514` | Wrong service name (CDB root service for SYS; `service suffix` for PDBs). |
| *session is in X, expected CDB$ROOT* | The configured CDB service points at a PDB; use the CDB root service. |
| Load blocked: *not open READ WRITE* | Open the listed PDBs (`ALTER PLUGGABLE DATABASE ... OPEN INSTANCES=ALL`) or adjust the range. |
| Workload blocked by the connection check | Fix the listed PDBs or tick "Skip unreachable PDBs". |
| Words like `soe` appear as `********` in logs | The password equals that word; masking errs on the safe side. Use a stronger password. |
| Run *interrupted* after a restart | The app stopped mid-run; logs are kept. Use **Kill leftovers** if processes are still listed. |

## Security notes

* Runs as the Oracle software owner, never root; binds to localhost unless direct access is configured,
  which requires a login and HTTPS (or a TLS proxy).
* Secrets are typed per run (or once per browser session for SYS), kept in memory only, passed to
  sqlplus on stdin and masked in every log and response. A test pushes unique passwords through every
  operation and searches all files, the database and all API responses for them.
* `oewizard` and `charbench` accept passwords only as command-line arguments: they are visible in `ps`
  on the app host while those processes run. Limit who can log in to that host.
* Every value that reaches SQL or a command line is validated server-side; commands are argument lists
  (no shell); there is no endpoint that runs arbitrary SQL.

## Runs at the same time

Several runs can be active at once (up to `ORCH_MAX_RUNS`, default 10). A run is refused, naming the
run that blocks it, only when it would clash on a PDB:

| New run \ active run on the same PDB | Check | Workload (schema S) | Load (schema S) | Create / Drop |
|---|---|---|---|---|
| Check | ok | ok | ok | refused |
| Workload on S | ok | ok | refused | refused |
| Load of S | ok | refused | refused | refused |
| Create / Drop | refused | refused | refused | refused |

Loads or workloads on *different* schemas (e.g. SOE and BATCH1_SOE) of the same PDB may overlap. Every
run keeps its own log directory, live view and Stop button; the dashboard lists all active runs.
