"""Increase existing Swingbench SOE schemas with sbutil -dup.

One item per (PDB, schema): a single schema keeps the PDB name as the item (PDBPP_001); several
schemas give PDBPP_001:SOE, PDBPP_001:BATCH1_SOE, ...

Per item: log in as the schema and check the core tables (same check as Check connections),
then  sbutil -u <user> -p <pw> -cs //scan:port/service -soe -parallel <n> -dup <n>.

* Pre-flight (when SYSDBA is available): every PDB must exist and be open READ WRITE, otherwise
  nothing starts.
* Sliding window of `parallel` schemas at a time; after a failure no new schema starts unless
  keep_going, running ones finish.
* -dup is not idempotent: every successful run grows the schema again. Retry runs only the
  schemas that failed or did not start.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from .. import validators as v
from ..cmdbuild import display, sbutil_dup_argv
from ..db import utcnow
from ..engine.runner import ToolNotFound
from ..naming import pdb_connect_string, pdb_range, schema_user
from ..parsing import parse_check
from ..redact import MASK
from ..sqlbuild import check_sql
from .base import Operation, PlanError, SysPasswordMissing
from .check_conn import NOLOG, TIMEOUT_GRACE
from .inventory import InventoryError, query_preflight
from .load_soe import SHOW_FAILED, oewizard_reason, short_list

CHECK_TIMEOUT = 60
SB_ERROR = re.compile(r"ORA-[0-9]{5}|SQLException|Exception in thread", re.I)


def login_for(user: str) -> str:
    """Schema user as recorded (SOE / BATCH1_SOE) -> user name on the command line ('soe' / BATCH1_SOE)."""
    return "soe" if user == "SOE" else user


class IncreaseSoe(Operation):
    type = "increase"
    PARAMS = {"prefix": "prefix", "first": "pdb_number", "last": "pdb_number", "schemas": "schema_prefixes",
              "scan_host": "scan_host", "scan_port": "scan_port", "service_suffix": "service_suffix",
              "dup": "positive_int", "sbutil_parallel": "positive_int", "parallel": "positive_int",
              "keep_going": "boolean", "sbutil_path": "abs_path", "log_dir": "abs_path",
              "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    OPTIONAL = {"pdbs": "pdb_names", "targets": "schema_targets"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "load_first", "last": "load_last",
                        "scan_host": "scan_host", "scan_port": "scan_port", "service_suffix": "service_suffix",
                        "dup": "sbutil_dup", "sbutil_parallel": "sbutil_parallel", "parallel": "grow_parallel",
                        "keep_going": "keep_going", "sbutil_path": "sbutil_path", "log_dir": "log_dir",
                        "cdb_connect": "cdb_connect", "cdb_service": "cdb_service"}
    # The schema password is used in CONNECT user/"pw" (check) and as sbutil -p.
    SECRETS = {"soe_password": "quoted_password", "sys_password": "password"}

    @classmethod
    def secret_spec(cls, params: dict[str, Any]) -> dict[str, str]:
        # SYS is only needed for the pre-flight query of a remote CDB (local: / as sysdba).
        if params.get("cdb_connect") == "remote":
            return cls.SECRETS
        return {"soe_password": "quoted_password"}

    @classmethod
    def check(cls, params: dict[str, Any], final: bool = True) -> None:
        super().check(params, final)
        if "targets" in params:
            prefix = params["prefix"] + "_"
            bad = [t for t in params["targets"] if not t.startswith(prefix)]
            if bad:
                raise v.ValidationError({"targets": f"not in prefix {params['prefix']}: {', '.join(bad[:5])}"})

    @classmethod
    def retry_params(cls, params: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """Retry exactly the (PDB, schema) pairs that did not finish: item keys back to targets."""
        only = schema_user(params["schemas"][0]) if params.get("schemas") else "SOE"
        targets = [k if ":" in k else f"{k}:{only}" for k in keys]
        return {**{k: val for k, val in params.items() if k != "pdbs"}, "targets": targets}

    # ---- targets -------------------------------------------------------------------------
    def pairs(self) -> list[tuple[str, str]]:
        """(PDB, schema user) in run order: PDB by PDB, schemas in the order given."""
        p = self.params
        if p.get("targets"):
            return [tuple(t.split(":", 1)) for t in p["targets"]]  # type: ignore[misc]
        pdbs = list(p["pdbs"]) if p.get("pdbs") else pdb_range(p["prefix"], p["first"], p["last"])
        return [(pdb, schema_user(s)) for pdb in pdbs for s in p["schemas"]]

    @property
    def single_schema(self) -> bool:
        return len({s for _, s in self.pairs()}) == 1

    def key(self, pdb: str, schema: str) -> str:
        return pdb if self.single_schema else f"{pdb}:{schema}"

    def targets(self) -> list[str]:
        return [self.key(pdb, s) for pdb, s in self.pairs()]

    def pdb_list(self) -> list[str]:
        return list(dict.fromkeys(pdb for pdb, _ in self.pairs()))

    def schema_list(self) -> list[str]:
        return list(dict.fromkeys(s for _, s in self.pairs()))

    # ---- commands ------------------------------------------------------------------------
    def connect(self, pdb: str) -> str:
        p = self.params
        return pdb_connect_string(pdb, p["scan_host"], p["scan_port"], p["service_suffix"])

    def argv(self, pdb: str, schema: str, password: str) -> list[str]:
        p = self.params
        return sbutil_dup_argv(p["sbutil_path"], self.connect(pdb), login_for(schema), password,
                               p["sbutil_parallel"], p["dup"])

    def equivalent_command(self) -> str:
        pairs = self.pairs()
        pdb, schema = pairs[0]
        text = display(self.argv(pdb, schema, MASK))
        if len(pairs) > 1:
            text = (f"# {len(pairs)} schema(s) ({', '.join(self.schema_list())}) in {len(self.pdb_list())} PDB(s), "
                    f"{self.params['parallel']} at a time; the first one:\n{text}")
        return text

    # ---- plan / execute ------------------------------------------------------------------
    async def _preflight(self, runner=None) -> tuple[dict[str, tuple[str, int]] | None, str | None]:
        try:
            return await query_preflight(self.settings, self.params["prefix"], self.sysdba(), runner), None
        except SysPasswordMissing:
            return None, "PDB existence not verified: no SYS password in this browser session."
        except (InventoryError, ToolNotFound) as exc:
            raise PlanError(f"Could not check the PDBs: {exc}") from exc

    def _problems(self, live: dict[str, tuple[str, int]] | None) -> tuple[list[str], list[str]]:
        if live is None:
            return [], []
        pdbs = self.pdb_list()
        return ([x for x in pdbs if x not in live], [x for x in pdbs if x in live and live[x][1] < 1])

    async def plan(self) -> dict[str, Any]:
        live, unverified = await self._preflight()
        missing, not_open = self._problems(live)
        warnings = [unverified] if unverified else []
        if missing:
            warnings.append(f"PDBs that do not exist ({len(missing)}): {short_list(missing)}")
        if not_open:
            warnings.append(f"PDBs not open READ WRITE ({len(not_open)}): {short_list(not_open)}")
        blocked = bool(missing or not_open)
        # Schemas this tool has no record of: allowed (they may have been loaded by the script), but the
        # per-schema login check will fail them if they do not exist.
        markers = self.db.soe_markers_all(self.params["prefix"])
        unknown = [f"{pdb}:{s}" for pdb, s in self.pairs()
                   if not any(m["schema_user"] == s and (live is None or pdb not in live or m["con_uid"] == live[pdb][0])
                              for m in markers.get(pdb, []))]
        if unknown and not blocked:
            warnings.append(f"Not loaded by this tool ({len(unknown)}): {short_list(unknown)}. Each schema is "
                            "checked first; a schema that does not exist fails without running sbutil.")
        if blocked:
            warnings.append("Fix the PDBs above (or adjust the range) — nothing would be increased.")
        pairs = self.pairs()
        pdb, schema = pairs[0]
        return {
            "type": self.type, "pdbs": self.targets(), "count": len(pairs),
            "missing": missing, "not_open": not_open, "blocked": blocked,
            "schemas": self.schema_list(), "pdb_count": len(self.pdb_list()), "unknown": unknown,
            "warnings": warnings,
            "command_preview": display(self.argv(pdb, schema, MASK)),
            "equivalent_command": self.equivalent_command(),
        }

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        p = self.params
        pairs = self.pairs()
        ctx.update_run(phase="preflight")
        live, unverified = await self._preflight(ctx.runner)
        if unverified:
            ctx.log(unverified)
        missing, not_open = self._problems(live)
        summary: dict[str, Any] = {"increased": 0, "failed": 0, "not_started": 0, "failed_pdbs": []}
        if missing or not_open:
            bad = {x: "does not exist" for x in missing} | {x: "not open READ WRITE" for x in not_open}
            for pdb, s in pairs:
                if pdb in bad:
                    ctx.set_item(self.key(pdb, s), detail=bad[pdb])
            parts = []
            if missing:
                parts.append(f"PDBs that do not exist ({len(missing)}): {short_list(missing)}")
            if not_open:
                parts.append(f"PDBs not open READ WRITE ({len(not_open)}): {short_list(not_open)}")
            ctx.update_run(message="; ".join(parts) + ". Nothing was increased.")
            return "blocked", summary

        ctx.update_run(phase="increase")
        ctx.log(f"increasing {len(pairs)} schema(s) in {len(self.pdb_list())} PDB(s) with sbutil -dup {p['dup']} "
                f"-parallel {p['sbutil_parallel']}: up to {p['parallel']} at a time")
        bin_dir = os.path.dirname(p["sbutil_path"])
        sem = asyncio.Semaphore(p["parallel"])
        failed: list[str] = []
        pw = self.secrets["soe_password"]

        async def grow_one(pdb: str, schema: str) -> None:
            key = self.key(pdb, schema)
            try:
                on_start = lambda res: ctx.set_item(key, pid=res.pid, pgid=res.pgid,  # noqa: E731
                                                    proc_starttime=res.starttime)
                ctx.set_item(key, state="running", started_at=utcnow(),
                             con_uid=live[pdb][0] if live and pdb in live else None,
                             detail=f"step 1/2: check {schema} login and tables")
                r = await ctx.runner.run(NOLOG, stdin_text=check_sql(pw, self.connect(pdb), login_for(schema)),
                                         log_path=ctx.run_dir / f"{key.replace(':', '-')}-check.log",
                                         timeout=CHECK_TIMEOUT, timeout_grace=TIMEOUT_GRACE, on_start=on_start)
                if r.cancelled:
                    ctx.set_item(key, state="cancelled", ended_at=utcnow(), detail="stopped during the check")
                    return
                chk = parse_check(pdb, r.output, r.exit_code, r.timed_out, CHECK_TIMEOUT)
                ctx.set_item(key, check_status=chk.status, check_detail=chk.detail, instance=chk.instance)
                if chk.status != "OK":
                    failed.append(key)
                    ctx.set_item(key, state="failed", ended_at=utcnow(), exit_code=r.exit_code,
                                 detail=f"{schema} check failed: {chk.detail}")
                    ctx.log(f"{schema} check failed in {pdb}: {chk.detail}")
                    return
                ctx.set_item(key, detail=f"step 2/2: sbutil -dup {p['dup']} -parallel {p['sbutil_parallel']}")
                r = await ctx.runner.run(self.argv(pdb, schema, pw),
                                         log_path=ctx.run_dir / f"{key.replace(':', '-')}-sbutil.log",
                                         cwd=bin_dir, on_start=on_start)
                if r.cancelled:
                    ctx.set_item(key, state="cancelled", ended_at=utcnow(), detail="stopped during sbutil")
                    return
                err = next((ln.strip()[:200] for ln in r.output.splitlines() if SB_ERROR.search(ln)), None)
                if r.exit_code != 0 or err:
                    why = err or oewizard_reason(r.output, r.exit_code)
                    failed.append(key)
                    ctx.set_item(key, state="failed", ended_at=utcnow(), exit_code=r.exit_code,
                                 detail=f"sbutil failed: {why}")
                    ctx.log(f"sbutil failed for {schema} in {pdb}: {why}")
                    return
                ctx.set_item(key, state="ok", ended_at=utcnow(), exit_code=0,
                             detail=f"{schema} increased (sbutil -dup {p['dup']})")
                ctx.log(f"finished {schema} in {pdb}")
            finally:
                sem.release()

        tasks: list[asyncio.Task] = []
        for pdb, schema in pairs:
            await sem.acquire()                     # wait for a free slot
            if ctx.cancelled:
                sem.release()
                break
            if failed and not p["keep_going"]:
                sem.release()
                ctx.log("an increase failed; not starting any more schemas (keep going is off)")
                break
            tasks.append(asyncio.create_task(grow_one(pdb, schema)))
        await asyncio.gather(*tasks)

        keys = self.targets()
        states = {k: ctx.items[k]["state"] for k in keys}
        summary["increased"] = sum(1 for s in states.values() if s == "ok")
        summary["failed"] = sum(1 for s in states.values() if s == "failed")
        summary["not_started"] = sum(1 for s in states.values() if s in ("pending", "not_started"))
        summary["failed_pdbs"] = [{"pdb": k, "detail": ctx.items[k]["detail"]}
                                  for k in keys if states[k] == "failed"][:SHOW_FAILED]
        summary["failed_more"] = max(0, summary["failed"] - SHOW_FAILED)
        ctx.log(f"finished: {summary['increased']} increased, {summary['failed']} failed, "
                f"{summary['not_started']} not started")
        if summary["failed"] or summary["not_started"]:
            ctx.update_run(message=f"{summary['failed']} failed, {summary['not_started']} not started. "
                                   "Retry runs only those; schemas already increased are not touched again.")
            return "failed", summary
        return "ok", summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        base = pdb.replace(":", "-")
        return [("sbutil", run_dir / f"{base}-sbutil.log"), ("schema check", run_dir / f"{base}-check.log")]
