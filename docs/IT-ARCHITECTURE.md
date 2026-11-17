# IT architecture: PDB Swingbench Orchestrator

This document describes the application as built (v1, milestones M0–M6) from an IT-architecture point
of view: context, deployment, network and security, software components, data, runtime behaviour and
operations. Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub. Design rationale and
parity notes with the original scripts are in [`ARCHITECTURE.md`](ARCHITECTURE.md); installation and
operation in [`INSTALL.md`](INSTALL.md) and [`OPERATIONS.md`](OPERATIONS.md).

**Contents**

1. [System context](#1-system-context)
2. [Deployment topologies](#2-deployment-topologies)
3. [Network, ports and trust zones](#3-network-ports-and-trust-zones)
4. [Logical components](#4-logical-components)
5. [Technology stack](#5-technology-stack)
6. [API surface](#6-api-surface)
7. [Data architecture](#7-data-architecture)
8. [Runtime: key scenarios](#8-runtime-key-scenarios)
9. [Process and concurrency model](#9-process-and-concurrency-model)
10. [Security architecture](#10-security-architecture)
11. [Resilience and recovery](#11-resilience-and-recovery)
12. [Build, packaging and deployment pipeline](#12-build-packaging-and-deployment-pipeline)
13. [Quality and test architecture](#13-quality-and-test-architecture)
14. [Constraints and known limits](#14-constraints-and-known-limits)

---

## 1. System context

Who uses the system and what it talks to.

```mermaid
flowchart LR
    DBA(["DBA / performance engineer<br/>web browser"])
    subgraph APPHOST["Application host - Oracle Linux 8/9, OS user oracle"]
        ORCH["PDB Swingbench Orchestrator<br/>web UI + REST/SSE API<br/>systemd service"]
        TOOLS["Oracle tools<br/>sqlplus, Swingbench oewizard and charbench (Java)"]
        FS[("Local storage<br/>SQLite database, run directories")]
    end
    subgraph DB["Oracle Exadata / RAC"]
        SCAN["SCAN listeners :1521"]
        CDB[("Container database<br/>CDB$ROOT + PDBPP_001..250")]
    end
    ADMIN(["Server administrator<br/>SSH, systemctl, journalctl"])

    DBA -- "HTTPS :8443 (login)<br/>or HTTP via SSH tunnel :8080" --> ORCH
    ORCH -- "starts processes,<br/>secrets on stdin" --> TOOLS
    ORCH --- FS
    TOOLS -- "SQL*Net / JDBC" --> SCAN
    SCAN --> CDB
    TOOLS -. "local layout only:<br/>/ as sysdba (bequeath)" .-> CDB
    ADMIN -- "SSH :22" --> APPHOST
```

| Actor / system | Role |
|---|---|
| DBA | Plans and runs PDB creation, SOE loads, connection checks and workloads; monitors live; downloads results |
| Orchestrator | Validates input, generates the same SQL and command lines as the reference scripts, runs and supervises the tools, stores history |
| sqlplus | Privileged SQL (inventory, create/drop, GRANT) and SOE connection checks |
| oewizard / charbench | Swingbench schema load and workload, one Java process per PDB |
| Oracle RAC CDB | Target of all operations, reached through the SCAN listeners |
| Administrator | Installs the service, manages logins, backups and upgrades |

---

## 2. Deployment topologies

The profile setting **CDB connection** selects one of two layouts. Both run the same software.

### 2.1 Local layout: application on a database node

```mermaid
flowchart TB
    B(["Browser"]) -- "SSH tunnel or HTTPS" --> APP
    subgraph N1["RAC node 1 - also the application host"]
        APP["Orchestrator<br/>systemd: pdb-orchestrator"]
        SP["sqlplus / as sysdba<br/>OS authentication, ORACLE_SID"]
        OEW["oewizard / charbench JVMs"]
        INST1[("Instance 1")]
        APP --> SP
        APP --> OEW
        SP -- "bequeath" --> INST1
    end
    subgraph N2["RAC node 2..n"]
        INST2[("Instance 2..n")]
    end
    SCAN["SCAN listeners"]
    OEW -- "//scan:1521/pdb service" --> SCAN
    SCAN --> INST1
    SCAN --> INST2
```

Needs on the node: the CDB's Oracle home, `ORACLE_SID`, Swingbench and Java. Downside: up to 250
charbench JVMs compete with the database being measured.

### 2.2 Remote layout: separate application server (recommended for large runs)

```mermaid
flowchart TB
    B(["Browser"]) -- "HTTPS :8443 + login<br/>or SSH tunnel" --> APP
    subgraph AS["Application server"]
        APP["Orchestrator<br/>systemd: pdb-orchestrator"]
        IC["Oracle Instant Client<br/>sqlplus /nolog + CONNECT sys AS SYSDBA"]
        OEW["oewizard / charbench JVMs"]
        APP --> IC
        APP --> OEW
    end
    subgraph RAC["Exadata RAC cluster"]
        SCAN["SCAN listeners :1521"]
        I1[("Instance 1")]
        I2[("Instance 2..n")]
        PWF["Password file<br/>remote SYSDBA allowed"]
    end
    IC -- "//scan:1521/CDB root service" --> SCAN
    OEW -- "//scan:1521/pdb service" --> SCAN
    SCAN --> I1
    SCAN --> I2
    I1 -.- PWF
```

Needs: Instant Client with SQL*Plus, Swingbench and Java on the app server, TCP 1521 to the SCAN,
and the CDB root service name. The SYS password is entered once per browser session.

### 2.3 Inside the application host

```mermaid
flowchart TB
    subgraph OS["Oracle Linux - application host"]
        SYSD["systemd<br/>pdb-orchestrator.service<br/>User=oracle, KillMode=mixed,<br/>Restart=on-failure"]
        ENV["/etc/pdb-orchestrator/orchestrator.env<br/>ORCH_HOST, ORCH_PORT, TLS,<br/>PATH, ORACLE_HOME, ORACLE_SID"]
        subgraph APPDIR["~oracle/pdb-orchestrator"]
            VENV["venv/ - Python 3.11<br/>FastAPI, Uvicorn, Pydantic"]
            BE["backend/app - the application"]
            UI["frontend/dist - built web UI"]
        end
        subgraph DATA["~oracle/.pdb-orchestrator"]
            SQLITE[("orchestrator.db<br/>SQLite, WAL")]
            TLS["tls/ cert.pem, key.pem"]
        end
        RUNS[("&lt;log dir&gt;/run-&lt;id&gt;/<br/>logs, TSV, results XML")]
        JOURNAL["journald<br/>application log"]
    end
    SYSD -- "reads" --> ENV
    SYSD -- "starts venv/bin/python -m app" --> BE
    BE --- VENV
    BE -- "serves" --> UI
    BE --- SQLITE
    BE --- TLS
    BE --- RUNS
    BE -- "stdout/stderr" --> JOURNAL
```

---

## 3. Network, ports and trust zones

```mermaid
flowchart LR
    subgraph Z1["Zone: user workstations"]
        BR(["Browser"])
        SSHC(["SSH client"])
    end
    subgraph Z2["Zone: application host"]
        FW{{"Host firewall<br/>firewalld"}}
        APP["Orchestrator<br/>127.0.0.1:8080 default<br/>0.0.0.0:8443 direct access"]
        SSHD["sshd :22"]
    end
    subgraph Z3["Zone: database network"]
        SCAN["SCAN :1521"]
    end
    BR -- "1: HTTPS :8443<br/>TLS + login + CSRF" --> FW --> APP
    SSHC -- "2: SSH :22, tunnel to 127.0.0.1:8080" --> SSHD --> APP
    APP -- "SQL*Net :1521<br/>sqlplus, oewizard, charbench" --> SCAN
```

| Flow | Port | Protocol | Protection | When |
|---|---|---|---|---|
| Browser to app (direct) | 8443 (any `ORCH_PORT`) | HTTPS | TLS (own or self-signed certificate), login, CSRF token, Secure cookies | `ORCH_HOST=0.0.0.0` |
| Browser to app (tunnel) | 22, then 127.0.0.1:8080 | SSH + HTTP | SSH encryption and authentication; app only on loopback | default |
| Browser to app (proxy) | proxy port | HTTPS to proxy, HTTP to app | TLS at the proxy, login and CSRF at the app | `ORCH_TLS=off` |
| App to database | 1521 | Oracle Net | database authentication (SYS, SOE) | always (remote layout also for SYSDBA) |
| Live updates | same as browser | Server-Sent Events | same session cookie | run pages |

Binding rules enforced at start: a non-loopback address requires at least one login and switches
HTTPS on (unless `ORCH_TLS=off` for a TLS proxy); without a login the service refuses to start.

---

## 4. Logical components

### 4.1 Component view

```mermaid
flowchart TB
    subgraph FE["Web UI - React SPA, served as static files"]
        PAGES["Pages: Dashboard, PDBs, Create, Drop, Load SOE,<br/>Check, Workload, Runs, Compare, Run detail,<br/>Profiles, Environment, Audit, Login"]
        STREAM["useRunStream<br/>REST snapshot + EventSource"]
        VALTS["validators.ts"]
    end
    subgraph BE["Backend - FastAPI on Uvicorn, one process"]
        MW["Auth middleware<br/>session + CSRF check"]
        API["REST routers<br/>profiles, env, pdbs, runs,<br/>artifacts, session, auth, audit"]
        SSE["SSE endpoints<br/>run events, live log tail"]
        VAL["validators.py<br/>shared rules"]
        subgraph ENG["Run engine"]
            RM["RunManager<br/>one active run, recovery"]
            CTX["RunContext<br/>item state, 250 ms flush"]
            BUS["EventBus<br/>seq numbers, replay buffer"]
            PR["ProcessRunner<br/>process groups, stdin, redaction,<br/>timeouts, kill"]
            LO["Leftovers<br/>/proc liveness, kill groups"]
        end
        subgraph OPS["Operations"]
            OCHK["CheckConnections"]
            OCRE["CreatePdbs / DropPdbs"]
            OLOAD["LoadSoe"]
            OWL["Workload"]
            OENV["Env check, Inventory"]
        end
        BLD["Builders<br/>sqlbuild, cmdbuild, naming"]
        SYS["SysdbaConn<br/>local or remote logon"]
        SEC["SessionStore (SYS password)<br/>AuthStore (logins)<br/>Redactor"]
        DBL["Database layer<br/>sqlite3"]
    end
    EXT["sqlplus / oewizard / charbench<br/>child processes"]
    STORE[("SQLite + run directories")]

    PAGES --> MW --> API
    STREAM --> SSE
    API --> VAL
    API --> RM
    API --> OPS
    RM --> CTX --> BUS --> SSE
    OPS --> BLD
    OPS --> SYS
    OPS --> PR
    CTX --> PR
    PR --> EXT
    RM --> LO
    API --> SEC
    OPS --> DBL
    CTX --> DBL
    DBL --> STORE
    PR --> STORE
```

### 4.2 Responsibilities

| Component | Responsibility |
|---|---|
| Web UI | Forms with plan/dry run, inline validation, equivalent-command preview, live tile grid, drawers with log tails, history, compare, audit |
| Auth middleware | With login on: rejects API calls without a session (401) and state changes without the CSRF token (403) |
| Validators | One rule set for every field that reaches SQL or a command line; mirrored in the browser and tested with shared vectors |
| Operations | One class per run type: validate, `plan()` (dry run), `execute()`; same behaviour as the reference scripts |
| Builders | Generate SQL text and argument lists; golden tests compare them with the scripts' heredocs |
| SysdbaConn | Privileged logon: `/ as sysdba` or `CONNECT sys/"…"@//scan/service AS SYSDBA` plus a session marker check |
| RunManager / RunContext | One active run, item state machine, batched persistence and events, cancel, startup recovery |
| EventBus | Ordered events with sequence numbers, replay on reconnect, resync when too old |
| ProcessRunner | Starts every tool in its own session/process group, feeds secrets on stdin, redacts output into 0600 logs, enforces timeouts, kills groups |
| Leftovers | After a crash: finds recorded processes still alive (pid + start time or process group), kills them on request |
| SessionStore / AuthStore | In-memory SYS password per browser (1–24 h) and login sessions (8 h idle, 24 h max) |

---

## 5. Technology stack

```mermaid
flowchart LR
    subgraph Client
        R["React 19 + TypeScript"]
        TQ["TanStack Query,<br/>TanStack Virtual"]
        V["Vite build"]
    end
    subgraph Server
        PY["Python 3.11"]
        FA["FastAPI + Pydantic v2"]
        UV["Uvicorn (TLS)"]
        SQ["sqlite3 (stdlib), WAL"]
        AIO["asyncio subprocesses"]
    end
    subgraph Platform
        OL["Oracle Linux 8/9"]
        SD["systemd + journald"]
        OSSL["OpenSSL (self-signed cert)"]
        ORA["SQL*Plus / Instant Client"]
        SB["Swingbench + Java"]
    end
    Client -- "HTTPS, JSON, SSE" --> Server
    Server --> Platform
```

| Layer | Choice | Why |
|---|---|---|
| UI | React, TypeScript, TanStack Query/Virtual, plain CSS | Dense, keyboard-accessible UI that stays smooth with 250 live items |
| API | FastAPI, Pydantic, Uvicorn | Async I/O for hundreds of child processes and SSE streams in one process |
| Storage | SQLite (WAL) via stdlib | No extra server; single file to back up |
| Process control | asyncio, POSIX sessions and process groups | Reliable stop of sqlplus, oewizard, charbench and their Java children |
| Packaging | venv, offline wheel bundle, committed UI build | Installs on hosts without internet, npm or Node.js |
| Testing | pytest, vitest, Playwright, simulation stubs | Full behaviour testable without an Oracle database |

---

## 6. API surface

All endpoints are under `/api`. With login on, everything except `health`, `auth/state` and
`auth/login` needs a session; POST/PUT/DELETE also need the `X-CSRF-Token` header.

```mermaid
flowchart LR
    API(["/api"])
    API --> H["health"]
    API --> AU["auth: state, login, logout"]
    API --> SE["session: get, sys-password set / forget"]
    API --> PR["profiles: list, fields, active,<br/>create, update, activate, delete"]
    API --> EN["env-check"]
    API --> PD["pdbs: inventory, import-markers"]
    API --> RU["runs: list, active, compare, plan or start,<br/>detail, items, events (SSE), cancel,<br/>item log, log stream (SSE), run log,<br/>leftovers, kill-leftovers"]
    API --> AR["artifacts: list, files, artifacts.zip, summary.csv"]
    API --> AD["audit"]
```

A run is started with `POST /api/runs {type, params, secrets, dry_run, retry_of}`; `type` is one of
`check`, `create`, `drop`, `load`, `workload`. With `dry_run` the same call returns the plan.

---

## 7. Data architecture

### 7.1 Data model (SQLite)

```mermaid
erDiagram
    PROFILES ||--o{ RUNS : "defaults for"
    RUNS ||--|{ RUN_ITEMS : "one per PDB"
    RUNS ||--o{ RUNS : "retry_of"
    RUNS ||--o{ AUDIT_LOG : "referenced by"
    RUNS ||--o{ SOE_MARKERS : "loaded by"
    RUNS ||--o{ LAST_CHECKS : "checked by"
    USERS ||--o{ AUDIT_LOG : "actor"

    PROFILES {
        int id PK
        text name
        int is_active
        text params_json "non-secret defaults"
    }
    RUNS {
        text id PK "yyyymmdd-hhmmss-hex"
        text type "check create drop load workload"
        text status
        text phase
        text params_json "validated, no secrets"
        text run_dir
        text retry_of FK
        text summary_json
        text message
    }
    RUN_ITEMS {
        text run_id PK
        text pdb PK
        text state
        text con_uid
        text check_status
        int pid
        int pgid
        int proc_starttime
        int exit_code
        int error_count
        text first_error
    }
    SOE_MARKERS {
        text pdb PK
        text con_uid "loaded incarnation"
        text loaded_at
        text run_id
    }
    LAST_CHECKS {
        text pdb PK
        text status
        text detail
        text checked_at
    }
    AUDIT_LOG {
        int id PK
        text ts
        text actor
        text action
        text run_id
        text params_json "redacted"
    }
    USERS {
        text username PK
        text password_hash "scrypt"
    }
```

### 7.2 Data classification and location

| Data | Location | Persistence | Sensitivity |
|---|---|---|---|
| Profiles, run history, items, markers, last checks, audit | `~/.pdb-orchestrator/orchestrator.db` | durable, back up | internal |
| Login password hashes | same database, `users` | durable | confidential (scrypt hashes only) |
| Run artifacts | `<log dir>/run-<id>/` (dir 0700, files 0600) | durable until removed | internal |
| TLS key | `~/.pdb-orchestrator/tls/key.pem` (0600) | durable | secret |
| SOE, SYS, PDB admin, wallet passwords | process memory only | lifetime of the run / browser session | secret, never written |
| Login sessions, CSRF tokens | process memory only | until expiry or restart | secret |

### 7.3 Run directory layout

```mermaid
flowchart LR
    LD["&lt;log dir&gt;"] --> LATEST["latest  (symlink to newest run)"]
    LD --> RD["run-&lt;id&gt;/"]
    RD --> RL["run.log"]
    RD --> TSV["connections.tsv, processes.tsv,<br/>status.tsv, summary.tsv"]
    RD --> CC["connection-check/&lt;PDB&gt;.log"]
    RD --> CB["&lt;PDB&gt;-charbench.log<br/>&lt;PDB&gt;-results.xml"]
    RD --> LS["&lt;PDB&gt;-soe-setup.log<br/>&lt;PDB&gt;-oewizard.log"]
    RD --> CD["&lt;PDB&gt;-create.log<br/>&lt;PDB&gt;-drop.log"]
```

### 7.4 Information flow of one run

```mermaid
flowchart LR
    F["Form + secrets"] --> V["Validation"] --> P["Plan / pre-flight<br/>gv$pdbs"]
    P --> R["Run row + items<br/>SQLite"]
    R --> X["Child processes"]
    X -- "stdout, redacted" --> L["Log files"]
    X -- "exit codes, tokens" --> S["Item state<br/>250 ms batches"]
    S --> DB[("SQLite")]
    S --> E["SSE events"] --> UI["Live UI"]
    L --> SC["Error scan, TSVs"] --> SUM["Run summary"]
    SUM --> DB
```

---

## 8. Runtime: key scenarios

### 8.1 Workload run: check, launch, monitor, summarise

```mermaid
sequenceDiagram
    autonumber
    actor U as DBA
    participant UI as Web UI
    participant API as API
    participant RM as RunManager
    participant OP as Workload op
    participant PR as ProcessRunner
    participant SQ as sqlplus
    participant CB as charbench (x N)
    participant DB as Oracle (SCAN)

    U->>UI: Plan / dry run
    UI->>API: POST /runs dry_run
    API->>OP: plan(): pre-flight, load estimate
    OP-->>UI: plan, heavy-load warning, masked command
    U->>UI: Check and start workload
    UI->>API: POST /runs with SOE password
    API->>RM: start(op)
    RM-->>UI: run_id
    UI->>API: GET run + items, then SSE events
    loop every PDB, at most check_parallel at a time
        OP->>PR: sqlplus /nolog, CONNECT soe on stdin
        PR->>SQ: start in own process group
        SQ->>DB: logon, container and table check
        SQ-->>OP: SOE_CHECK_OK token or error
    end
    alt failures and skip unreachable is off
        OP-->>RM: blocked, no workload started
    else allowed
        loop every PDB that passed
            OP->>PR: charbench argv, own process group
            PR->>CB: start (optional start delay)
        end
        par all workloads run at once
            CB->>DB: Swingbench transactions
            CB-->>PR: console lines, redacted into logs
            PR-->>RM: item running, error lines flagged live
        end
        CB-->>OP: each exit reported as it happens
        OP->>OP: scan logs, write status.tsv and summary.tsv
    end
    RM-->>UI: run.updated, end of stream
```

### 8.2 Create PDBs with drop-and-recreate (guarded)

```mermaid
sequenceDiagram
    autonumber
    actor U as DBA
    participant API as API
    participant OP as CreatePdbs
    participant SQ as sqlplus SYSDBA
    participant AUD as Audit log

    U->>API: plan (range, TDE choice)
    API->>OP: pre-flight by prefix
    OP->>SQ: gv$pdbs query
    SQ-->>OP: existing PDBs with con_uid
    OP-->>U: existing list + confirm token (hash of name:con_uid)
    U->>API: start: drop, confirm_text DELETE, token
    API->>OP: validate DELETE and token present
    OP->>SQ: pre-flight again
    alt token differs (PDB appeared, vanished or recreated)
        OP-->>U: blocked, nothing dropped
    else token matches
        OP->>AUD: drop_confirmed with exact list
        loop each existing PDB, sequential
            OP->>SQ: close where open, drop including datafiles
        end
        loop each PDB of the range, sequential
            OP->>SQ: create, open, register, optional TDE key
            SQ-->>OP: exit code, first ORA- line on failure
        end
        OP-->>U: ok, or stopped at the first failure
    end
```

### 8.3 Remote SYSDBA logon and the session marker

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant S as Session API
    participant ST as SessionStore (memory)
    participant SQ as sqlplus /nolog
    participant DB as CDB root service

    B->>S: PUT sys-password
    S->>SQ: stdin: CONNECT sys AS SYSDBA, marker query, env query
    SQ->>DB: logon through SCAN
    alt logon fails
        DB-->>SQ: ORA-01017 or ORA-12514
        SQ-->>S: no marker in output (exit code may be 0)
        S-->>B: 422 with the ORA- line, nothing kept
    else logon succeeds
        SQ-->>S: SYSDBA_SESSION_OK, CDB name, container CDB$ROOT
        S->>ST: keep password, expiry 1-24 h
        S-->>B: HttpOnly SameSite=Strict session cookie
    end
    Note over S,SQ: Every later privileged call checks the marker the same way:<br/>no marker means failed logon, never success or an empty result
```

### 8.4 Live updates: snapshot, stream, reconnect

```mermaid
sequenceDiagram
    autonumber
    participant UI as Run page
    participant API as REST
    participant BUS as EventBus

    UI->>API: GET run and items (includes event_seq N)
    UI->>BUS: SSE events, last_id = N
    BUS-->>UI: items.patch (batched every 250 ms), run.updated, heartbeat, log.run
    Note over UI,BUS: connection drops
    UI->>BUS: browser reconnects with Last-Event-ID = M
    alt M still in the 2000-event buffer
        BUS-->>UI: replay events after M, then live
    else too old
        BUS-->>UI: resync
        UI->>API: fetch snapshot again
    end
    BUS-->>UI: end (run finished)
    UI->>API: final snapshot
```

### 8.5 State machines

Run status:

```mermaid
stateDiagram-v2
    [*] --> running: POST /runs
    running --> ok: all items succeeded
    running --> failed: an item failed or internal error
    running --> blocked: pre-flight or connection-check policy
    running --> cancelled: Stop all
    running --> interrupted: backend stopped mid-run
    ok --> [*]
    failed --> [*]
    blocked --> [*]
    cancelled --> [*]
    interrupted --> [*]
```

Item (PDB) state:

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> checking: connection check
    checking --> pending: check OK, workload follows
    checking --> ok: check-only run
    checking --> failed: check failed
    checking --> skipped: check failed, skip unreachable
    pending --> running: process started
    pending --> skipped: exists already / loaded already / missing
    running --> ok: exit 0
    running --> failed: non-zero exit or error
    running --> cancelled: Stop all
    pending --> not_started: run stopped or blocked
    ok --> [*]
    failed --> [*]
    skipped --> [*]
    cancelled --> [*]
    not_started --> [*]
```

---

## 9. Process and concurrency model

```mermaid
flowchart TB
    subgraph SVC["systemd service cgroup"]
        UV["Uvicorn / FastAPI<br/>one Python process, asyncio event loop"]
        subgraph G1["Process group 1 (own session)"]
            S1["sqlplus"]
        end
        subgraph G2["Process group 2"]
            C2["charbench"] --> J2["Java children"]
        end
        subgraph G3["Process group N"]
            C3["charbench"] --> J3["Java children"]
        end
        UV -- "start_new_session" --> G1
        UV -- "start_new_session" --> G2
        UV -- "start_new_session" --> G3
    end
```

| Mechanism | Value | Purpose |
|---|---|---|
| Active runs | 1 per server | No conflicting operations (e.g. drop during workload); 409 for a second run |
| Connection checks in parallel | `check_parallel` (default 16) | Semaphore |
| SOE loads in parallel | `parallel` (default 1) | Sliding window; no new start after a failure unless keep-going |
| Workloads | all at once, optional start delay | All launched before any is awaited; completions reported in order of completion |
| Create / drop | strictly sequential | Same as the script; stop at first failure |
| Check timeout | `check_timeout`, then TERM, KILL after 5 s | Hung SQL*Net connects |
| Stop all / cancel | TERM to every group, KILL after 2 s | Reaches Java children too |
| State persistence | every 250 ms, one transaction | 250 completions cost a handful of writes and events |
| Heartbeat | 5 s (UI), 60 s (run log) | Liveness of long runs |

Stop sequence:

```mermaid
sequenceDiagram
    participant U as DBA
    participant RM as RunManager
    participant PR as ProcessRunner
    participant G as Process groups
    U->>RM: Stop all (confirmed)
    RM->>RM: cancelled flag, no new starts
    RM->>PR: kill_all()
    PR->>G: SIGTERM to each group and stragglers
    PR->>PR: wait up to 2 s
    PR->>G: SIGKILL to what is left
    PR-->>RM: all groups gone
    RM-->>U: items cancelled / not started, run cancelled
```

---

## 10. Security architecture

### 10.1 Controls

```mermaid
flowchart TB
    subgraph EDGE["Edge"]
        T1["Loopback bind by default"]
        T2["Non-loopback bind requires a login and HTTPS"]
        T3["Firewall: one port"]
    end
    subgraph IAM["Identity and session"]
        I1["Logins created on the server only (CLI)"]
        I2["scrypt password hashes"]
        I3["HttpOnly, SameSite=Strict, Secure cookies"]
        I4["CSRF token on every change"]
        I5["Lockout: 5 failures per address, 60 s"]
        I6["Idle 8 h, absolute 24 h"]
    end
    subgraph APPSEC["Application"]
        A1["Server-side validation of every value"]
        A2["Argument lists only, no shell"]
        A3["No arbitrary SQL endpoint"]
        A4["Drops need DELETE + token over name:con_uid"]
        A5["Audit log of every action"]
    end
    subgraph SECRETS["Secrets"]
        S1["Typed per run or per browser session"]
        S2["Memory only, never on disk or in the DB"]
        S3["sqlplus: stdin only"]
        S4["Masked in logs, events, errors, previews"]
    end
    subgraph HOST["Host"]
        H1["Runs as oracle, never root"]
        H2["Run dirs 0700, files 0600, TLS key 0600"]
        H3["systemd: NoNewPrivileges, ProtectSystem, PrivateTmp"]
    end
```

### 10.2 Where secrets travel

```mermaid
flowchart LR
    B["Browser password field"] -- "HTTPS or SSH tunnel" --> M["Server memory<br/>run secrets / SYS session"]
    M -- "stdin" --> SQ["sqlplus<br/>CONNECT ... or GRANT ... IDENTIFIED BY"]
    M -- "command-line argument" --> SW["oewizard -p / -dbap<br/>charbench -p"]
    SW -. "visible in ps on the app host<br/>while running" .-> PS["Process list"]
    SQ -- "output, redacted" --> LOG["Log files"]
    SW -- "output, redacted" --> LOG
    M -. "never" .-x DISK[("Database, files, audit")]
```

| Threat | Mitigation |
|---|---|
| Unauthenticated network access | Loopback by default; login + HTTPS required for network binds |
| Credential theft in transit | TLS or SSH tunnel; Secure cookies |
| CSRF from another site | SameSite=Strict cookies plus double-submit CSRF token |
| Password guessing | scrypt hashes, per-address lockout |
| Injection into SQL or commands | Strict validators, argument lists, no shell, no free SQL |
| Accidental destruction | Plan first, typed DELETE, token bound to the exact `name:con_uid` list, audit entry |
| Secret leakage | Memory-only secrets, stdin for sqlplus, redaction; tested by a suite that searches every file and response |
| Known residual risk | oewizard/charbench take passwords as arguments (visible in `ps` to users of the host) |

---

## 11. Resilience and recovery

```mermaid
flowchart TB
    START(["Backend starts"]) --> Q{"Runs still marked running?"}
    Q -- "no" --> READY(["Ready"])
    Q -- "yes" --> SCAN["Scan /proc once:<br/>pid + start time, process group members"]
    SCAN --> ALIVE{"Recorded process alive?"}
    ALIVE -- "yes" --> KEEP["Item stays running<br/>(not monitored)"]
    ALIVE -- "no" --> UNK["Item cancelled:<br/>ended while backend was down, result unknown"]
    KEEP --> INT["Run marked interrupted,<br/>logs kept"]
    UNK --> INT
    INT --> UIB["Run page: banner with live process groups"]
    UIB --> KL["Kill leftovers: TERM, KILL after 2 s, verified"]
    INT --> READY
```

| Failure | Behaviour |
|---|---|
| Browser closed or network drop | Run continues; page resyncs from REST and replays missed events |
| `systemctl stop/restart` | App stops the active run's process groups itself (cancelled), then exits |
| App crash under systemd | Restart after 5 s; remaining processes in the service cgroup are stopped; run interrupted |
| App killed outside systemd (`kill -9`) | Workloads keep running (charbench ignores the broken pipe); next start marks the run interrupted and lists them for Kill leftovers |
| Failed SYSDBA logon with exit code 0 | Detected by the missing session marker; reported, never treated as success |
| Load failure | Other loads finish; no new starts unless keep-going; Retry failed reruns only unfinished PDBs |
| Recreated PDB | `con_uid` differs from the marker, so it is loaded again |
| Host loss | Restore `orchestrator.db` from backup; run directories as needed |

---

## 12. Build, packaging and deployment pipeline

```mermaid
flowchart LR
    subgraph DEV["Development"]
        SRC["Source<br/>backend, frontend/src"]
        T["Tests<br/>pytest, vitest, Playwright"]
        UIB["npm run build<br/>frontend/dist + source hash"]
        GIT[("Git repository<br/>includes frontend/dist")]
        SRC --> T --> UIB --> GIT
    end
    subgraph BUILD["Build machine with internet"]
        BW["scripts/build-wheels.sh<br/>cp311 x86_64 wheels + lock"]
        BU["scripts/bundle.sh<br/>pdb-orchestrator-version.tgz"]
        BW --> BU
    end
    subgraph TARGET["Application host"]
        IO["scripts/install.sh<br/>online: pip, offline: wheels"]
        IS["scripts/install-service.sh<br/>unit + env file, enable, start"]
        RUN(["pdb-orchestrator.service"])
        IO --> IS --> RUN
    end
    GIT -- "git clone (online)" --> IO
    GIT --> BW
    BU -- "scp (offline)" --> IO
```

| Step | Who | Result |
|---|---|---|
| `scripts/install.sh` | app user | `venv/` with the backend and its dependencies (online or from bundled wheels) |
| `scripts/install-service.sh` | root | `/etc/systemd/system/pdb-orchestrator.service`, `/etc/pdb-orchestrator/orchestrator.env` (from the user's login environment; never overwritten), service enabled and started |
| `python -m app set-password` | app user | login for direct access |
| Upgrade | app user + root | `git pull` (or new bundle), `scripts/install.sh`, `systemctl restart`; database migrated automatically |

---

## 13. Quality and test architecture

```mermaid
flowchart TB
    subgraph SIM["Simulation mode, ORCH_MODE=simulate"]
        ST["Stub sqlplus, oewizard, charbench<br/>scenario.json: per-PDB failures, hangs,<br/>wrong container, ORA- errors, exit codes"]
        STATE[("Simulated CDB state<br/>PDBs, con_uid, open modes")]
        ST --- STATE
    end
    subgraph TESTS["Automated tests"]
        U["Unit: validators, builders, parsers,<br/>redaction, log scan"]
        G["Golden: SQL and argv compared with<br/>the reference scripts' text"]
        E["Engine: concurrency limits, failure policy,<br/>resume, timeouts, cancel without orphans"]
        A["API: validation, SSE replay, auth, CSRF,<br/>remote SYSDBA, restart recovery, kill -9"]
        L["Secret-leak suite over the whole lifecycle"]
        D["Deployment: service files, systemd-analyze"]
        F["Frontend: shared validation vectors,<br/>stale-build guard"]
        P["Browser (Playwright): 250-PDB runs, UI flows"]
    end
    TESTS --> SIM
```

---

## 14. Constraints and known limits

| Topic | Constraint |
|---|---|
| Scale | One CDB and one application host per installation; one active run at a time |
| Platform | Linux x86_64, Python 3.11, SQL*Plus on the host; Swingbench and Java where the app runs |
| Credentials | oewizard and charbench accept passwords only as arguments (visible in `ps`) |
| Results | Swingbench result XML is downloaded, not parsed; comparisons use exit codes, durations and log errors |
| Sessions | SYS password and logins live in memory; a restart requires entering them again |
| Crash | Console output of workloads that outlive a crash is lost; their result XML is still written |
| Resources | 250 charbench JVMs plus `PDBs x users` sessions load both hosts; the UI warns before heavy runs |
