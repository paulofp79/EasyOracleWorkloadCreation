"""PDB inventory: live gv$pdbs (via sqlplus / as sysdba) merged with what this tool remembers per PDB."""
from __future__ import annotations

import re
from typing import Any

from ..config import Settings
from ..db import Database, utcnow
from ..engine.runner import ProcessRunner, ToolNotFound
from ..parsing import PdbRow, first_error, parse_inventory, parse_preflight
from ..sqlbuild import PDB_TOKEN, inventory_sql, preflight_sql

SYSDBA = ["sqlplus", "-L", "-s", "/", "as", "sysdba"]
QUERY_TIMEOUT = 120
FULL_OUTPUT = 1_000_000      # keep every line: 999 PDBs x many instances must not be cut to the tail


class InventoryError(Exception):
    pass


def numbered(prefix: str) -> re.Pattern[str]:
    """Exact PDB names of this tool: PREFIX_NNN (LIKE 'PREFIX%' also matches PREFIXX_001 or PREFIX_ABC)."""
    return re.compile(rf"{re.escape(prefix.upper())}_[0-9]{{3}}")


async def query_inventory(settings: Settings, prefix: str) -> dict[str, PdbRow]:
    runner = ProcessRunner(settings.tool_env())
    try:
        r = await runner.run(SYSDBA, stdin_text=inventory_sql(prefix), timeout=QUERY_TIMEOUT,
                             tail_lines=FULL_OUTPUT)
    except ToolNotFound as exc:
        raise InventoryError(str(exc)) from exc
    if r.timed_out:
        raise InventoryError(f"inventory query timed out after {QUERY_TIMEOUT}s")
    if r.exit_code != 0 and not any(ln.startswith(PDB_TOKEN) for ln in r.tail):
        raise InventoryError(first_error(r.output) or f"sqlplus exited with code {r.exit_code}")
    rx = numbered(prefix)
    return {k: v for k, v in parse_inventory(r.output).items() if rx.fullmatch(k)}


async def query_preflight(settings: Settings, prefix: str,
                          runner: ProcessRunner | None = None) -> dict[str, tuple[str, int]]:
    """The scripts' pre-flight query: {name: (con_uid, instances open READ WRITE)}."""
    runner = runner or ProcessRunner(settings.tool_env())
    r = await runner.run(SYSDBA, stdin_text=preflight_sql(prefix), timeout=QUERY_TIMEOUT,
                         tail_lines=FULL_OUTPUT)
    if r.timed_out:
        raise InventoryError(f"pre-flight query timed out after {QUERY_TIMEOUT}s")
    if r.exit_code != 0:
        raise InventoryError(first_error(r.output) or f"sqlplus exited with code {r.exit_code}")
    rx = numbered(prefix)
    return {k: v for k, v in parse_preflight(r.output).items() if rx.fullmatch(k)}


async def inventory(settings: Settings, db: Database, prefix: str) -> dict[str, Any]:
    live = await query_inventory(settings, prefix)
    checks = db.last_checks(prefix)
    markers = db.soe_markers(prefix)
    instances: dict[int, str] = {}
    pdbs = []
    for name in sorted(live):
        row = live[name]
        for inst in row.instances:
            instances[inst["inst_id"]] = inst["instance"]
        marker = markers.get(name)
        pdbs.append({
            "name": name,
            "number": int(name[-3:]),
            "con_uid": row.con_uid,
            "rw_instances": row.rw_instances,
            "n_instances": len(row.instances),
            "restricted": row.restricted,
            "instances": row.instances,
            # A marker for an older incarnation (different con_uid) does not count (gotcha 7).
            "soe_loaded": marker if marker and marker["con_uid"] == row.con_uid else None,
            "soe_marker_stale": bool(marker and marker["con_uid"] != row.con_uid),
            "last_check": checks.get(name),
        })
    return {"prefix": prefix, "fetched_at": utcnow(),
            "instances": [{"inst_id": k, "instance": v} for k, v in sorted(instances.items())],
            "pdbs": pdbs}
