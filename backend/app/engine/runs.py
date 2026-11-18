"""Run lifecycle: concurrent runs with per-PDB conflict rules, item state, events, cancel and recovery.

Item changes are kept in memory and flushed every FLUSH_S seconds: one SQLite transaction
and one `items.patch` event per flush, so 250 near-simultaneous completions stay cheap.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import secrets as pysecrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import Database, utcnow
from ..ops.base import PlanError
from ..sysdba import SysPasswordMissing
from ..redact import Redactor
from .events import EventBus
from .leftovers import alive_items, proc_table
from .runner import ProcessRunner

log = logging.getLogger("orchestrator.runs")

FLUSH_S = 0.25
HEARTBEAT_S = 5.0
KEEP_BUSES = 30
FINAL_STATES = {"ok", "failed", "skipped", "cancelled", "not_started"}


class Conflict(Exception):
    def __init__(self, message: str, run_id: str):
        super().__init__(message)
        self.run_id = run_id


# Which runs may touch the same PDB at the same time. A claim is (type, schema user(s)):
#  * create / drop need the PDB to themselves;
#  * a load or an increase of schema S excludes another load / increase of S and a workload on S
#    in that PDB;
#  * everything else (checks, several workloads, loads of different schemas) may overlap.
EXCLUSIVE = {"create", "drop"}
WRITERS = {"load": "loaded", "increase": "increased"}
DOING = {"load": "loading", "increase": "increasing", "workload": "a workload on"}


def claim(op) -> tuple[str, frozenset[str]]:
    from ..naming import schema_user
    if hasattr(op, "schema_list"):                 # Increase SOE: one or several schemas
        return op.type, frozenset(op.schema_list())
    return op.type, frozenset({schema_user(op.params.get("schema_prefix", "") or "")})


def clash(a: tuple[str, Any], b: tuple[str, Any]) -> str | None:
    """Why runs with claims a and b cannot share a PDB, or None. Schemas: one name or a set of names."""
    (ta, sa), (tb, sb) = a, b
    if ta in EXCLUSIVE or tb in EXCLUSIVE:
        return "creating or dropping a PDB needs it to itself"
    sa, sb = ({s} if isinstance(s, str) else set(s) for s in (sa, sb))
    common = ", ".join(sorted(sa & sb))
    if not common or not (ta in WRITERS or tb in WRITERS) or not {ta, tb} <= {*WRITERS, "workload"}:
        return None
    if ta == tb:
        return f"{common} is being {WRITERS[ta]} there"
    first, second = (ta, tb) if ta in WRITERS else (tb, ta)
    return f"{DOING[first]} {common} and {DOING[second]} {common} cannot overlap"


def new_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{pysecrets.token_hex(3)}"


def make_run_dir(log_dir: str, run_id: str) -> Path:
    base = Path(log_dir)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir = base / f"run-{run_id}"
    run_dir.mkdir(mode=0o700)
    os.chmod(run_dir, 0o700)
    latest = base / "latest"
    tmp = base / f".latest-{run_id}"
    try:
        tmp.symlink_to(run_dir.name)
        os.replace(tmp, latest)
    except OSError:
        tmp.unlink(missing_ok=True)
    return run_dir


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


class RunContext:
    def __init__(self, manager: "RunManager", run: dict[str, Any], pdbs: list[str], op, actor: str = "local"):
        self.manager = manager
        self.manager_actor = actor
        self.db: Database = manager.db
        self.settings: Settings = manager.settings
        self.run_id: str = run["id"]
        self.type: str = run["type"]
        self.params: dict[str, Any] = run["params"]
        self.run_dir = Path(run["run_dir"])
        self.op = op
        self.redactor = Redactor(op.secret_values())
        self.runner = ProcessRunner(self.settings.tool_env(), self.redactor)
        self.bus = EventBus(self.run_id)
        self.items: dict[str, dict[str, Any]] = {
            p: {"pdb": p, "ord": i, "state": "pending", **{k: None for k in Database.ITEM_FIELDS if k != "state"}}
            for i, p in enumerate(pdbs)}
        self._dirty: set[str] = set()
        self.cancelled = False
        self.started_mono = time.monotonic()
        self.peak_running = 0

    # ---- items ---------------------------------------------------------------------------
    def set_item(self, pdb: str, **fields: Any) -> None:
        item = self.items[pdb]
        if "detail" in fields and isinstance(fields["detail"], str):
            fields["detail"] = self.redactor.text(fields["detail"])
        if "check_detail" in fields and isinstance(fields["check_detail"], str):
            fields["check_detail"] = self.redactor.text(fields["check_detail"])
        item.update(fields)
        self._dirty.add(pdb)
        running = sum(1 for it in self.items.values() if it["state"] in ("checking", "running"))
        self.peak_running = max(self.peak_running, running)

    def counts(self) -> dict[str, int]:
        return dict(collections.Counter(it["state"] for it in self.items.values()))

    def flush(self) -> None:
        if not self._dirty:
            return
        changed = [dict(self.items[p]) for p in sorted(self._dirty, key=lambda p: self.items[p]["ord"])]
        self._dirty.clear()
        self.db.update_items(self.run_id, changed)
        self.bus.publish("items.patch", {"items": changed, "counts": self.counts()})

    # ---- run -----------------------------------------------------------------------------
    def update_run(self, **fields: Any) -> None:
        if "message" in fields and isinstance(fields["message"], str):
            fields["message"] = self.redactor.text(fields["message"])
        self.db.update_run(self.run_id, **fields)
        self.bus.publish("run.updated", self.manager.run_view(self.run_id))

    def elapsed(self) -> float:
        return time.monotonic() - self.started_mono

    def log(self, message: str) -> None:
        """Run-level log line (run.log in the run directory and a `log.run` event)."""
        line = f"{datetime.now().strftime('%H:%M:%S')} {self.redactor.text(message)}"
        fd = os.open(self.run_dir / "run.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(line + "\n")
        self.bus.publish("log.run", {"line": line})

    def write_artifact(self, name: str, text: str) -> None:
        write_private(self.run_dir / name, self.redactor.text(text))


class RunManager:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.active: dict[str, RunContext] = {}          # run_id -> context, in start order
        self.tasks: dict[str, asyncio.Task] = {}
        self.buses: collections.OrderedDict[str, EventBus] = collections.OrderedDict()
        self.contexts: dict[str, RunContext] = {}
        self._lock = asyncio.Lock()

    def recover(self) -> list[str]:
        """Runs left 'running' by a previous backend process become 'interrupted'. Processes that are
        verifiably still alive keep state 'running' (Kill leftovers stops them); logs stay untouched."""
        ids = self.db.running_run_ids()
        table = proc_table()
        for rid in ids:
            items = [it for it in self.db.get_items(rid) if it["state"] in ("checking", "running")]
            alive = alive_items(items, table)
            self.db.mark_interrupted(rid, {pdb: info["pid"] for pdb, info in alive.items()})
            log.warning("run %s was running when the backend stopped; marked interrupted (%d process group(s) "
                        "still alive)", rid, len(alive))
        return ids

    def run_view(self, run_id: str) -> dict[str, Any] | None:
        run = self.db.get_run(run_id)
        if run is None:
            return None
        ctx = self.contexts.get(run_id)
        run["counts"] = ctx.counts() if ctx else self.db.item_counts(run_id)
        run["total"] = sum(run["counts"].values())
        bus = self.buses.get(run_id)
        run["event_seq"] = bus.seq if bus else None
        run["live"] = run_id in self.active
        return run

    async def start(self, op, actor: str, retry_of: str | None = None) -> str:
        async with self._lock:
            pdbs = op.targets()
            self._check_conflicts(op, pdbs)
            run_id = new_run_id()
            run_dir = make_run_dir(op.params["log_dir"], run_id)
            now = utcnow()
            run = {"id": run_id, "type": op.type, "status": "running", "phase": None, "profile_id": op.profile_id,
                   "params": op.params, "run_dir": str(run_dir), "retry_of": retry_of, "created_at": now,
                   "started_at": now}
            self.db.insert_run(run, pdbs)
            ctx = RunContext(self, run, pdbs, op, actor)
            self.active[run_id] = ctx
            self.contexts[run_id] = ctx
            self.buses[run_id] = ctx.bus
            while len(self.buses) > KEEP_BUSES:
                old = next((k for k in self.buses if k not in self.active), None)
                if old is None:
                    break
                del self.buses[old]
                self.contexts.pop(old, None)
            self.db.audit(actor, "start", run_id, {"type": op.type, **op.params})
            self.tasks[run_id] = asyncio.create_task(self._run(ctx))
            return run_id

    def _check_conflicts(self, op, pdbs: list[str]) -> None:
        if len(self.active) >= self.settings.max_runs:
            first = next(iter(self.active.values()))
            raise Conflict(f"{len(self.active)} runs are already in progress (the limit is "
                           f"{self.settings.max_runs}, ORCH_MAX_RUNS); wait for one to finish", first.run_id)
        mine, wanted = claim(op), {p.split(":", 1)[0] for p in pdbs}   # items may be PDB:SCHEMA
        for ctx in self.active.values():
            why = clash(mine, claim(ctx.op))
            if why is None:
                continue
            busy = sorted(wanted & {k.split(":", 1)[0] for k in ctx.items})
            if busy:
                names = ", ".join(busy[:5]) + (f" and {len(busy) - 5} more" if len(busy) > 5 else "")
                raise Conflict(f"run {ctx.run_id} ({ctx.type}) is still in progress on {names}: {why}. "
                               f"Wait for it, stop it, or choose other PDBs.", ctx.run_id)

    async def _run(self, ctx: RunContext) -> None:
        flusher = asyncio.create_task(self._flush_loop(ctx))
        beat = asyncio.create_task(self._heartbeat_loop(ctx))
        status, summary, message = "failed", {}, None
        try:
            ctx.log(f"{ctx.type} run {ctx.run_id} started: {len(ctx.items)} PDB(s)")
            status, summary = await ctx.op.execute(ctx)
        except asyncio.CancelledError:
            status, message = "cancelled", "The run task was cancelled."
        except (PlanError, SysPasswordMissing) as exc:   # e.g. gv$pdbs unreadable: show the reason
            status, message = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            ref = uuid.uuid4().hex[:8]
            log.exception("run %s failed (ref %s)", ctx.run_id, ref)
            status, message = "failed", f"Internal error ({type(exc).__name__}, ref {ref}); see the server log."
            await ctx.runner.kill_all()
        finally:
            flusher.cancel()
            beat.cancel()
            for pdb, it in ctx.items.items():
                if it["state"] == "pending":
                    ctx.set_item(pdb, state="not_started")
                elif it["state"] in ("checking", "running"):
                    ctx.set_item(pdb, state="cancelled", ended_at=utcnow())
            if ctx.cancelled:
                status = "cancelled"
                message = message or "Stopped by the user."
            ctx.flush()
            summary = {**summary, "counts": ctx.counts(), "elapsed_s": round(ctx.elapsed(), 1),
                       "peak_running": ctx.peak_running}
            fields: dict[str, Any] = {"status": status, "ended_at": utcnow(), "summary": summary}
            if message:
                fields["message"] = message
            ctx.update_run(**fields)
            ctx.log(f"run finished: {status} {summary.get('counts')}")
            ctx.bus.close()
            self.active.pop(ctx.run_id, None)
            self.tasks.pop(ctx.run_id, None)
            ctx.op.secrets = {}          # drop secrets with the run

    async def _flush_loop(self, ctx: RunContext) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            try:
                ctx.flush()
            except Exception:  # noqa: BLE001
                log.exception("flush failed for run %s", ctx.run_id)

    async def _heartbeat_loop(self, ctx: RunContext) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            ctx.bus.publish("heartbeat", {"elapsed_s": round(ctx.elapsed(), 1), "counts": ctx.counts()})

    async def cancel(self, run_id: str, actor: str) -> bool:
        ctx = self.active.get(run_id)
        if ctx is None:
            return False
        if not ctx.cancelled:
            ctx.cancelled = True
            self.db.audit(actor, "cancel", run_id)
            ctx.log("stop requested: TERM to every process group, KILL after 2 s")
            await ctx.runner.kill_all()
        return True

    async def shutdown(self) -> None:
        contexts, tasks = list(self.active.values()), list(self.tasks.values())
        for ctx in contexts:
            ctx.cancelled = True
        await asyncio.gather(*(ctx.runner.kill_all() for ctx in contexts), return_exceptions=True)
        if tasks:
            _, pending = await asyncio.wait([asyncio.shield(t) for t in tasks], timeout=10)
            if pending:
                for t in tasks:
                    t.cancel()
