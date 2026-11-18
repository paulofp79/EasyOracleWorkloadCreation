"""SOE connection check (run_charbench_all_pdbs.sh step 1), standalone or as phase 1 of a workload."""
from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from ..db import utcnow
from ..engine.runner import ToolNotFound
from ..naming import login_user, pdb_connect_string, schema_user
from ..parsing import parse_check
from ..sqlbuild import check_sql
from .base import Operation, PlanError, SysPasswordMissing
from .inventory import InventoryError, query_preflight

NOLOG = ["sqlplus", "-L", "-s", "/nolog"]
TIMEOUT_GRACE = 5          # timeout -k 5
CONN_DIR = "connection-check"
SHOW_FAILED = 20

CONNECTION_PARAMS = {"prefix": "prefix", "scan_host": "scan_host", "scan_port": "scan_port",
                     "service_suffix": "service_suffix", "log_dir": "abs_path",
                     "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
CHECK_PARAMS = {"check_timeout": "positive_int", "check_parallel": "positive_int"}


def connect_for(params: dict[str, Any], pdb: str) -> str:
    return pdb_connect_string(pdb, params["scan_host"], params["scan_port"], params["service_suffix"])


async def run_checks(ctx, pdbs: list[str], *, ok_state: str = "ok", fail_state: str = "failed") -> dict[str, Any]:
    """Check every PDB, at most check_parallel at a time. Returns {pdb: CheckResult} plus writes
    connection-check/<PDB>.log, connections.tsv and the per-PDB last check."""
    p = ctx.params
    conn_dir = ctx.run_dir / CONN_DIR
    conn_dir.mkdir(mode=0o700, exist_ok=True)
    sem = asyncio.Semaphore(p["check_parallel"])
    password = ctx.op.secrets["soe_password"]
    results: dict[str, Any] = {}

    async def one(pdb: str) -> None:
        async with sem:
            if ctx.cancelled:
                return
            ctx.set_item(pdb, state="checking", started_at=utcnow())
            r = await ctx.runner.run(
                NOLOG, stdin_text=check_sql(password, connect_for(p, pdb), login_user(p.get("schema_prefix", ""))),
                log_path=conn_dir / f"{pdb}.log",
                timeout=p["check_timeout"], timeout_grace=TIMEOUT_GRACE,
                on_start=lambda res: ctx.set_item(pdb, pid=res.pid, pgid=res.pgid, proc_starttime=res.starttime))
            if r.cancelled:
                ctx.set_item(pdb, state="cancelled", ended_at=utcnow(), detail="stopped by the user",
                             exit_code=r.exit_code)
                return
            res = parse_check(pdb, r.output, r.exit_code, r.timed_out, p["check_timeout"])
            results[pdb] = res
            ctx.set_item(pdb, state=ok_state if res.status == "OK" else fail_state, check_status=res.status,
                         check_detail=res.detail, instance=res.instance, exit_code=r.exit_code,
                         ended_at=utcnow(), detail=None if res.status == "OK" else res.detail)
            ctx.db.upsert_last_check(pdb, res.status, res.detail, res.instance, ctx.run_id,
                                     schema_user(p.get("schema_prefix", "")))

    await asyncio.gather(*(one(pdb) for pdb in pdbs))

    lines = ["pdb\tstatus\tdetail"]
    for pdb in pdbs:
        if pdb in results:
            lines.append(f"{pdb}\t{results[pdb].status}\t{results[pdb].detail}")
        else:
            lines.append(f"{pdb}\t{'CANCELLED' if ctx.cancelled else 'FAIL'}\t"
                         f"{'stopped before the check finished' if ctx.cancelled else 'no result recorded'}")
    ctx.write_artifact("connections.tsv", "\n".join(lines) + "\n")
    return results


def check_summary(pdbs: list[str], results: dict[str, Any]) -> dict[str, Any]:
    failed = [(p, results[p].detail) for p in pdbs if p in results and results[p].status != "OK"]
    return {"checked": len(results), "ok": sum(1 for r in results.values() if r.status == "OK"),
            "failed": len(failed), "failed_pdbs": [{"pdb": p, "detail": d} for p, d in failed[:SHOW_FAILED]],
            "failed_more": max(0, len(failed) - SHOW_FAILED)}


class CheckConnections(Operation):
    type = "check"
    PARAMS = {**CONNECTION_PARAMS, **CHECK_PARAMS, "first": "pdb_number", "last": "pdb_number"}
    OPTIONAL = {"pdbs": "pdb_names", "schema_prefix": "schema_prefix"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "workload_first", "last": "workload_last",
                        "schema_prefix": "schema_prefix",
                        "scan_host": "scan_host", "scan_port": "scan_port", "service_suffix": "service_suffix",
                        "check_timeout": "check_timeout", "check_parallel": "check_parallel", "log_dir": "log_dir",
                        "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    # SYS is only used by the plan (existence pre-check); the check itself logs in as SOE.
    SECRETS = {"soe_password": "quoted_password", "sys_password": "password"}

    @classmethod
    def secret_spec(cls, params):
        return {"soe_password": "quoted_password"}

    def equivalent_command(self) -> str:
        p = self.params
        cmd = ["run_charbench_all_pdbs.sh", "--check-only", "-f", str(p["first"]), "-l", str(p["last"]),
               "-p", p["prefix"], "--scan-host", p["scan_host"], "--scan-port", str(p["scan_port"])]
        if p["service_suffix"]:
            cmd += ["--service-suffix", p["service_suffix"]]
        cmd += ["--connect-timeout", str(p["check_timeout"]), "--connect-parallel", str(p["check_parallel"]),
                "-L", p["log_dir"]]
        text = "SOE_PASSWORD='********' " + " ".join(shlex.quote(c) for c in cmd)
        if p.get("pdbs"):
            text += f"   # app: only the {len(p['pdbs'])} selected PDBs"
        if p.get("schema_prefix"):
            text += f"   # app: schema {schema_user(p['schema_prefix'])} (the script always uses SOE)"
        return text

    async def plan(self) -> dict[str, Any]:
        pdbs = self.targets()
        unverified = None
        try:
            live = await query_preflight(self.settings, self.params["prefix"], self.sysdba())
        except SysPasswordMissing:
            live, unverified = None, "PDB existence not verified: no SYS password in this browser session."
        except (InventoryError, ToolNotFound) as exc:
            raise PlanError(f"Could not read gv$pdbs: {exc}") from exc
        missing = [p for p in pdbs if live is not None and p not in live]
        not_open = [p for p in pdbs if live is not None and p in live and live[p][1] < 1]
        return {
            "type": self.type, "pdbs": pdbs, "count": len(pdbs),
            "missing": missing, "not_open": not_open,
            "warnings": ([unverified] if unverified else []) +
                        ([f"{len(missing)} PDB(s) do not exist and will fail the check"] if missing else []) +
                        ([f"{len(not_open)} PDB(s) are not open READ WRITE and will fail the check"]
                         if not_open else []),
            "example_connect": connect_for(self.params, pdbs[0]),
            "schema_user": schema_user(self.params.get("schema_prefix", "")),
            "equivalent_command": self.equivalent_command(),
        }

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        pdbs = self.targets()
        ctx.update_run(phase="check")
        ctx.log(f"checking SOE connections to {len(pdbs)} PDB(s), {self.params['check_parallel']} at a time, "
                f"timeout {self.params['check_timeout']}s")
        try:
            results = await run_checks(ctx, pdbs)
        except ToolNotFound as exc:
            ctx.update_run(message=str(exc))
            return "failed", {"error": str(exc)}
        summary = check_summary(pdbs, results)
        ctx.log(f"connection check finished: {summary['ok']} OK, {summary['failed']} failed")
        status = "ok" if summary["failed"] == 0 and summary["checked"] == len(pdbs) else "failed"
        return status, summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return [("connection check", run_dir / CONN_DIR / f"{pdb}.log")]
