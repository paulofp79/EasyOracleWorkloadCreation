# Installing on Oracle Linux

Target: Oracle Linux 8 or 9 (x86_64). The app runs as `oracle` (or the user that owns Swingbench),
needs no root at run time, and listens on `127.0.0.1:8080` unless you choose direct access (step 6).
Day-to-day operation (service, logs, backups, upgrades, troubleshooting): [`OPERATIONS.md`](OPERATIONS.md).

Pick one of two layouts (profile setting "CDB connection"):

| Layout | Privileged SQL | Needs on the app host |
|---|---|---|
| **Local**: app on a database node | `sqlplus / as sysdba` (OS authentication) | Oracle home of the CDB, `ORACLE_SID`, Swingbench + Java |
| **Remote**: app on a separate server | `sqlplus /nolog` + `CONNECT sys/"…"@//<scan>:<port>/<CDB root service> AS SYSDBA` | Oracle Instant Client with SQL*Plus, Swingbench + Java, network to SCAN:1521 |

In remote mode the SYS password is entered once per browser session ("SYS password" in the top
bar), checked with a SYSDBA logon, kept in server memory only and given to sqlplus on stdin. The CDB
needs a password file that allows remote SYSDBA logons (`remote_login_passwordfile=EXCLUSIVE`, the
usual RAC setting); the CDB root service is typically `<db_unique_name>.<db_domain>`
(`lsnrctl status` or `select name from v$services` in CDB$ROOT shows it).

Two ways to install: **A** when the server can reach GitHub and PyPI (directly, through a proxy or a
pip mirror), **B** from an offline bundle.

## A. Online install

### 1. Prerequisites (as root)

```bash
dnf install -y git python3.11 python3.11-pip      # OL 8.8+ / 9.2+
```

* If dnf stops with "Failed to download metadata for repo '<name>'" (an internal repository that needs
  credentials), add `--disablerepo=<name>` to the dnf command.
* Node.js is **not** needed: the built web UI is part of the repository (`frontend/dist`).
* Swingbench must be installed for the app user on this host, with the Java version your Swingbench
  release needs (e.g. `dnf install -y java-17-openjdk`).

**Remote layout only: Oracle Instant Client with SQL*Plus** (Oracle Linux yum server):

```bash
dnf install -y oracle-instantclient-release-23ai-el8      # -el9 on OL9; 23ai clients work with 19c+
dnf install -y oracle-instantclient-basic oracle-instantclient-sqlplus
```

`dnf search oracle-instantclient` lists the names for other client releases. If only `sqlplus64` ends
up on the PATH, add the client's `bin` directory to the app user's PATH. Check the remote SYSDBA logon
exactly as the app does it (password read without echo, given to sqlplus on stdin); it prints `CDB$ROOT`:

```bash
read -rs -p "SYS password: " SYSPW; echo
printf 'CONNECT sys/"%s"@//%s AS SYSDBA\nselect sys_context(%s) from dual;\nexit\n' "$SYSPW" \
  "<scan-host>:1521/<cdb-root-service>" "'userenv','con_name'" | sqlplus -L -s /nolog
```

### 2. Get the code and install it (as the app user)

```bash
sudo su - oracle
git clone -b claude/gracious-ptolemy-uu1i4i https://github.com/paulofp79/EasyOracleWorkloadCreation.git ~/pdb-orchestrator
cd ~/pdb-orchestrator && scripts/install.sh
```

The repository is private: use a GitHub personal access token as the password, or the SSH URL with a
key added to GitHub. Once the work is merged to `main`, drop `-b claude/gracious-ptolemy-uu1i4i`.
`scripts/install.sh` creates `venv/` and installs the backend. Behind a proxy export `https_proxy`
first; with a pip mirror set `PIP_INDEX_URL=https://<mirror>/simple`.

### 3. Oracle environment (local layout only)

The local layout uses OS authentication exactly like the scripts, so the app needs the CDB
environment. Put it in the app user's `~/.bash_profile` (the service installer copies it from there):

```bash
export ORACLE_SID=<cdb instance on this node>
export ORAENV_ASK=NO; . oraenv >/dev/null
```

Check: `sqlplus -L -s / as sysdba <<< "select name, cdb from v\$database;"`. The remote layout needs
nothing here.

### 4. Try it by hand

```bash
cd ~/pdb-orchestrator/backend && ../venv/bin/python -m app
```

Ctrl-C stops it. Open it through an SSH tunnel from your workstation, then http://localhost:8080:

```bash
ssh -N -L 8080:127.0.0.1:8080 <user>@<app-host>
```

### 5. Install the service (as root)

```bash
sudo ~oracle/pdb-orchestrator/scripts/install-service.sh --user oracle
```

This writes `/etc/systemd/system/pdb-orchestrator.service` and `/etc/pdb-orchestrator/orchestrator.env`
(PATH, ORACLE_HOME, ORACLE_SID, LD_LIBRARY_PATH taken from the user's login shell), then enables and
starts the service. `--dry-run` prints both files without installing anything; an existing env file is
never overwritten. Logs: `journalctl -u pdb-orchestrator -f`.

### 6. Direct access over HTTPS (optional)

Instead of the SSH tunnel, the app can listen on the network. That requires a login and switches HTTPS
on (self-signed certificate generated on first start in `~/.pdb-orchestrator/tls/`, or your own).

```bash
cd ~/pdb-orchestrator/backend && ../venv/bin/python -m app set-password       # as oracle; user 'admin'
```

Then either install the service with `--host 0.0.0.0 --port 8443`, or set `ORCH_HOST=0.0.0.0` and
`ORCH_PORT=8443` in `/etc/pdb-orchestrator/orchestrator.env` and `systemctl restart pdb-orchestrator`.
Open the port (as root):

```bash
firewall-cmd --permanent --add-port=8443/tcp && firewall-cmd --reload
```

Browse to `https://<app-host>:8443`; the browser warns once about the self-signed certificate (compare
the SHA-256 fingerprint in `journalctl -u pdb-orchestrator` with the one it shows). Your own
certificate: `ORCH_TLS_CERT` / `ORCH_TLS_KEY` in the env file. Behind a TLS reverse proxy: `ORCH_TLS=off`.

### 7. First use

1. **Profiles**: SCAN host and port, service suffix, **CDB connection** (local/remote) and the **CDB
   root service** for remote, RAC CDB, `oewizard`/`charbench` paths, config XML, log directory, defaults.
2. Remote layout: **SYS password needed** in the top bar.
3. **Environment**: "Run check again" until everything passes.
4. **PDBs**: confirm the inventory matches `gv$pdbs`, then start with **Check connections** on a few PDBs.

Rehearsal without touching Oracle: `ORCH_MODE=simulate ORCH_PORT=8081 ../venv/bin/python -m app` (from
`backend/`) runs the whole UI against the stub tools (separate database under `var/sim`).

## B. Offline install (no internet on the server)

On any Linux x86_64 machine with `python3.11` and access to PyPI (or a mirror):

```bash
git clone -b claude/gracious-ptolemy-uu1i4i https://github.com/paulofp79/EasyOracleWorkloadCreation.git pdb-orchestrator
cd pdb-orchestrator && scripts/bundle.sh            # dist/pdb-orchestrator-<version>.tgz (about 4 MB)
scp dist/pdb-orchestrator-*.tgz oracle@<app-host>:
```

On the server (`dnf install -y python3.11` as root first), as the app user:

```bash
tar xzf pdb-orchestrator-*.tgz -C ~ && cd ~/pdb-orchestrator && scripts/install.sh --offline
```

Then continue with steps 3–7. The bundle contains the code, the built UI, the Python wheels for
python3.11/x86_64, the scripts and the docs.

## Updating

```bash
cd ~/pdb-orchestrator && git pull && scripts/install.sh       # offline: unpack the new bundle over it
sudo systemctl restart pdb-orchestrator                         # when no run is in progress
```

The database is migrated automatically on start; profiles, logins and run history are kept.
