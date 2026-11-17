# Project context: PDB Swingbench Orchestrator (working title, rename freely)

Paste this whole document into Claude Code as the first message of a new project (or save it as `docs/PROJECT_CONTEXT.md` and paste only the "Kickoff prompt" at the end). Put the three reference scripts in `./reference/` before starting:

- `reference/create_pdbs.sh`
- `reference/create_swingbench.sh` (also called `load_swingbench.sh`)
- `reference/run_charbench_all_pdbs.sh`

Those scripts are the source of truth for **behavior**. This document describes what to build around them and what is new. If the two disagree about existing behavior, the scripts win; if this document asks for something new, this document wins.

---

## 1. Goal

Build a web application that lets an Oracle DBA do, from a browser, everything the three bash scripts do today:

1. **Create** a numbered range of pluggable databases (PDBs) in a CDB (optionally dropping existing ones, optionally creating a TDE master key per PDB).
2. **Load** the Swingbench **SOE** schema into each PDB (`oewizard`), with bounded parallelism, resume and failure handling.
3. **Check connections** to every PDB as the SOE user, and only if they are healthy,
4. **Run a workload**: one `charbench` per PDB, all at the same time, monitored live, with logs, result files and a summary.

The use case is PDB consolidation / density testing: up to 250 PDBs (names `PDBPP_001` … `PDBPP_250`) on an Exadata RAC CDB, each driven by its own Swingbench workload at the same time. The UI must stay usable at that scale (hundreds of items, live updates) and must make long, destructive, or heavy operations safe and visible.

Primary user: an Oracle DBA / performance engineer (expert in Oracle RAC, Exadata, Swingbench). Not a general audience. Prefer precision and information density over hand-holding, but keep destructive actions explicit.

## 2. Environment and constraints (important)

- The backend runs on a **Linux host as the `oracle` OS user** (a DB node or an admin host with an Oracle client), because the scripts use OS authentication: `sqlplus -L -s / as sysdba` against the local CDB root. `ORACLE_HOME`, `ORACLE_SID` and `PATH` (sqlplus) must be set for that user. Do not assume Docker is usable for the backend; it needs host binaries.
- External tools used (all must be found/validated by an "Environment check" screen): `sqlplus`, Swingbench `oewizard` and `charbench` (path configurable; default `charbench` = `/home/oracle/swingbench/bin/charbench`, config XML default `/home/oracle/swingbench/configs/SOE_Server_Side_V2.xml`), plus standard Linux tools.
- Clients connect to PDBs through Easy Connect on the SCAN: `//<scan-host>:<port>/<service>`. Example values from the scripts: SCAN host `scaqaq01-scan1.us.oracle.com`, port `1521`, service = lower-case PDB name + optional suffix (e.g. `.us.oracle.com`).
- The CDB is RAC (script variable `RAC_CDB=true`), but single-instance must also work (`RAC_CDB=false`).
- Credentials needed: PDB admin password (new PDBs), TDE wallet password (optional), SOE user password, SYS password (only `oewizard` needs it, via `-dbap`).
- v1 target: one CDB, one backend host, one or a few users. No multi-tenant SaaS concerns.

## 3. Scope of v1 (modules)

### A. Settings and environment check
- Named **profiles** holding the connection and default parameters (section 5). Switchable in the UI.
- Environment check page: sqlplus found; `/ as sysdba` works and shows CDB name, version, instance count (RAC or not); oewizard/charbench executable; config XML readable; free disk in the log directory. Show pass/fail per item with the exact error.

### B. PDB inventory
- Table of PDBs matching the prefix: name, `con_uid`, open mode per instance (`gv$pdbs`), whether SOE was loaded by this tool (and when), last connection-check result and time.
- Refresh button plus auto-refresh. Filter by range, state and text. Select a range or arbitrary set for bulk actions.

### C. Create PDBs
- Inputs: prefix, first, last, PDB admin password, RAC yes/no, **Use TDE wallet yes/no** (when no, skip every TDE step; when yes, wallet password required).
- Before creating anything, list which PDBs of the range already exist and ask what to do: **Drop and recreate** (type-to-confirm, shows the exact list and "including datafiles"), **Skip existing**, or **Abort**.
- Creation is sequential (one PDB at a time), exactly as the script; show per-PDB progress and elapsed time. Stop on first failure (show the SQL*Plus output tail).

### D. Load Swingbench SOE schema
- Inputs: first, last, prefix, scale, threads (`-tc`), tablespace, parallel loaders, keep-going, force, SOE and SYS passwords, connection settings.
- Pre-flight in one query: every PDB must exist and be open READ WRITE, otherwise list the problem PDBs and do not start.
- Skip PDBs already loaded successfully by this tool (tracked per PDB and `con_uid`; a dropped and recreated PDB has a new `con_uid` and is loaded again). "Force reload" overrides.
- Sliding-window parallelism (start a new load the moment a slot frees up). On a failure, do not start new PDBs unless keep-going; let running ones finish; final summary: loaded / skipped / failed / not started, with a "retry failed" button.
- Dry-run: show the plan (PDB list, counts, masked `oewizard` command) without changing anything.

### E. Connection check
- Parallel (default 16) SOE login test of every selected PDB with a per-PDB timeout (default 30 s). Result per PDB: OK or FAIL with a reason (ORA-/TNS- text, timeout, wrong container, schema missing).
- Used standalone ("Check connections" button) and automatically as step 1 of a workload run.
- Policy when some PDBs fail: **abort** (default) or **continue with the ones that passed** ("skip unreachable").

### F. Run workload
- Inputs: first, last, prefix, users per PDB (`-uc`), runtime `HH:MM`, config XML, disabled transactions list, start delay between launches, connection check settings, skip-unreachable, SOE password.
- Flow: connection check, then (if allowed) start one `charbench` per PDB **all at once**, optional start delay, each in its own process group. Report each workload as soon as it ends (not in launch order). Periodic heartbeat (running count, elapsed).
- After the run: exit codes, durations, and a scan of every charbench log for error text (ORA-, TNS-, `SQLException`, `Exception`, connection refused/reset) because exit code 0 does not prove the database had no errors. Show PDBs with error text and the first error line.
- **Stop all** button (graceful TERM to every process group, then KILL after 2 s).

### G. Runs and history
- Every operation is a **Run** with an id, type, redacted parameters, status, timestamps and per-PDB items. Runs survive browser reloads and backend restarts (persist in SQLite). A run left "running" after a backend restart becomes **interrupted** (and the UI says so; check whether PIDs are still alive before claiming).
- Run detail page: per-PDB table, log viewer (tail and full download), artifacts (`<PDB>-results.xml`, TSV summaries, logs as a zip), CSV export of the summary.

### H. Live monitoring
- A **grid of tiles**, one per PDB, colored and labeled by state (pending, checking, running, ok, failed, skipped, not started, cancelled). Do not rely on color alone (use a text label/icon). Click a tile to open a drawer with live log tail and details.
- Counters and progress bar (done / total, failed, elapsed). Server-Sent Events (or WebSocket) for updates; must stay smooth with 250 items.

## 4. Behavior contract (extracted from the scripts)

### 4.1 Naming and ranges
- PDB name = `UPPER(prefix)` + `_` + zero-padded 3-digit number, e.g. `PDBPP_007`.
- Service name = `lower(pdb)` + service suffix (possibly empty). Connect string = `//<scan-host>:<scan-port>/<service>`.
- Range: `1 <= first <= last <= 999`, integers without leading zeros.
- Prefix: `^[A-Za-z][A-Za-z0-9_]*$`, at most 26 characters (so prefix + `_` + 3 digits fits Oracle's 30-character limit).

### 4.2 Create PDBs (SQL as the script runs it, via `sqlplus -L -s / as sysdba`, SQL on stdin)
```sql
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET DEFINE OFF
CREATE PLUGGABLE DATABASE <PDB> ADMIN USER pdbadmin IDENTIFIED BY "<pdb_admin_password>";
ALTER PLUGGABLE DATABASE <PDB> OPEN INSTANCES=ALL;          -- RAC
-- single instance instead:  ALTER PLUGGABLE DATABASE <PDB> OPEN;
--                           ALTER PLUGGABLE DATABASE <PDB> SAVE STATE;
ALTER SYSTEM REGISTER;
-- only when "Use TDE wallet" is on:
ALTER SESSION SET CONTAINER = <PDB>;
ADMINISTER KEY MANAGEMENT SET KEY FORCE KEYSTORE IDENTIFIED BY "<wallet_password>" WITH BACKUP CONTAINER = CURRENT;
EXIT
```
- Why the wallet toggle exists: some CDBs have no wallet configured and the `ADMINISTER KEY MANAGEMENT` step fails there. With the toggle off, none of the TDE statements and no wallet password prompt.

### 4.3 Drop existing PDBs (only after explicit confirmation)
```sql
-- RAC (for non-RAC use CLOSE IMMEDIATE without INSTANCES=ALL)
DECLARE
  l_open_instances PLS_INTEGER;
BEGIN
  SELECT COUNT(*) INTO l_open_instances FROM gv$pdbs
  WHERE name = '<PDB>' AND open_mode <> 'MOUNTED';
  IF l_open_instances > 0 THEN
    EXECUTE IMMEDIATE 'ALTER PLUGGABLE DATABASE <PDB> CLOSE IMMEDIATE INSTANCES=ALL';
  END IF;
END;
/
DROP PLUGGABLE DATABASE <PDB> INCLUDING DATAFILES;
```

### 4.4 Inventory / pre-flight query
```sql
SELECT name || '|' || con_uid || '|' || SUM(CASE WHEN open_mode = 'READ WRITE' THEN 1 ELSE 0 END)
FROM   gv$pdbs
WHERE  name LIKE '<PREFIX>%'
GROUP  BY name, con_uid
ORDER  BY name;
```
Query by prefix and filter the exact names in application code. Do **not** build a huge `IN ('A','B',...)` list: SQL*Plus has a per-line input limit (about 2,500 characters on older versions), and a 250-name list on one line is over 3,000 characters.

### 4.5 Load SOE schema, per PDB (two steps)
```sql
-- step 1: sqlplus -L -s / as sysdba
ALTER SESSION SET CONTAINER = <PDB>;
GRANT DBA TO soe IDENTIFIED BY "<soe_password>";      -- creates the SOE user if missing, or resets its password
EXIT SUCCESS
```
```
# step 2
oewizard -cl -create -cs //<scan>:<port>/<service> -u soe -p <soe_password> \
         -scale <scale> -tc <threads> -dba "sys as sysdba" -dbap <sys_password> \
         -ts <tablespace> -async_off -v
```
- Output of each step goes to its own log file; a non-zero exit of either step fails that PDB.
- On success record the PDB's `con_uid` as "loaded". Skip a PDB if its recorded `con_uid` equals the current one (unless force).
- Failure policy: after the first failure, stop starting new PDBs (unless keep-going), let running ones finish, report. Re-running the same request retries only failed and not-started PDBs.

### 4.6 Connection check, per PDB (password via stdin, never on a command line)
```
timeout -k 5 <timeout_s> sqlplus -L -s /nolog        # stdin:
WHENEVER SQLERROR EXIT FAILURE
SET DEFINE OFF
SET PAGESIZE 0 FEEDBACK OFF HEADING OFF VERIFY OFF ECHO OFF
SET LINESIZE 300 TRIMSPOOL ON
CONNECT soe/"<soe_password>"@//<scan>:<port>/<service>
SELECT 'SOE_CHECK_OK|' || SYS_CONTEXT('USERENV','CON_NAME') || '|' || SYS_CONTEXT('USERENV','INSTANCE_NAME') || '|' ||
       (SELECT COUNT(*) FROM user_tables WHERE table_name IN ('CUSTOMERS','ORDERS','ORDER_ITEMS','PRODUCT_INFORMATION'))
FROM dual;
EXIT
```
Decide the result by **parsing the output token**, not just the exit code (a failed `CONNECT` does not reliably give a non-zero exit):
- OK: token present, container name equals the PDB name (case-insensitive), table count is 4. Store the instance name.
- FAIL reasons: timed out after N s; first `ORA-`/`TNS-`/`SP2-` line of the output; "connected to container X, expected Y"; "SOE schema missing or incomplete (n/4 core tables)"; "no output from sqlplus".

### 4.7 Workload, per PDB
```
charbench -cs //<scan>:<port>/<service> -u soe -p <soe_password> -c <config.xml> \
          -v users,tpm,tps,errs,vresp -intermin 0 -intermax 0 -min 0 -max 0 \
          -uc <users> [-di <SQ,WQ,WA>] -rt <HH:MM> -r <run_dir>/<PDB>-results.xml
```
- stdout+stderr go to `<run_dir>/<PDB>-charbench.log`. `-di` is omitted when the disabled list is empty. Default disabled list: `SQ,WQ,WA`. `-v` includes `errs` so the log has an Errors column.
- Run from the Swingbench `bin` directory (resolve all paths to absolute first).
- Launch every PDB's charbench before waiting for any (optional start delay between launches). Each workload is its own **process group/session** so cancel can kill the Java children too.
- Error scan regex for the logs: `ORA-[0-9]{5}|TNS-[0-9]{5}|SQLException|Exception|Connection refused|Connection reset`. Report per-PDB count and the first matching line; this is informational and does not change the exit status.
- Process exit status of the whole run is failure if any charbench exited non-zero (or the connection check blocked the run).

### 4.8 Cancellation (all operations)
Send TERM to each running process group (or process tree for sqlplus/oewizard), wait about 2 s, then KILL. Make sure `sqlplus`, `oewizard`, `charbench` and their Java children are gone. Mark items cancelled and the run cancelled. Background shell jobs ignore Ctrl-C by default, which is why this must be explicit.

## 5. Parameters and defaults (each becomes a form field; defaults come from the active profile)

| Group | Parameter | Default | Validation |
|---|---|---|---|
| Common | prefix | `PDBPP` | section 4.1 |
| Common | first / last | create: 1 / 250; load: 222 / 250 (user has also used 3 / 38); workload: 1 / 250 | 1 ≤ first ≤ last ≤ 999 |
| Connection | SCAN host | `scaqaq01-scan1.us.oracle.com` (example) | `^[A-Za-z0-9._-]+$` |
| Connection | SCAN port | `1521` | 1–65535 |
| Connection | service suffix | empty (e.g. `.us.oracle.com`) | `^[A-Za-z0-9._-]*$` |
| Create | RAC CDB | true | boolean |
| Create | use TDE wallet | ask (no default; must be explicit) | boolean |
| Create | PDB admin password | prompt | non-empty; no `"` |
| Create | wallet password | prompt, only if wallet on | non-empty; no `"` |
| Load | scale | `0.1` | positive number |
| Load | threads (`-tc`) | `16` | positive integer |
| Load | tablespace (`-ts`) | `SOETBS` | `^[A-Za-z][A-Za-z0-9_$#]*$` |
| Load | parallel loaders | `1` (user has used 16) | positive integer |
| Load | keep going / force | false / false | boolean |
| Load | oewizard path | `./oewizard` | executable |
| Load | SOE password, SYS password | prompt | non-empty; SOE: no `"` |
| Check | timeout | `30` s | positive integer |
| Check | check parallelism | `16` | positive integer |
| Check | skip unreachable | false | boolean |
| Workload | users (`-uc`) | `8` | positive integer |
| Workload | runtime (`-rt`) | `00:30` | `^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$` |
| Workload | charbench path | `/home/oracle/swingbench/bin/charbench` | executable |
| Workload | config XML | `/home/oracle/swingbench/configs/SOE_Server_Side_V2.xml` | readable |
| Workload | disabled transactions | `SQ,WQ,WA` (empty allowed = none) | `^[A-Za-z0-9,]*$` |
| Workload | start delay | `0` s | non-negative number |
| Storage | log / run directory | `/home/oracle/charbench-logs` | writable dir |

Every field is validated **server-side** with these rules (values end up in SQL and command lines); the UI validation is only a convenience. Mirror the validators in one shared module with unit tests.

## 6. Suggested architecture (you may propose a better one in the plan)

- **Backend:** Python 3.11+ with **FastAPI**, `asyncio` subprocess management, **SQLite** (SQLAlchemy or plain `sqlite3`) for profiles, runs, items and "loaded" markers. SSE endpoint for live events. Uvicorn, single process, no Celery/Redis needed.
- **Frontend:** a small SPA (React + Vite + TypeScript, or SvelteKit) served by the same backend as static files. Table/virtualized-list component for large grids.
- **Operation engine:** one `Operation` class per module (create, drop, load, check, workload) with `plan()` (dry run), `run()` (async generator of events), `cancel()`. A central **ProcessRunner** that: starts subprocesses with `start_new_session=True`, feeds stdin, streams stdout/stderr to log files, enforces timeouts, kills process groups (`os.killpg`) and falls back to process-tree kill for non-leaders. All concurrency limits (loaders, connection checks) are semaphores in the engine.
- **SQL*Plus access:** v1 uses `sqlplus` subprocesses exactly like the scripts (parity, OS auth). An optional later step may use `python-oracledb` for read-only queries.
- **Run state:** every item has a state machine `pending → running → ok | failed | skipped | cancelled | not_started`. Events (`run.updated`, `item.updated`, `log.append`, heartbeat) are persisted and pushed via SSE; the UI can reconnect and resync from the REST API.
- **Artifacts:** per run directory `<log_dir>/run-<timestamp>-<id>/` holding logs, results XML, `connections.tsv`, `status.tsv`, `summary.tsv`. Keep a `latest` symlink. The DB stores paths and summaries; large logs stay on disk and are tailed on demand.
- **Layout idea:**
```
backend/app/{main.py, api/, engine/, ops/{create_pdbs,drop_pdbs,load_soe,check_conn,workload}.py, db/, validators.py, redact.py}
backend/tests/
frontend/src/{pages,components,api}
tools/stubs/{sqlplus,oewizard,charbench}      # simulation mode, section 9
reference/                                    # the three bash scripts
docs/{PROJECT_CONTEXT.md,ARCHITECTURE.md}
```

## 7. Web UI specification

Pages (left navigation):
1. **Dashboard:** environment status, active run (if any) with live progress, last runs.
2. **Profiles / Settings:** CRUD of profiles, environment check results.
3. **PDBs:** inventory table with filters, bulk select, actions (check connections, load SOE, create, drop).
4. **Create PDBs** / **Load SOE** / **Check connections** / **Run workload:** one form each. Every form has: all parameters from section 5 with defaults and inline validation; a **Plan / dry run** button (what will happen, counts, which PDBs exist/skip/fail pre-flight); an **"Equivalent command"** box showing the CLI-style command with secrets masked; a clearly separated **Start** button.
5. **Run detail (live):** tile grid + table toggle, counters, progress bar, filters (state, text), per-PDB drawer with live log, **Stop all** button, summary at the end with "Retry failed", "Download artifacts", "Export CSV".
6. **Runs:** history with filters and compare of two workload runs (users, runtime, durations, error counts).

UX rules:
- Destructive actions (drop PDBs, force reload, stop all) need explicit confirmation; dropping requires typing `DELETE` and shows the PDB list.
- Secrets use password inputs, are sent once per operation, held only in memory for the life of the run, never written to disk, DB or logs, and never echoed back by the API. Provide an optional "remember for this browser session" only if kept in memory server-side with an expiry.
- Show server-side validation errors next to the field. Never show a stack trace; show a short message plus a link to the log.
- Warn before heavy workload starts (for example `PDBs × users` large, or no start delay with 100+ PDBs): "N concurrent Java processes and M sessions".
- Keyboard accessible, readable at 1366×768 and up, light and dark theme optional.

## 8. API sketch (REST + SSE; adjust as needed)

- `GET/PUT /api/profiles`, `GET /api/env-check`
- `GET /api/pdbs?prefix=&first=&last=` (live from `gv$pdbs` plus stored state)
- `POST /api/runs` body `{type, params, secrets, dry_run}` returns the plan when `dry_run`, otherwise `{run_id}`. `type` ∈ `create | drop | load | check | workload`. The `workload` type includes the connection check phase.
- `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/items`
- `GET /api/runs/{id}/events` (SSE), `POST /api/runs/{id}/cancel`
- `GET /api/runs/{id}/items/{pdb}/log?tail=N`, `GET /api/runs/{id}/artifacts.zip`, `GET /api/runs/{id}/summary.csv`
- One active run at a time per CDB by default (reject or queue a second one that would conflict, for example drop during workload).

## 9. Testing and simulation mode (required, build this first)

You cannot assume an Oracle database is available while developing. Build a **simulation mode** (`ORCH_MODE=simulate`) that points the ProcessRunner at stub executables in `tools/stubs/` and make the whole app runnable and testable without Oracle:
- `sqlplus` stub: reads stdin, answers the inventory query with a configurable list of PDBs (con_uid, open modes), accepts create/drop/grant SQL, and for the connection check returns `SOE_CHECK_OK|<PDB>|inst1|4` or one of: `ORA-12514: TNS:listener does not currently know of service requested in connect descriptor` plus `SP2-0640: Not connected`, a hang (sleep) to test timeouts, a wrong container name, or 0 tables. Behavior is controlled by env vars or a small JSON file listing PDB → scenario.
- `oewizard` stub: sleeps a configurable time, can fail for chosen PDBs.
- `charbench` stub: records its arguments, prints a Swingbench-like header and a few table lines, can sleep long (to test cancel and process-group kill), can print `java.sql.SQLException: ORA-00942...`, can exit non-zero, and spawns a child process so group kill can be verified.

Tests to write:
- Unit: validators, name/service builders, command and SQL builders (golden tests against the SQL/command text in section 4), secret redaction, log-error scanner.
- Engine: sliding-window concurrency never exceeds the limit; failure policy (stop vs keep-going); resume via `con_uid`; check timeouts; skip-unreachable; cancel leaves no orphan processes (assert by PID/pgid); backend restart marks runs interrupted.
- API: SSE reconnect and resync; validation errors; secret never present in any response, DB row or log file (grep the run directory for the test password).
- UI: Playwright smoke test of the main flows in simulation mode with 250 PDBs (grid stays responsive).

## 10. Security requirements

- No shell interpolation: pass argument lists (`shell=False`), never build commands from raw strings. Validate every input with the section 5 rules before it reaches SQL or a command line.
- No arbitrary-SQL endpoint. Only the whitelisted operations above run privileged SQL.
- Passwords reach `sqlplus` through **stdin only**. Known limitation to document in the UI: `oewizard` and `charbench` accept the password only as a command-line argument, so it is visible in `ps` on the host while they run (mention a config-file alternative only if verified for the installed Swingbench version).
- Redact secrets everywhere (logs, events, errors, "equivalent command"). Run directories are created with mode 700 and files 600.
- The web server binds to `127.0.0.1` by default; any other bind requires authentication (login with a password hash configured at first start, session cookie with `HttpOnly` and `SameSite`, CSRF protection for state-changing requests) and TLS via a reverse proxy or SSH tunnel (document both).
- Audit log of who started/cancelled/dropped what, with parameters (redacted).
- Run the app as the `oracle` OS user only for the reasons in section 2; do not require root.

## 11. Gotchas learned while writing the scripts

1. A failed `CONNECT` in SQL*Plus does not reliably return a non-zero exit code. Judge success by an explicit token in the output.
2. `GRANT DBA TO soe IDENTIFIED BY "..."` creates the user if it does not exist and resets the password if it does; the password must not contain `"` (reject it).
3. Never rely on `IN (...)` lists with hundreds of names in SQL*Plus input (line-length limit); query by prefix and filter in code.
4. Background jobs started from a non-interactive shell ignore SIGINT; a naive "Ctrl-C" leaves `sqlplus`, `oewizard`, `charbench` and Java processes running. Use process groups/sessions and explicit group kill.
5. A hung `sqlplus` connect (listener or network issue) can block forever; always use a hard timeout with a follow-up KILL.
6. PDB names are upper-case in the data dictionary; service names are lower-case in the connect string; verify the container the session landed in (`SYS_CONTEXT('USERENV','CON_NAME')`).
7. After dropping and recreating a PDB the old "loaded" marker is wrong; key it by `con_uid`.
8. RAC vs single instance: `OPEN INSTANCES=ALL` / `CLOSE IMMEDIATE INSTANCES=ALL` and `gv$pdbs` for RAC; `OPEN` + `SAVE STATE` for single instance.
9. TDE: `ADMINISTER KEY MANAGEMENT SET KEY ... CONTAINER = CURRENT` fails when no wallet is configured, hence the explicit toggle. A nice-to-have is reading the wallet status from the CDB to suggest a default for the toggle (verify the exact view and columns against the target database before relying on it).
10. Swingbench result XML (`-r`) format has not been verified here. Treat result files as opaque downloads at first; if you later parse them (TPS, failed transactions), inspect real files from the user's environment and parse defensively. Likewise the exact column layout of the charbench console table is not guaranteed; do not build brittle parsers on it in v1.
11. `-rt` takes a run time in `HH:MM` form (the script's default is `00:30`).
12. 250 simultaneous charbench JVMs plus 250 × users sessions is heavy on the client host and the database. Offer the start delay and show the warning from section 7.

## 12. Milestones and acceptance criteria

- **M0 – Scaffold and simulation:** repo layout, config/profiles, ProcessRunner, stubs, validators with tests, env-check page. *Accept:* `ORCH_MODE=simulate` starts, env check shows simulated results, unit tests pass.
- **M1 – Inventory and connection check:** PDB list from `gv$pdbs`, parallel check with timeouts and reasons, check-only run in the UI. *Accept:* scenarios for OK / ORA-12514 / hang / wrong container / no schema all show correct reasons; 250 PDBs checked at parallelism 16 with a live grid.
- **M2 – Create / drop PDBs:** wallet toggle, existing-PDB decision (drop / skip / abort with confirmation), sequential creation with logs. *Accept:* generated SQL matches section 4.2–4.3 exactly (golden tests); wallet off produces no TDE statements.
- **M3 – Load SOE:** pre-flight, sliding window, resume by `con_uid`, force, keep-going, retry failed, dry-run. *Accept:* concurrency never exceeds the limit; a failure stops new starts unless keep-going; re-run loads only the rest.
- **M4 – Workload:** check → start → live monitoring → summary with error scan; stop-all; start delay. *Accept:* all workloads start before any is awaited; completions appear as they happen; cancel leaves no orphan processes; the run is blocked or filtered by the check policy.
- **M5 – History and artifacts:** run list/detail, downloads, CSV, compare, restart-safe state. *Accept:* restarting the backend mid-run marks it interrupted and keeps all logs.
- **M6 – Hardening:** auth, audit log, secret-leak tests, packaging (single `systemd` unit or script), README with install and operations guide.

## 13. Non-goals for v1

Multiple CDBs or hosts, distributed workers, Swingbench result analytics beyond download/compare, schedulers/cron, schemas other than SOE (design the operation classes so SH/TPC-DS/CMA can be added later), editing Swingbench config XML in the UI.

## 14. Decisions already made (do not ask; propose changes in the plan if you disagree)

- Backend runs on the DB/admin host as `oracle`, OS-authenticated `sqlplus`, Linux only.
- Python/FastAPI + SQLite + SPA frontend, single process, simulation mode first.
- One active run at a time; secrets in memory only; bind to localhost by default.
- Behavior parity with the three reference scripts, plus the new features in section 3.

## 15. Open questions (ask at most these, once, then proceed with the stated default)

1. Preferred frontend stack? *Default: React + Vite + TypeScript.*
2. Should the UI be reachable from other machines? *Default: localhost only, SSH tunnel documented.*
3. Is a second Swingbench schema (SH, TPC-DS) needed soon? *Default: no; keep the design extensible.*

## 16. Kickoff prompt (paste this if you saved the document as a file)

> Read `docs/PROJECT_CONTEXT.md` completely, then read the three scripts in `reference/`. Do not write application code yet. First write `docs/ARCHITECTURE.md` (at most two pages): the final stack, module layout, data model (runs, items, profiles, loaded markers), the process-management design (process groups, timeouts, cancel), the event/SSE design, and a task list for milestones M0–M6. Ask only the questions in section 15 that you cannot answer from the defaults, then stop and wait for my approval. After approval implement M0 (including simulation mode and tests) and show me the app running in simulation mode before starting M1. Commit after each milestone with a clear message.
