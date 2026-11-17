"""Load the Swingbench SOE schema into a range of PDBs (create_swingbench.sh).

* Pre-flight: every PDB must exist and be open READ WRITE, otherwise nothing starts.
* A PDB is skipped when this tool loaded it before and its con_uid is unchanged (unless force).
* Sliding window of `parallel` loads; after a failure no new PDB starts unless keep_going,
  running loads finish. Re-running the same request retries only failed / not-started PDBs.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from ..cmdbuild import display, oewizard_argv
from ..db import utcnow
from ..engine.runner import ToolNotFound
from ..naming import pdb_connect_string
from ..parsing import first_error
from ..redact import MASK
from ..sqlbuild import soe_setup_sql
from .base import Operation, PlanError, SysPasswordMissing
from .inventory import InventoryError, query_preflight

SHOW = 10
SHOW_FAILED = 20
ERROR_LINE = re.compile(r"(ORA|TNS)-[0-9]{5}|Exception|Error|failed", re.I)


def oewizard_reason(output: str, exit_code: int | None) -> str:
    """First ORA-/exception line, else the last non-blank line (the banner comes first, the reason last)."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in lines:
        if ERROR_LINE.search(ln):
            return ln[:200]
    return (lines[-1][:200] if lines else f"no output (exit code {exit_code})")


def short_list(names: list[str]) -> str:
    return " ".join(names[:SHOW]) + (" ..." if len(names) > SHOW else "")


class LoadSoe(Operation):
    type = "load"
    PARAMS = {"prefix": "prefix", "first": "pdb_number", "last": "pdb_number", "scan_host": "scan_host",
              "scan_port": "scan_port", "service_suffix": "service_suffix", "scale": "scale",
              "threads": "positive_int", "tablespace": "tablespace", "parallel": "positive_int",
              "keep_going": "boolean", "oewizard_path": "abs_path", "log_dir": "abs_path",
              "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    OPTIONAL = {"pdbs": "pdb_names", "force": "boolean"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "load_first", "last": "load_last", "scan_host": "scan_host",
                        "scan_port": "scan_port", "service_suffix": "service_suffix", "scale": "scale",
                        "threads": "threads", "tablespace": "tablespace", "parallel": "parallel",
                        "keep_going": "keep_going", "oewizard_path": "oewizard_path", "log_dir": "log_dir",
                        "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    SECRETS = {"soe_password": "quoted_password", "sys_password": "password"}

    @property
    def force(self) -> bool:
        return bool(self.params.get("force", False))

    def connect(self, pdb: str) -> str:
        p = self.params
        return pdb_connect_string(pdb, p["scan_host"], p["scan_port"], p["service_suffix"])

    def argv(self, pdb: str, soe_pw: str, sys_pw: str) -> list[str]:
        p = self.params
        return oewizard_argv(p["oewizard_path"], self.connect(pdb), soe_pw, p["scale"], p["threads"], sys_pw,
                             p["tablespace"])

    def equivalent_command(self) -> str:
        p = self.params
        cmd = ["create_swingbench.sh", "-f", str(p["first"]), "-l", str(p["last"]), "-p", p["prefix"],
               "--scan-host", p["scan_host"], "--scan-port", str(p["scan_port"])]
        if p["service_suffix"]:
            cmd += ["--service-suffix", p["service_suffix"]]
        cmd += ["-s", p["scale"], "-t", str(p["threads"]), "-T", p["tablespace"], "-o", p["oewizard_path"],
                "-n", str(p["parallel"]), "-L", p["log_dir"]]
        if p["keep_going"]:
            cmd.append("--keep-going")
        if self.force:
            cmd.append("--force")
        text = f"SOE_PASSWORD='{MASK}' DBA_PASSWORD='{MASK}' " + " ".join(shlex.quote(c) for c in cmd)
        if p.get("pdbs"):
            text += f"   # app: only the {len(p['pdbs'])} selected PDBs"
        return text

    def classify(self, live: dict[str, tuple[str, int]]) -> dict[str, Any]:
        """Same decision as the script's pre-flight loop."""
        markers = self.db.soe_markers(self.params["prefix"])
        missing, not_open, done, targets = [], [], [], []
        for pdb in self.targets():
            if pdb not in live:
                missing.append(pdb)
            elif live[pdb][1] < 1:
                not_open.append(pdb)
            elif not self.force and pdb in markers and markers[pdb]["con_uid"] == live[pdb][0]:
                done.append(pdb)
            else:
                targets.append(pdb)
        return {"missing": missing, "not_open": not_open, "already_loaded": done, "targets": targets,
                "markers": markers}

    async def plan(self) -> dict[str, Any]:
        try:
            live = await query_preflight(self.settings, self.params["prefix"], self.sysdba())
        except (InventoryError, ToolNotFound) as exc:
            raise PlanError(f"Could not check the PDBs: {exc}") from exc
        c = self.classify(live)
        warnings = []
        if c["missing"]:
            warnings.append(f"PDBs that do not exist ({len(c['missing'])}): {short_list(c['missing'])}")
        if c["not_open"]:
            warnings.append(f"PDBs not open READ WRITE ({len(c['not_open'])}): {short_list(c['not_open'])}")
        blocked = bool(c["missing"] or c["not_open"])
        if blocked:
            warnings.append("Fix the PDBs above (or adjust the range) — nothing would be loaded.")
        elif not c["targets"]:
            warnings.append("Nothing to do: every PDB is already loaded. Use force reload to load again.")
        first = c["targets"][0] if c["targets"] else self.targets()[0]
        return {
            "type": self.type, "pdbs": self.targets(), "count": len(self.targets()),
            "missing": c["missing"], "not_open": c["not_open"], "blocked": blocked,
            "already_loaded": c["already_loaded"], "to_load": c["targets"],
            "warnings": warnings,
            "command_preview": display(self.argv(first, MASK, MASK)),
            "equivalent_command": self.equivalent_command(),
        }

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        p = self.params
        ctx.update_run(phase="preflight")
        ctx.log(f"checking PDBs {self.targets()[0]} through {self.targets()[-1]}")
        try:
            live = await query_preflight(self.settings, p["prefix"], self.sysdba(), ctx.runner)
        except (InventoryError, ToolNotFound) as exc:
            raise PlanError(f"Could not check the PDBs: {exc}") from exc
        c = self.classify(live)
        summary: dict[str, Any] = {"loaded": 0, "skipped": len(c["already_loaded"]), "failed": 0, "not_started": 0,
                                   "failed_pdbs": []}
        if c["missing"] or c["not_open"]:
            for pdb in c["missing"]:
                ctx.set_item(pdb, detail="does not exist")
            for pdb in c["not_open"]:
                ctx.set_item(pdb, detail="not open READ WRITE", con_uid=live[pdb][0])
            parts = []
            if c["missing"]:
                parts.append(f"PDBs that do not exist ({len(c['missing'])}): {short_list(c['missing'])}")
            if c["not_open"]:
                parts.append(f"PDBs not open READ WRITE ({len(c['not_open'])}): {short_list(c['not_open'])}")
            ctx.update_run(message="; ".join(parts) + ". Nothing was loaded.")
            return "blocked", summary
        for pdb in c["already_loaded"]:
            m = c["markers"][pdb]
            ctx.set_item(pdb, state="skipped", con_uid=live[pdb][0],
                         detail=f"already loaded {m['loaded_at'][:19].replace('T', ' ')} (same con_uid)")
        targets = c["targets"]
        ctx.log(f"PDBs to load: {len(targets)}; already loaded and skipped: {len(c['already_loaded'])}")
        if not targets:
            return "ok", summary

        bin_dir = os.path.dirname(p["oewizard_path"])
        ctx.update_run(phase="load")
        ctx.log(f"loading {len(targets)} PDB(s): up to {p['parallel']} at a time, {p['threads']} threads each, "
                f"scale {p['scale']}")
        sem = asyncio.Semaphore(p["parallel"])
        failed: list[str] = []
        soe_pw, sys_pw = self.secrets["soe_password"], self.secrets["sys_password"]

        async def load_one(pdb: str) -> None:
            try:
                started = utcnow()
                on_start = lambda res: ctx.set_item(pdb, pid=res.pid, pgid=res.pgid,  # noqa: E731
                                                    proc_starttime=res.starttime)
                ctx.set_item(pdb, state="running", started_at=started, con_uid=live[pdb][0],
                             detail="step 1/2: GRANT DBA TO soe")
                conn = self.sysdba()
                r = await ctx.runner.run(conn.argv(), stdin_text=conn.stdin(soe_setup_sql(pdb, soe_pw)),
                                         log_path=ctx.run_dir / f"{pdb}-soe-setup.log", on_start=on_start)
                if r.cancelled:
                    ctx.set_item(pdb, state="cancelled", ended_at=utcnow(), detail="stopped during SOE setup")
                    return
                if r.exit_code != 0:
                    err = first_error(r.output) or f"exit code {r.exit_code}"
                    failed.append(pdb)
                    ctx.set_item(pdb, state="failed", ended_at=utcnow(), exit_code=r.exit_code,
                                 detail=f"SOE setup failed: {err}")
                    ctx.log(f"SOE setup failed for {pdb}: {err}")
                    return
                ctx.set_item(pdb, detail=f"step 2/2: oewizard (scale {p['scale']}, {p['threads']} threads)")
                r = await ctx.runner.run(self.argv(pdb, soe_pw, sys_pw), log_path=ctx.run_dir / f"{pdb}-oewizard.log",
                                         cwd=bin_dir, on_start=on_start)
                if r.cancelled:
                    ctx.set_item(pdb, state="cancelled", ended_at=utcnow(), detail="stopped during oewizard")
                    return
                if r.exit_code != 0:
                    err = oewizard_reason(r.output, r.exit_code)
                    failed.append(pdb)
                    ctx.set_item(pdb, state="failed", ended_at=utcnow(), exit_code=r.exit_code,
                                 detail=f"Swingbench failed: {err}")
                    ctx.log(f"Swingbench failed for {pdb}: {err}")
                    return
                ctx.db.set_soe_marker(pdb, live[pdb][0], ctx.run_id, p["scale"], p["tablespace"])
                ctx.set_item(pdb, state="ok", ended_at=utcnow(), exit_code=0,
                             detail=f"loaded (scale {p['scale']}, tablespace {p['tablespace']})")
                ctx.log(f"finished {pdb}")
            finally:
                sem.release()

        tasks: list[asyncio.Task] = []
        for pdb in targets:
            await sem.acquire()                     # wait for a free slot, like the script's jobs loop
            if ctx.cancelled:
                sem.release()
                break
            if failed and not p["keep_going"]:
                sem.release()
                ctx.log("a load failed; not starting any more PDBs (keep going is off)")
                break
            tasks.append(asyncio.create_task(load_one(pdb)))
        await asyncio.gather(*tasks)

        states = {pdb: ctx.items[pdb]["state"] for pdb in targets}
        summary["loaded"] = sum(1 for s in states.values() if s == "ok")
        summary["failed"] = sum(1 for s in states.values() if s == "failed")
        summary["not_started"] = sum(1 for s in states.values() if s in ("pending", "not_started"))
        summary["failed_pdbs"] = [{"pdb": x, "detail": ctx.items[x]["detail"]}
                                  for x in targets if states[x] == "failed"][:SHOW_FAILED]
        summary["failed_more"] = max(0, summary["failed"] - SHOW_FAILED)
        ctx.log(f"finished: {summary['loaded']} loaded, {summary['skipped']} already loaded (skipped), "
                f"{summary['failed']} failed, {summary['not_started']} not started")
        if summary["failed"] or summary["not_started"]:
            ctx.update_run(message=f"{summary['failed']} failed, {summary['not_started']} not started. "
                                   "Retry runs only those; loaded PDBs are skipped.")
            return "failed", summary
        return "ok", summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return [("oewizard", run_dir / f"{pdb}-oewizard.log"), ("SOE setup", run_dir / f"{pdb}-soe-setup.log")]


def import_done_markers(db, prefix: str, directory: str, live: dict[str, tuple[str, int]]) -> dict[str, Any]:
    """Import create_swingbench.sh '<PDB>.done' files (content: con_uid) as markers.

    Only markers whose con_uid matches the PDB that exists now are imported; others are reported."""
    rx = re.compile(rf"{re.escape(prefix)}_[0-9]{{3}}")
    imported, stale, ignored = [], [], []
    base = Path(directory)
    if not base.is_dir():
        raise PlanError(f"{directory}: not a directory")
    for f in sorted(base.glob("*.done")):
        name = f.stem.upper()
        if not rx.fullmatch(name):
            ignored.append(f.name)
            continue
        try:
            uid = f.read_text().strip()
        except OSError:
            ignored.append(f.name)
            continue
        if not uid.isdigit():
            ignored.append(f.name)
        elif name in live and live[name][0] == uid:
            db.set_soe_marker(name, uid, None, None, None)
            imported.append(name)
        else:
            stale.append(name)
    return {"imported": imported, "stale": stale, "ignored": ignored}
