"""Environment check (PROJECT_CONTEXT §3.A). Not a Run: read-only, nothing is written to the database."""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..engine.runner import ProcessRunner, ToolNotFound
from ..parsing import first_error
from ..redact import Redactor
from ..sqlbuild import ENV_CHECK_TOKEN, env_check_sql
from ..sysdba import SysdbaConn, SysPasswordMissing

LOW_DISK_BYTES = 2 * 1024 ** 3


@dataclass
class Check:
    id: str
    label: str
    status: str = "pass"          # pass | warn | fail | skip
    detail: str = ""
    error: str = ""


def _gib(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GiB"


def check_executable(cid: str, label: str, path: str) -> Check:
    c = Check(cid, label)
    p = Path(path)
    if not p.exists():
        c.status, c.error = "fail", f"{path}: no such file"
    elif not p.is_file():
        c.status, c.error = "fail", f"{path}: not a regular file"
    elif not os.access(p, os.X_OK):
        c.status, c.error = "fail", f"{path}: not executable by this user"
    else:
        c.detail = str(p.resolve())
    return c


def check_readable(cid: str, label: str, path: str) -> Check:
    c = Check(cid, label)
    p = Path(path)
    if not p.is_file():
        c.status, c.error = "fail", f"{path}: no such file"
    elif not os.access(p, os.R_OK):
        c.status, c.error = "fail", f"{path}: not readable by this user"
    else:
        try:
            head = p.read_bytes()[:512].lstrip()
        except OSError as exc:
            c.status, c.error = "fail", f"{path}: {exc.strerror}"
            return c
        c.detail = f"{p.resolve()} ({p.stat().st_size} bytes)"
        if not head.startswith(b"<"):
            c.status, c.error = "warn", "file does not look like XML"
    return c


def check_log_dir(path: str) -> Check:
    c = Check("log_dir", "Log / run directory")
    p = Path(path)
    probe = p
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.is_dir():
        c.status, c.error = "fail", f"{probe}: not a directory"
        return c
    if not os.access(probe, os.W_OK | os.X_OK):
        c.status, c.error = "fail", f"{probe}: not writable by this user"
        return c
    free = shutil.disk_usage(probe).free
    where = str(p) if p.exists() else f"{p} (will be created under {probe})"
    c.detail = f"{where}; free {_gib(free)}"
    if free < LOW_DISK_BYTES:
        c.status, c.error = "warn", f"only {_gib(free)} free"
    return c


async def run_env_check(settings: Settings, params: dict[str, Any], sys_password: str | None = None) -> dict[str, Any]:
    conn = SysdbaConn.from_params(params, sys_password)
    runner = ProcessRunner(settings.tool_env(), Redactor([sys_password] if sys_password else []))
    checks: list[Check] = []

    checks.append(Check("mode", "Mode", "warn" if settings.simulate else "pass",
                        "simulation (stub executables from tools/stubs)" if settings.simulate
                        else "real Oracle tools"))

    # Oracle environment variables
    env = runner.env
    c = Check("oracle_env", "ORACLE_HOME / ORACLE_SID")
    if settings.simulate:
        c.status, c.detail = "skip", "not needed in simulation"
    elif conn.remote:
        c.status, c.detail = "skip", "not needed: the CDB is reached over the network (remote mode)"
    else:
        missing = [k for k in ("ORACLE_HOME", "ORACLE_SID") if not env.get(k)]
        c.detail = ", ".join(f"{k}={env.get(k, '')}" for k in ("ORACLE_HOME", "ORACLE_SID"))
        if missing:
            c.status, c.error = "warn", f"not set: {', '.join(missing)} (OS-authenticated sqlplus needs both)"
    checks.append(c)

    # sqlplus on PATH
    c = Check("sqlplus", "sqlplus found")
    sqlplus = runner.which("sqlplus")
    if sqlplus is None:
        c.status, c.error = "fail", "sqlplus not found in PATH"
    else:
        try:
            r = await runner.run([sqlplus, "-V"], timeout=15)
            version = next((ln.strip() for ln in r.tail if ln.strip()), "")
            c.detail = f"{sqlplus} — {version}" if version else sqlplus
        except ToolNotFound as exc:
            c.status, c.error = "fail", str(exc)
    checks.append(c)

    # SYSDBA connection to the CDB root (local OS authentication or remote sys@//scan/service)
    c = Check("sysdba", f"Connect {conn.describe()} (CDB root)")
    if sqlplus is None:
        c.status, c.error = "skip", "sqlplus not available"
    elif conn.remote and not conn.service:
        c.status, c.error = "fail", "remote mode needs the CDB root service name (profile: CDB service)"
    elif conn.remote and not conn.password:
        c.status, c.error = "warn", str(SysPasswordMissing())
    else:
        argv = conn.argv()
        argv[0] = sqlplus
        r = await runner.run(argv, stdin_text=conn.stdin(env_check_sql()), timeout=30)
        line = next((ln for ln in r.tail if ln.startswith(ENV_CHECK_TOKEN)), None)
        if r.timed_out:
            c.status, c.error = "fail", "timed out after 30s"
        elif line is None:
            c.status = "fail"
            c.error = first_error(r.output) or f"no output from sqlplus (exit code {r.exit_code})"
        else:
            parts = [p.strip() for p in line.split("|")]
            parts += [""] * (7 - len(parts))
            _, name, version, n_inst, is_cdb, con, cluster = parts[:7]
            rac = cluster.upper() == "TRUE"
            c.detail = (f"CDB {name}, version {version}, {n_inst} instance(s) open, "
                        f"{'RAC' if rac else 'single instance'}, container {con}")
            if is_cdb.upper() != "YES":
                c.status, c.error = "fail", f"{name} is not a container database"
            elif con.upper() != "CDB$ROOT":
                c.status, c.error = "fail", f"session is in {con}, expected CDB$ROOT (use the CDB root service)"
            elif bool(params.get("rac_cdb")) != rac:
                c.status = "warn"
                c.error = (f"profile says RAC CDB = {str(bool(params.get('rac_cdb'))).lower()}, "
                           f"but cluster_database = {cluster or 'unknown'}")
    checks.append(c)

    checks.append(check_executable("oewizard", "oewizard executable", params["oewizard_path"]))
    checks.append(check_executable("charbench", "charbench executable", params["charbench_path"]))
    checks.append(check_readable("config_xml", "Swingbench config XML readable", params["config_xml"]))
    checks.append(check_log_dir(params["log_dir"]))

    worst = "pass"
    for ch in checks:
        if ch.status == "fail":
            worst = "fail"
        elif ch.status == "warn" and worst == "pass" and ch.id != "mode":
            worst = "warn"
    return {"status": worst, "mode": settings.mode, "checks": [asdict(ch) for ch in checks]}
