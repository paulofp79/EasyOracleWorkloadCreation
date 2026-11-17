# Installing on Oracle Linux

Target: Oracle Linux 8 or 9 (x86_64) on a DB node, or an admin host with an Oracle client, where the
`oracle` OS user can run `sqlplus / as sysdba` against the CDB. The app runs as `oracle`, needs no
root at run time and listens on `127.0.0.1:8080` only.

Two ways to install:

* **A. The server can reach GitHub, PyPI and npm** (directly or through a proxy): steps 1–8.
* **B. The server has no internet access**: build a bundle elsewhere, then copy it (section B).

## A. Online install

### 1. Prerequisites (as root)

```bash
# Oracle Linux 8 (8.8 or later) and Oracle Linux 9 (9.2 or later)
dnf install -y git python3.11 python3.11-pip
# Node.js is only needed to build the web UI (once per update); skip it if you build elsewhere
dnf module install -y nodejs:20
```

Check: `python3.11 --version` (3.11.x) and `node --version` (v20 or later).

Swingbench must already be installed for `oracle` (with its Java), e.g. `/home/oracle/swingbench/bin/charbench`.

If the server reaches the internet through a proxy, export it in the shell you use for the following
steps: `export https_proxy=http://proxy.example.com:80 http_proxy=$https_proxy`.

### 2. Get the code (as oracle)

```bash
sudo su - oracle
git clone -b claude/gracious-ptolemy-uu1i4i https://github.com/paulofp79/EasyOracleWorkloadCreation.git ~/pdb-orchestrator
cd ~/pdb-orchestrator
```

The repository is private: use a GitHub personal access token as the password, or an SSH URL
(`git@github.com:paulofp79/EasyOracleWorkloadCreation.git`) with a key added to GitHub. Once the work
is merged to `main`, drop `-b claude/gracious-ptolemy-uu1i4i`.

### 3. Python environment and backend

```bash
cd ~/pdb-orchestrator
python3.11 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -e ./backend
```

### 4. Build the web UI

```bash
cd ~/pdb-orchestrator/frontend
npm ci
npm run build          # creates frontend/dist, served by the backend
cd ..
```

### 5. Oracle environment

The backend uses OS authentication exactly like the scripts, so the shell that starts it needs the
CDB environment:

```bash
export ORACLE_SID=<cdb instance on this node>      # e.g. the local instance of the RAC CDB
. oraenv <<< "$ORACLE_SID"                          # or export ORACLE_HOME and PATH yourself
sqlplus -L -s / as sysdba <<< "select name, cdb from v\$database;"    # must work before going on
```

### 6. Start

Foreground (Ctrl-C stops it):

```bash
cd ~/pdb-orchestrator
venv/bin/python -m app
```

To keep it running after you log out, use `tmux` (`dnf install -y tmux`) or:

```bash
cd ~/pdb-orchestrator
nohup venv/bin/python -m app >> ~/pdb-orchestrator/orchestrator.log 2>&1 &
```

Settings come from environment variables: `ORCH_PORT` (default 8080), `ORCH_DB` (default
`~/.pdb-orchestrator/orchestrator.db`). Binding to anything other than localhost is refused until
authentication exists (milestone M6). A systemd unit follows in M6.

### 7. Open the UI from your workstation

```bash
ssh -L 8080:127.0.0.1:8080 oracle@<db-host>
```

Then browse to http://localhost:8080. Through a jump host: `ssh -J user@bastion -L 8080:127.0.0.1:8080 oracle@<db-host>`.

### 8. First use

1. **Profiles**: edit the `default` profile: SCAN host and port, service suffix (e.g. `.us.oracle.com`),
   RAC CDB, `oewizard` and `charbench` paths, config XML, log/run directory, ranges and defaults.
2. **Environment**: "Run check again" until sqlplus, `/ as sysdba` (CDB name, version, instances),
   oewizard, charbench, config XML and the log directory all pass.
3. **PDBs**: confirm the inventory matches `gv$pdbs`.
4. Start with something harmless: **Check connections** on a few PDBs.

Optional rehearsal without touching Oracle: `ORCH_MODE=simulate ORCH_PORT=8081 venv/bin/python -m app`
runs the whole UI against the stub tools in `tools/stubs` (separate database under `var/sim`).

## B. Offline install (server without internet)

On a build machine with internet, `python3.11`, Node.js 20+ and the same CPU architecture (x86_64):

```bash
git clone -b claude/gracious-ptolemy-uu1i4i https://github.com/paulofp79/EasyOracleWorkloadCreation.git pdb-orchestrator
cd pdb-orchestrator
(cd frontend && npm ci && npm run build)
scripts/build-wheels.sh                    # dist/wheels/*.whl + dist/requirements.lock
cd .. && tar czf pdb-orchestrator.tgz --exclude=node_modules --exclude=.git pdb-orchestrator
scp pdb-orchestrator.tgz oracle@<db-host>:
```

On the server (as root: `dnf install -y python3.11`; Node.js and git are not needed), then as `oracle`:

```bash
tar xzf pdb-orchestrator.tgz && cd pdb-orchestrator
python3.11 -m venv venv
venv/bin/pip install --no-index --find-links dist/wheels -r dist/requirements.lock
venv/bin/pip install --no-index --find-links dist/wheels --no-deps pdb-orchestrator
# Oracle environment as in step 5, then:
ORCH_HOME=$PWD venv/bin/python -m app
```

`ORCH_HOME` tells the installed package where the built UI and the stubs are. Continue with steps 7–8.

## Updating

```bash
cd ~/pdb-orchestrator
git pull
venv/bin/pip install -e ./backend
(cd frontend && npm ci && npm run build)
# stop the running app (no run in progress), start it again as in step 6
```

The SQLite database is migrated automatically on start; profiles and run history are kept.

## Good to know

* **Stopping the app** (Ctrl-C, `kill`) stops the processes of a run in progress, like Stop all. If the
  app is killed hard (`kill -9`, host crash), workloads keep running; on the next start the run is
  marked *interrupted* and the run page offers **Kill leftovers**.
* **Data**: the database is `~/.pdb-orchestrator/orchestrator.db`; logs, TSVs and result XML files are in
  `<log dir>/run-<id>/` (mode 700, files 600), with `<log dir>/latest` pointing at the newest run.
* **Passwords** are entered per run, never stored, passed to sqlplus on stdin and masked in all logs.
  oewizard and charbench only accept them on the command line, so they are visible in `ps` while those
  run.
* **Do not run it as root**: it must be the OS user that owns the Oracle and Swingbench installation.
* **Disk**: each workload run writes one charbench log and one results file per PDB; check free space in
  the log directory (shown by the environment check).
