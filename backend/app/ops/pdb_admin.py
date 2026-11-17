"""Create and drop PDBs (create_pdbs.sh). Both run strictly one PDB at a time and stop at the first failure.

Dropping is guarded twice: the request must carry confirm_text == "DELETE" and a confirm_token that
the plan computed from the exact (name, con_uid) list of PDBs to drop. The token is re-computed from
the live database when the run starts; if any PDB appeared, disappeared or was recreated in between,
the run is blocked and nothing is dropped.
"""
from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any

from .. import validators as v
from ..db import utcnow
from ..engine.runner import ToolNotFound
from ..parsing import first_error
from ..redact import MASK
from ..sqlbuild import create_pdb_sql, drop_pdb_sql
from .base import Operation, PlanError, SysPasswordMissing, needs_sys
from .inventory import InventoryError, query_preflight

SHOW_NAMES = 20
# SCAN host/port and the CDB root service: only used when cdb_connect is remote
REMOTE_CONN = {"scan_host": "scan_host", "scan_port": "scan_port", "cdb_connect": "cdb_connect",
               "cdb_service": "cdb_service"}
REMOTE_CONN_DEFAULTS = {k: k for k in REMOTE_CONN}


def drop_token(pdbs: list[str], live: dict[str, tuple[str, int]]) -> str:
    """Fingerprint of exactly which PDB incarnations would be dropped."""
    text = "\n".join(f"{p}:{live[p][0]}" for p in sorted(pdbs))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def names(pdbs: list[str]) -> str:
    return " ".join(pdbs[:SHOW_NAMES]) + (f" … and {len(pdbs) - SHOW_NAMES} more" if len(pdbs) > SHOW_NAMES else "")


async def live_state(op: Operation, runner=None) -> dict[str, tuple[str, int]]:
    try:
        return await query_preflight(op.settings, op.params["prefix"], op.sysdba(), runner)
    except (InventoryError, ToolNotFound) as exc:
        raise PlanError(f"Could not read gv$pdbs: {exc}") from exc


async def drop_one(ctx, pdb: str, rac: bool) -> bool:
    """Close (where open) and drop one PDB including datafiles. Returns True on success."""
    ctx.set_item(pdb, state="running", started_at=utcnow(), ended_at=None,
                 detail="dropping: close immediate, drop including datafiles")
    conn = ctx.op.sysdba()
    r = await ctx.runner.run(conn.argv(), stdin_text=conn.stdin(drop_pdb_sql(pdb, rac)),
                             log_path=ctx.run_dir / f"{pdb}-drop.log",
                             on_start=lambda res: ctx.set_item(pdb, pid=res.pid, pgid=res.pgid,
                                                               proc_starttime=res.starttime))
    if r.cancelled:
        ctx.set_item(pdb, state="cancelled", ended_at=utcnow(), detail="stopped while dropping", exit_code=r.exit_code)
        return False
    logon = conn.logon_failure(r.output)
    if r.exit_code != 0 or logon:
        err = logon or first_error(r.output) or f"sqlplus exited with code {r.exit_code}"
        ctx.set_item(pdb, state="failed", ended_at=utcnow(), exit_code=r.exit_code, detail=f"drop failed: {err}")
        ctx.log(f"drop failed for {pdb}: {err}")
        return False
    ctx.db.delete_soe_marker(pdb)
    ctx.log(f"dropped {pdb}")
    return True


class CreatePdbs(Operation):
    type = "create"
    PARAMS = {"prefix": "prefix", "first": "pdb_number", "last": "pdb_number", "rac_cdb": "boolean",
              "use_wallet": "boolean", "log_dir": "abs_path", **REMOTE_CONN}
    OPTIONAL = {"existing_action": "existing_action", "confirm_text": "confirm_delete", "confirm_token": "confirm_token"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "create_first", "last": "create_last", "rac_cdb": "rac_cdb",
                        "log_dir": "log_dir", **REMOTE_CONN_DEFAULTS}   # use_wallet deliberately has no default
    SECRETS = {"pdb_admin_password": "quoted_password", "wallet_password": "quoted_password",
               "sys_password": "password"}

    @classmethod
    def secret_spec(cls, params):
        spec = {"pdb_admin_password": "quoted_password"}
        if params.get("use_wallet"):
            spec["wallet_password"] = "quoted_password"
        if needs_sys(params):
            spec["sys_password"] = "password"
        return spec

    @classmethod
    def check(cls, params, final=True):
        super().check(params, final)
        if final and params.get("existing_action") == "drop":
            errs = {}
            if params.get("confirm_text") != "DELETE":
                errs["confirm_text"] = "type DELETE to confirm dropping the existing PDBs"
            if not params.get("confirm_token"):
                errs["confirm_token"] = "run the plan first; the token confirms the exact list of PDBs"
            if errs:
                raise v.ValidationError(errs)

    def equivalent_command(self) -> str:
        p = self.params
        env = [f"USE_WALLET={str(p['use_wallet']).lower()}", f"FIRST={p['first']}", f"LAST={p['last']}",
               f"PDB_ADMIN_PASSWORD='{MASK}'"]
        if p["use_wallet"]:
            env.append(f"TDE_WALLET_PASSWORD='{MASK}'")
        env.append(f"LOG_DIR={shlex.quote(p['log_dir'])}")
        cmd = " ".join(env) + " bash create_pdbs.sh"
        notes = []
        if p["prefix"] != "PDBPP":
            notes.append(f"PDB_PREFIX={p['prefix']}")
        if not p["rac_cdb"]:
            notes.append("RAC_CDB=false")
        if notes:
            cmd += f"   # edit the script: {', '.join(notes)}"
        action = p.get("existing_action")
        if action:
            cmd += f"   # answer {action[0].upper()} when asked about existing PDBs"
        return cmd

    async def plan(self) -> dict[str, Any]:
        pdbs = self.targets()
        live = await live_state(self)
        existing = [p for p in pdbs if p in live]
        p = self.params
        sql = create_pdb_sql(pdbs[0], MASK, p["rac_cdb"], MASK if p["use_wallet"] else None)
        warnings = []
        if existing:
            warnings.append(f"{len(existing)} PDB(s) of the range already exist: choose drop and recreate, "
                            "skip, or abort")
        return {
            "type": self.type, "pdbs": pdbs, "count": len(pdbs),
            "existing": [{"name": e, "con_uid": live[e][0], "rw_instances": live[e][1]} for e in existing],
            "to_create": len(pdbs) - (len(existing) if p.get("existing_action") == "skip" else 0),
            "drop_token": drop_token(existing, live) if existing else None,
            "warnings": warnings,
            "sql_preview": sql,
            "equivalent_command": self.equivalent_command(),
        }

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        p = self.params
        pdbs = self.targets()
        ctx.update_run(phase="preflight")
        live = await live_state(self, ctx.runner)
        existing = [x for x in pdbs if x in live]
        action = p.get("existing_action")
        summary: dict[str, Any] = {"existing": len(existing), "dropped": 0, "created": 0, "skipped": 0}

        if existing and action in (None, "abort"):
            msg = (f"Aborted without changes: {len(existing)} PDB(s) already exist ({names(existing)})."
                   if action == "abort" else
                   f"{len(existing)} PDB(s) already exist ({names(existing)}). Choose drop and recreate, skip, "
                   "or abort, and start again.")
            ctx.update_run(message=msg)
            return "blocked", summary
        if existing and action == "drop" and drop_token(existing, live) != p.get("confirm_token"):
            ctx.update_run(message="The PDBs to drop changed since the plan you confirmed (a PDB appeared, "
                                   "disappeared or was recreated). Nothing was dropped; run the plan again.")
            return "blocked", summary

        skip = set(existing) if action == "skip" else set()
        for pdb in skip:
            ctx.set_item(pdb, state="skipped", con_uid=live[pdb][0], detail="already exists (skipped)")

        if existing and action == "drop":
            ctx.update_run(phase="drop")
            ctx.db.audit(ctx.manager_actor, "drop_confirmed", ctx.run_id,
                         {"pdbs": [f"{e}:{live[e][0]}" for e in existing]})
            ctx.log(f"dropping {len(existing)} existing PDB(s) including datafiles: {names(existing)}")
            for pdb in existing:
                if ctx.cancelled:
                    break
                if not await drop_one(ctx, pdb, p["rac_cdb"]):
                    if not ctx.cancelled:
                        ctx.update_run(message=f"Drop failed for {pdb}; nothing was created. "
                                               f"See {pdb}-drop.log.")
                    return "failed", summary
                summary["dropped"] += 1
                ctx.set_item(pdb, state="pending", detail="dropped; waiting to be created")

        ctx.update_run(phase="create")
        conn = ctx.op.sysdba()
        wallet = ctx.op.secrets.get("wallet_password") if p["use_wallet"] else None
        total = len(pdbs) - len(skip)
        ctx.log(f"creating {total} PDB(s) one at a time ({'RAC' if p['rac_cdb'] else 'single instance'}, "
                f"TDE master key: {'yes' if p['use_wallet'] else 'no'})")
        for i, pdb in enumerate(pdbs, start=1):
            if pdb in skip:
                continue
            if ctx.cancelled:
                break
            ctx.set_item(pdb, state="running", started_at=utcnow(), ended_at=None, detail="creating and opening")
            r = await ctx.runner.run(
                conn.argv(), stdin_text=conn.stdin(
                    create_pdb_sql(pdb, ctx.op.secrets["pdb_admin_password"], p["rac_cdb"], wallet)),
                log_path=ctx.run_dir / f"{pdb}-create.log",
                on_start=lambda res, pdb=pdb: ctx.set_item(pdb, pid=res.pid, pgid=res.pgid,
                                                           proc_starttime=res.starttime))
            if r.cancelled:
                ctx.set_item(pdb, state="cancelled", ended_at=utcnow(), detail="stopped while creating",
                             exit_code=r.exit_code)
                break
            logon = conn.logon_failure(r.output)
            if r.exit_code != 0 or logon:
                err = logon or first_error(r.output) or f"sqlplus exited with code {r.exit_code}"
                ctx.set_item(pdb, state="failed", ended_at=utcnow(), exit_code=r.exit_code, detail=err)
                ctx.update_run(message=f"PDB setup failed for {pdb}: {err}. Stopped; see {pdb}-create.log.")
                ctx.log(f"create failed for {pdb}: {err}")
                return "failed", summary
            done = "created, opened on all instances" if p["rac_cdb"] else "created, opened, state saved"
            replaced = pdb in existing and action == "drop"
            ctx.set_item(pdb, state="ok", ended_at=utcnow(), exit_code=0,
                         detail=("dropped and recreated: " if replaced else "") + done +
                         (", TDE master key set" if wallet is not None else ""))
            summary["created"] += 1
            ctx.log(f"[PDB {pdb[-3:]}; range {p['first']}-{p['last']}] finished {pdb}")
        summary["skipped"] = len(skip)
        status = "ok" if summary["created"] == total and not ctx.cancelled else "failed"
        return status, summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return [("create", run_dir / f"{pdb}-create.log"), ("drop", run_dir / f"{pdb}-drop.log")]


class DropPdbs(Operation):
    type = "drop"
    PARAMS = {"prefix": "prefix", "first": "pdb_number", "last": "pdb_number", "rac_cdb": "boolean",
              "log_dir": "abs_path", **REMOTE_CONN}
    OPTIONAL = {"pdbs": "pdb_names", "confirm_text": "confirm_delete", "confirm_token": "confirm_token"}
    PROFILE_DEFAULTS = {"prefix": "prefix", "first": "create_first", "last": "create_last", "rac_cdb": "rac_cdb",
                        "log_dir": "log_dir", **REMOTE_CONN_DEFAULTS}
    SECRETS = {"sys_password": "password"}

    @classmethod
    def secret_spec(cls, params):
        return {"sys_password": "password"} if needs_sys(params) else {}

    @classmethod
    def check(cls, params, final=True):
        super().check(params, final)
        if final:
            errs = {}
            if params.get("confirm_text") != "DELETE":
                errs["confirm_text"] = "type DELETE to confirm"
            if not params.get("confirm_token"):
                errs["confirm_token"] = "run the plan first; the token confirms the exact list of PDBs"
            if errs:
                raise v.ValidationError(errs)

    async def plan(self) -> dict[str, Any]:
        pdbs = self.targets()
        live = await live_state(self)
        existing = [p for p in pdbs if p in live]
        return {
            "type": self.type, "pdbs": pdbs, "count": len(pdbs),
            "existing": [{"name": e, "con_uid": live[e][0], "rw_instances": live[e][1]} for e in existing],
            "missing": [p for p in pdbs if p not in live],
            "drop_token": drop_token(existing, live) if existing else None,
            "warnings": ([] if existing else ["None of these PDBs exist; nothing to drop."]),
            "sql_preview": drop_pdb_sql(existing[0], self.params["rac_cdb"]) if existing else "",
            "equivalent_command": "(no script equivalent: create_pdbs.sh drops existing PDBs only before recreating "
                                  "them, answer D)",
        }

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        p = self.params
        pdbs = self.targets()
        ctx.update_run(phase="preflight")
        live = await live_state(self, ctx.runner)
        existing = [x for x in pdbs if x in live]
        summary: dict[str, Any] = {"existing": len(existing), "dropped": 0}
        if drop_token(existing, live) != p.get("confirm_token"):
            ctx.update_run(message="The PDBs to drop changed since the plan you confirmed. Nothing was dropped; "
                                   "run the plan again.")
            return "blocked", summary
        for pdb in pdbs:
            if pdb not in live:
                ctx.set_item(pdb, state="skipped", detail="does not exist")
        ctx.update_run(phase="drop")
        ctx.db.audit(ctx.manager_actor, "drop_confirmed", ctx.run_id,
                     {"pdbs": [f"{e}:{live[e][0]}" for e in existing]})
        ctx.log(f"dropping {len(existing)} PDB(s) including datafiles, one at a time: {names(existing)}")
        for pdb in existing:
            if ctx.cancelled:
                break
            if not await drop_one(ctx, pdb, p["rac_cdb"]):
                if not ctx.cancelled:
                    ctx.update_run(message=f"Drop failed for {pdb}; stopped. See {pdb}-drop.log.")
                return "failed", summary
            ctx.set_item(pdb, state="ok", ended_at=utcnow(), exit_code=0, con_uid=live[pdb][0],
                         detail="dropped including datafiles")
            summary["dropped"] += 1
        return ("ok" if summary["dropped"] == len(existing) and not ctx.cancelled else "failed"), summary

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return [("drop", run_dir / f"{pdb}-drop.log")]
