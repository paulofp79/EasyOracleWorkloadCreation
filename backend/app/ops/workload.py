"""Run one charbench per PDB at the same time (run_charbench_all_pdbs.sh).

Phase 1  connection check of every PDB (abort on any failure, or skip unreachable PDBs).
Phase 2  launch every charbench before waiting for any (optional start delay between launches),
         each in its own session/process group; report each workload as it ends.
Phase 3  scan every charbench log for error text (exit code 0 does not prove there were none).
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from pathlib import Path
from typing import Any

from ..cmdbuild import charbench_argv, display
from ..db import utcnow
from ..engine.runner import ToolNotFound
from ..logscan import ERR_REGEX, FIRST_MAX, scan_file
from ..redact import MASK
from .base import Operation, PlanError, SysPasswordMissing
from .check_conn import CHECK_PARAMS, CONN_DIR, CONNECTION_PARAMS, check_summary, connect_for, run_checks
from .inventory import InventoryError, query_preflight

HEARTBEAT_LOG_S = 60
SHOW = 20
HEAVY_SESSIONS = 1000
HEAVY_PDBS_NO_DELAY = 100


def runtime_seconds(rt: str) -> int:
    parts = [int(x) for x in rt.split(":")]
    h, m, s = (parts + [0])[:3]
    return h * 3600 + m * 60 + s


def fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 60}m{s % 60:02d}s"


def exit_status(rc: int | None) -> int | None:
    """Python reports a signal as -N; bash `wait` (and the script's status.tsv) shows 128+N."""
    return 128 - rc if rc is not None and rc < 0 else rc


class Workload(Operation):
    type = "workload"
    PARAMS = {**CONNECTION_PARAMS, **CHECK_PARAMS, "first": "pdb_number", "last": "pdb_number",
              "skip_unreachable": "boolean", "users": "positive_int", "runtime": "runtime",
              "charbench_path": "abs_path", "config_xml": "abs_path", "disable_txns": "disable_txns",
              "start_delay": "start_delay"}
    OPTIONAL = {"pdbs": "pdb_names"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "workload_first", "last": "workload_last",
                        "scan_host": "scan_host", "scan_port": "scan_port", "service_suffix": "service_suffix",
                        "check_timeout": "check_timeout", "check_parallel": "check_parallel",
                        "skip_unreachable": "skip_unreachable", "users": "users", "runtime": "runtime",
                        "charbench_path": "charbench_path", "config_xml": "config_xml",
                        "disable_txns": "disable_txns", "start_delay": "start_delay", "log_dir": "log_dir",
                        "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    SECRETS = {"soe_password": "quoted_password", "sys_password": "password"}

    @classmethod
    def secret_spec(cls, params):
        return {"soe_password": "quoted_password"}

    def argv(self, run_dir: Path, pdb: str, password: str) -> list[str]:
        p = self.params
        return charbench_argv(p["charbench_path"], connect_for(p, pdb), password, p["config_xml"], p["users"],
                              p["disable_txns"], p["runtime"], str(run_dir / f"{pdb}-results.xml"))

    def tool_problems(self) -> list[str]:
        p = self.params
        problems = []
        cb = Path(p["charbench_path"])
        if not (cb.is_file() and os.access(cb, os.X_OK)):
            problems.append(f"Not executable: {cb}")
        cfg = Path(p["config_xml"])
        if not (cfg.is_file() and os.access(cfg, os.R_OK)):
            problems.append(f"Cannot read config: {cfg}")
        return problems

    def load_estimate(self, n: int) -> dict[str, Any]:
        p = self.params
        sessions = n * p["users"]
        heavy = sessions >= HEAVY_SESSIONS or (n >= HEAVY_PDBS_NO_DELAY and p["start_delay"] == 0)
        return {"processes": n, "sessions": sessions, "heavy": heavy,
                "message": f"{n} concurrent charbench Java processes and {sessions} database sessions"
                           + (" with no start delay" if p["start_delay"] == 0 and n > 1 else "")}

    def equivalent_command(self) -> str:
        p = self.params
        cmd = ["run_charbench_all_pdbs.sh", "-f", str(p["first"]), "-l", str(p["last"]), "-p", p["prefix"],
               "--scan-host", p["scan_host"], "--scan-port", str(p["scan_port"])]
        if p["service_suffix"]:
            cmd += ["--service-suffix", p["service_suffix"]]
        cmd += ["-u", str(p["users"]), "-r", p["runtime"], "-c", p["config_xml"], "-b", p["charbench_path"],
                "-d", p["disable_txns"]]
        if p["start_delay"]:
            cmd += ["--start-delay", f"{p['start_delay']:g}"]
        cmd += ["--connect-timeout", str(p["check_timeout"]), "--connect-parallel", str(p["check_parallel"])]
        if p["skip_unreachable"]:
            cmd.append("--skip-unreachable")
        cmd += ["-L", p["log_dir"]]
        text = f"SOE_PASSWORD='{MASK}' " + " ".join(shlex.quote(c) for c in cmd)
        if p.get("pdbs"):
            text += f"   # app: only the {len(p['pdbs'])} selected PDBs"
        return text

    async def plan(self) -> dict[str, Any]:
        pdbs = self.targets()
        problems = self.tool_problems()
        warnings = list(problems)
        try:
            live = await query_preflight(self.settings, self.params["prefix"], self.sysdba())
        except SysPasswordMissing:
            live = None
            warnings.append("PDB existence not verified: no SYS password in this browser session.")
        except (InventoryError, ToolNotFound) as exc:
            raise PlanError(f"Could not read gv$pdbs: {exc}") from exc
        missing = [x for x in pdbs if live is not None and x not in live]
        not_open = [x for x in pdbs if live is not None and x in live and live[x][1] < 1]
        bad = len(missing) + len(not_open)
        if bad:
            policy = ("they will be skipped (skip unreachable)" if self.params["skip_unreachable"]
                      else "the connection check will fail and no workload will start")
            warnings.append(f"{bad} PDB(s) are missing or not open READ WRITE: {policy}")
        est = self.load_estimate(len(pdbs) - (bad if self.params["skip_unreachable"] else 0))
        run_s = runtime_seconds(self.params["runtime"])
        return {
            "type": self.type, "pdbs": pdbs, "count": len(pdbs), "missing": missing, "not_open": not_open,
            "blocked": bool(problems), "warnings": warnings, "load": est,
            "runtime_s": run_s, "launch_s": round(self.params["start_delay"] * max(0, est["processes"] - 1), 1),
            "example_connect": connect_for(self.params, pdbs[0]),
            "command_preview": display(self.argv(Path(self.params["log_dir"]) / "run-<id>", pdbs[0], MASK)),
            "equivalent_command": self.equivalent_command(),
        }

    # ---- execution ------------------------------------------------------------------------
    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        p = self.params
        pdbs = self.targets()
        summary: dict[str, Any] = {}
        problems = self.tool_problems()
        if problems:
            ctx.update_run(message="; ".join(problems) + ". Nothing was started.")
            return "blocked", summary

        # ---- phase 1: connection check
        ctx.update_run(phase="check")
        ctx.log(f"checking SOE connections to {len(pdbs)} PDBs ({pdbs[0]} .. {pdbs[-1]}), "
                f"{p['check_parallel']} at a time, timeout {p['check_timeout']}s")
        t0 = time.monotonic()
        fail_state = "skipped" if p["skip_unreachable"] else "failed"
        results = await run_checks(ctx, pdbs, ok_state="pending", fail_state=fail_state)
        cs = check_summary(pdbs, results)
        summary["check"] = cs
        good = [x for x in pdbs if x in results and results[x].status == "OK"]
        bad = [x for x in pdbs if x in results and results[x].status != "OK"]
        ctx.log(f"connection check finished in {fmt_time(time.monotonic() - t0)}: {len(good)} OK, {len(bad)} failed")
        if ctx.cancelled:
            return "cancelled", summary
        if bad and not p["skip_unreachable"]:
            ctx.update_run(message=f"Not starting any workload because {len(bad)} PDB(s) failed the connection "
                                   f"check. Fix them, or turn on 'skip unreachable' to run on the {len(good)} "
                                   "that passed.")
            return "blocked", summary
        if not good:
            ctx.update_run(message="No PDB passed the connection check; nothing to run.")
            return "blocked", summary
        if bad:
            ctx.log(f"continuing with the {len(good)} PDB(s) that passed (skip unreachable)")
        for x in bad:
            ctx.set_item(x, detail=f"connection check failed: {results[x].detail}")

        # ---- phase 2: launch everything, then wait
        ctx.update_run(phase="launch")
        bin_dir = os.path.dirname(p["charbench_path"])
        password = self.secrets["soe_password"]
        ctx.write_artifact("processes.tsv", "pdb\tpid\tservice\n")
        ctx.write_artifact("status.tsv", "pdb\texit_code\tseconds\n")
        status_rows: list[tuple[str, int | None, int]] = []
        ctx.log(f"starting {len(good)} workloads: {p['users']} users per PDB, runtime {p['runtime']}")
        launched = time.monotonic()

        async def run_one(pdb: str, started: asyncio.Event) -> None:
            t_start = time.monotonic()
            errors = {"n": 0, "first": ""}

            def on_start(res) -> None:
                nonlocal t_start
                t_start = time.monotonic()
                ctx.set_item(pdb, state="running", started_at=utcnow(), pid=res.pid, pgid=res.pgid,
                             proc_starttime=res.starttime, detail=None, error_count=0)
                with open(ctx.run_dir / "processes.tsv", "a") as fh:
                    fh.write(f"{pdb}\t{res.pid}\t{connect_for(p, pdb)}\n")
                started.set()

            def on_line(line: str) -> None:            # live error flag while the workload runs
                if ERR_REGEX.search(line):
                    errors["n"] += 1
                    if not errors["first"]:
                        errors["first"] = line.replace("\t", " ")[:FIRST_MAX]
                    ctx.set_item(pdb, error_count=errors["n"], first_error=errors["first"])

            try:
                r = await ctx.runner.run(self.argv(ctx.run_dir, pdb, password), cwd=bin_dir,
                                         log_path=ctx.run_dir / f"{pdb}-charbench.log", on_start=on_start,
                                         on_line=on_line)
            except ToolNotFound as exc:
                ctx.set_item(pdb, state="failed", detail=str(exc), ended_at=utcnow())
                return
            finally:
                started.set()
            if r.pid is None:                           # stop requested before it could start
                return
            rc = exit_status(r.exit_code)
            dur = int(time.monotonic() - t_start)
            status_rows.append((pdb, rc, dur))
            with open(ctx.run_dir / "status.tsv", "a") as fh:
                fh.write(f"{pdb}\t{rc}\t{dur}\n")
            if r.cancelled:
                ctx.set_item(pdb, state="cancelled", exit_code=rc, ended_at=utcnow(), detail="stopped by the user")
                ctx.log(f"stopped {pdb} after {fmt_time(dur)}")
            elif rc == 0:
                ctx.set_item(pdb, state="ok", exit_code=0, ended_at=utcnow(), detail=f"finished in {fmt_time(dur)}")
                ctx.log(f"finished {pdb} in {fmt_time(dur)}")
            else:
                ctx.set_item(pdb, state="failed", exit_code=rc, ended_at=utcnow(),
                             detail=f"charbench exited with {rc} after {fmt_time(dur)}")
                ctx.log(f"FAILED: {pdb}, exit {rc}; see {pdb}-charbench.log")

        tasks: list[asyncio.Task] = []
        delay = p["start_delay"]
        for idx, pdb in enumerate(good):
            if ctx.cancelled:
                break
            started = asyncio.Event()
            tasks.append(asyncio.create_task(run_one(pdb, started)))
            await started.wait()                        # its process exists before the next one starts
            if idx < len(good) - 1 and delay > 0:
                end = time.monotonic() + delay
                while not ctx.cancelled and time.monotonic() < end:
                    await asyncio.sleep(min(0.2, end - time.monotonic()))
        ctx.log(f"all {len(tasks)} workloads launched in {fmt_time(time.monotonic() - launched)}; "
                "waiting for completion")
        ctx.update_run(phase="run")

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_LOG_S)
                running = sum(1 for t in tasks if not t.done())
                ctx.log(f"... {running} of {len(tasks)} workloads still running "
                        f"({fmt_time(time.monotonic() - launched)} elapsed)")

        beat = asyncio.create_task(heartbeat())
        try:
            await asyncio.gather(*tasks)
        finally:
            beat.cancel()

        # ---- phase 3: summary with the log scan
        ctx.update_run(phase="summary")
        lines = ["pdb\texit_code\terror_lines\tfirst_error"]
        with_errors = []
        for pdb, rc, _ in status_rows:
            res = scan_file(ctx.run_dir / f"{pdb}-charbench.log")
            ctx.set_item(pdb, error_count=res.error_lines, first_error=res.first_error or None)
            lines.append(f"{pdb}\t{rc}\t{res.error_lines}\t{res.first_error}")
            if res.error_lines:
                with_errors.append({"pdb": pdb, "lines": res.error_lines, "first": res.first_error})
        ctx.write_artifact("summary.tsv", "\n".join(lines) + "\n")
        failed = [r for r in status_rows if r[1] != 0]
        summary.update({
            "started": len(status_rows), "ok": len(status_rows) - len(failed), "failed": len(failed),
            "skipped": len(bad), "with_errors": len(with_errors), "error_pdbs": with_errors[:SHOW],
            "error_more": max(0, len(with_errors) - SHOW),
            "failed_pdbs": [{"pdb": x, "detail": ctx.items[x]["detail"]} for x, _, _ in failed][:SHOW],
            "failed_more": max(0, len(failed) - SHOW),
            "users": p["users"], "runtime": p["runtime"],
        })
        ctx.log(f"finished: {summary['ok']} workloads exited normally, {len(failed)} failed")
        if with_errors:
            ctx.log(f"error text found in the charbench logs of {len(with_errors)} PDB(s)")
        else:
            ctx.log("no error text found in the charbench logs")
        if failed or ctx.cancelled:
            return "failed", summary
        return "ok", summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return [("charbench", run_dir / f"{pdb}-charbench.log"),
                ("connection check", run_dir / CONN_DIR / f"{pdb}.log")]
