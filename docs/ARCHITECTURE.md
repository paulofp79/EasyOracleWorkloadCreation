# Architecture: PDB Swingbench Orchestrator

Status: **proposal, awaiting approval**. Source of requirements: `docs/PROJECT_CONTEXT.md` (PC) and `reference/*.sh`.

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python ≥ 3.11, FastAPI, Uvicorn (1 process), Pydantic v2 | as decided (PC §14) |
| Storage | SQLite via stdlib `sqlite3` (WAL mode), hand-written migrations | no ORM: fewer deps to install on a DB node |
| Processes | `asyncio.create_subprocess_exec`, `start_new_session=True`, `shell=False` | process groups, no shell |
| Live updates | SSE (`text/event-stream`) | one-way, survives proxies, easy reconnect |
| Frontend | React 18 + Vite + TypeScript, TanStack Query, TanStack Virtual, plain CSS (light/dark tokens) | default of PC §15.1 |
| Tests | pytest + pytest-asyncio + httpx; Vitest; Playwright smoke | PC §9 |

Deployment: the frontend is built to `frontend/dist` and served by FastAPI as static files, so the Oracle host needs **no Node.js**, only Python 3.11 (on OL8 the `python3.11` AppStream package; otherwise a self-contained venv/`uv` build) and the pip deps (vendored wheels option for hosts without internet).

## 2. Module layout

```
backend/app/
  main.py            app factory, static files, startup recovery (interrupted runs)
  config.py          ORCH_MODE (real|simulate), ORCH_DB, bind host/port, stub dir
  validators.py      every PC §5 rule; single source, mirrored in frontend/src/validators.ts (shared JSON test vectors)
  naming.py          pdb_name(), service(), connect_string(), pdb_range()
  sqlbuild.py        SQL text builders (create, drop, inventory, grant, check, env) – golden-tested
  cmdbuild.py        argv builders (oewizard, charbench) + masked "equivalent command"
  redact.py          Redactor: replaces known secret values in any str/bytes/dict
  logscan.py         error regex scanner (PC §4.7)
  secrets.py         in-memory SecretStore (per run, wiped on run end; optional TTL session cache)
  db/                schema.sql, migrations, repo functions (runs, items, profiles, markers, audit)
  engine/
    runner.py        ProcessRunner (spawn, stdin, stream→redact→file, timeout, kill group/tree)
    runmanager.py    single-active-run lock, task lifecycle, cancel, restart recovery
    events.py        EventBus: per-run seq numbers, ring buffer, coalescing, SSE fan-out
    operation.py     Operation base: validate() → plan() → run(ctx) ; cancel via ctx
  ops/  envcheck.py inventory.py check_conn.py create_pdbs.py drop_pdbs.py load_soe.py workload.py
  api/  profiles.py env.py pdbs.py runs.py events.py auth.py (M6)
backend/tests/{unit,engine,api}/
tools/stubs/{sqlplus,oewizard,charbench}   Python scripts, scenario file tools/stubs/scenario.json
frontend/src/{api,pages,components,state,validators.ts}
reference/  docs/
```

New schema types later (SH, TPC-DS): add `ops/load_<schema>.py` + a `schema` field on markers; nothing else is SOE-specific outside `load_soe.py`, `check_conn.py` (table list) and `workload.py` (config default).

## 3. Data model (SQLite)

- **profiles**(id, name UNIQUE, is_active, params_json, created_at, updated_at) – non-secret defaults of PC §5 only.
- **runs**(id TEXT ULID, type `env|check|create|drop|load|workload`, status `running|ok|failed|cancelled|interrupted|blocked`, phase `check|execute|summary`, profile_id, params_json (validated, contains no secrets), run_dir, retry_of, created_at, started_at, ended_at, summary_json, message).
- **run_items**(run_id, pdb, ord, state `pending|checking|running|ok|failed|skipped|cancelled|not_started`, con_uid, check_status, check_detail, instance, pid, pgid, proc_starttime, exit_code, started_at, ended_at, detail, error_count, first_error) PK(run_id, pdb). Workload rows carry both the check result and the charbench result.
- **soe_markers**(pdb PK, con_uid, loaded_at, run_id, scale, tablespace) – replaces the script's `<PDB>.done`; skip iff `con_uid` equals live `con_uid` and not force. One-time importer for existing `.done` files is offered in M3.
- **last_checks**(pdb PK, status, detail, instance, checked_at, run_id) – for the inventory page.
- **audit_log**(id, ts, actor, action `start|cancel|drop_confirmed|login|profile_change`, run_id, params_json redacted).
- **schema_version**.

Large text (logs, SQL*Plus output, results XML) stays on disk in the run dir: `<log_dir>/run-YYYYmmdd-HHMMSS-<id>/` (mode 700, files 600), `latest` symlink, per-PDB subdirs for create/load/check logs, and the same TSVs the script writes (`connections.tsv`, `processes.tsv`, `status.tsv`, `summary.tsv`).

## 4. Process management

**ProcessRunner.spawn(argv, stdin_text, log_path, cwd, timeout, env)**
- Every child is started with `start_new_session=True` → it is the leader of its own session and process group (`pgid == pid`). This applies to sqlplus, oewizard and charbench alike, so a single `os.killpg` reaches all Java descendants; the bash `setsid`/`pgrep -P` split is no longer needed. A `/proc` tree walk is kept as fallback for descendants that call `setsid` themselves.
- stdin: secret-bearing SQL is written to the pipe and closed; never put on argv, never written to disk (the script's drop SQL file is replaced by stdin).
- stdout+stderr merged into a pipe, read line by line, passed through the run's `Redactor`, written to the log file, and (rate-limited) offered to subscribers of that item's log stream. Redacting in-process is what lets the leak test grep the run dir for the password.
- Timeout (connection check): `asyncio.wait_for`; on expiry TERM the group, KILL after 5 s (same as `timeout -k 5`), result "timed out after N s". Done in-engine instead of wrapping `/usr/bin/timeout`, which would add an extra process layer between us and sqlplus.
- `pid`, `pgid` and `/proc/<pid>/stat` start time are persisted on the item when the process starts.

**Cancel** (`POST /runs/{id}/cancel`): set the run's cancel flag (no new starts) → TERM every live group → 2 s grace → KILL survivors → await reaping → items: running→`cancelled`, pending→`not_started`, run→`cancelled`. Verified in tests by asserting `os.killpg(pgid, 0)` raises `ProcessLookupError` for every recorded pgid, including the stub charbench's child.

**Concurrency**: `asyncio.Semaphore` for check parallelism and load parallelism (sliding window = acquire before start, release on exit). Workload launches all processes in a loop (with `start_delay` between launches, none after the last) before awaiting any; completions are consumed via `asyncio.wait(FIRST_COMPLETED)` so they report in completion order. Create and drop are strictly sequential.

**Restart recovery**: on startup every run in `running` becomes `interrupted`. For each item with a pgid, liveness is checked (`killpg(pgid,0)` + start time match against `/proc`, which guards against PID reuse); the run detail shows "N processes from this run are still alive" with a **Kill leftovers** button. Logs are kept untouched.

**One active run**: `RunManager` holds a single lock; a second `POST /runs` gets `409` with the active run id. Env-check and inventory reads are not runs and are always allowed.

## 5. Events and SSE

- Each run has an `EventBus` with a monotonically increasing `seq`. Event types: `run.updated`, `items.patch` (list of changed item fields), `heartbeat` (running count, elapsed; every 5 s), `log.line` (only on per-item streams), `resync`.
- Item changes are **coalesced every 250 ms** into one `items.patch`, so 250 near-simultaneous completions produce a handful of messages; state changes are written to SQLite in the same flush (one transaction).
- `GET /api/runs/{id}/events` sends `id: <seq>`; the browser's `Last-Event-ID` on reconnect replays from a 2,000-event ring buffer; if the gap is older, the server sends `resync` and the client refetches `GET /runs/{id}` + `/items` (REST is always the source of truth).
- Per-item live log: `GET /api/runs/{id}/items/{pdb}/log/stream` (SSE, tails the file, opened only while the drawer is open); `…/log?tail=N` for snapshots.
- Frontend keeps items in a `Map<pdb, Item>` store, applies patches, and renders tiles as memoized components keyed by pdb; the table view is virtualized.

## 6. Parity notes (script behavior kept, or deliberately changed)

1. Create: existing-PDB check uses the prefix query (PC §4.4), not the script's `v$pdbs … IN (…)` list (gotcha 3).
2. Create SQL text: generated exactly as the script lays it out (multi-line `CREATE …`/`ADMINISTER …`); golden tests pin it. Semantically identical to PC §4.2.
3. Drop: same PL/SQL block as the script (it uses `gv$pdbs` for both RAC and non-RAC), but one sqlplus per PDB instead of one batch file, so progress is per PDB and a failure names the PDB. Still stops on first failure.
4. Load cancel adds the KILL-after-2 s step (script only sent TERM), per PC §4.8.
5. oewizard path must be absolute in profiles (script default `./oewizard` is relative to the caller's cwd); relative values are resolved against the Swingbench `bin` dir and shown resolved.
6. Truncation as in scripts: check reason 200 chars, first error line 150 chars; timeout detection also maps exit codes 124/137 for parity.
7. Prefix is upper-cased before validation (load/workload scripts do this).

## 7. Simulation mode

`ORCH_MODE=simulate` puts `tools/stubs` first in `PATH` and sets default tool paths to the stubs. Stubs read `tools/stubs/scenario.json` (or `ORCH_SIM_SCENARIO`) holding: CDB info (name, version, instances), PDB list with con_uid/open modes (mutated by create/drop so the inventory reflects them), per-PDB scenario (`ok|ora12514|hang|wrong_container|no_schema` for check; `fail`/`delay` for oewizard; `errors|exit1|long` for charbench) and a global time scale so a "00:30" run takes seconds. The charbench stub forks a child `sleep` to prove group kill.

## 8. Milestone task list

**M0 Scaffold & simulation** – repo layout, pyproject, config, SQLite schema+migrations, profiles CRUD API, validators (+ shared test vectors), naming, redact, ProcessRunner (+ tests: stdin, timeout, group kill, redaction), the three stubs + scenario file, env-check op/API, React shell (nav, Profiles, Env check, Dashboard placeholder), `make dev`/`make test`. *Demo: app in simulate mode, env check green/red per item.*

**M1 Inventory & check** – inventory op (prefix query + parser), PDBs page (virtualized table, filters, selection), EventBus + SSE + RunManager, check op (semaphore, timeout, token parser, reasons), `connections.tsv`, Run detail with tile grid/table/drawer/counters, last_checks. Tests for all five scenarios, 250 PDBs at parallelism 16.

**M2 Create/drop** – sqlbuild golden tests (RAC/SI × wallet on/off), plan returns existing PDBs, decision (drop/skip/abort) with `DELETE` confirmation enforced server-side (confirm token = hash of the exact PDB list), sequential create, standalone drop from PDBs page, audit entries.

**M3 Load SOE** – pre-flight (missing/not RW), markers by con_uid, force, sliding window, keep-going, summary counts, retry failed (`retry_of`), dry run with masked command, `.done` importer. Concurrency-limit and failure-policy tests.

**M4 Workload** – check phase + policy (abort/skip-unreachable/block), launch-all with start delay, completion-order reporting, heartbeat, log error scan, TSVs, Stop all, heavy-load warning, ps-visibility notice.

**M5 History & artifacts** – Runs list/filter, artifacts.zip (streamed), summary.csv, compare two workload runs, restart recovery + Kill leftovers, retention info (disk usage per run).

**M6 Hardening** – non-localhost bind requires auth (argon2/scrypt hash set at first start, HttpOnly SameSite=Strict cookie, CSRF token), audit page, secret-leak test suite (responses, DB, run dir, logs), Playwright smoke at 250 PDBs, systemd unit + install script, README (install, SSH tunnel, nginx TLS example, operations).
