from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import validators as v
from ..ops.base import PlanError
from ..ops.inventory import InventoryError, inventory, query_preflight
from ..ops.load_soe import import_done_markers
from .deps import actor, db, settings

router = APIRouter(prefix="/api/pdbs", tags=["pdbs"])


@router.get("")
async def list_pdbs(request: Request, prefix: str | None = None):
    if prefix is None:
        profile = db(request).active_profile()
        if profile is None:
            raise HTTPException(404, "no active profile")
        prefix = profile["params"]["prefix"]
    clean = v.validate({"prefix": prefix}, {"prefix": "prefix"})["prefix"]
    try:
        return await inventory(settings(request), db(request), clean)
    except InventoryError as exc:
        raise HTTPException(502, f"Could not read gv$pdbs: {exc}") from exc


class ImportIn(BaseModel):
    directory: str
    prefix: str | None = None


@router.post("/import-markers")
async def import_markers(body: ImportIn, request: Request):
    """Adopt '<PDB>.done' files written by create_swingbench.sh (only where con_uid still matches)."""
    d = db(request)
    prefix = body.prefix or (d.active_profile() or {"params": {"prefix": "PDBPP"}})["params"]["prefix"]
    clean = v.validate({"prefix": prefix, "directory": body.directory}, {"prefix": "prefix", "directory": "abs_path"})
    try:
        live = await query_preflight(settings(request), clean["prefix"])
        result = import_done_markers(d, clean["prefix"], clean["directory"], live)
    except (InventoryError, PlanError) as exc:
        raise HTTPException(502 if isinstance(exc, InventoryError) else 422, str(exc)) from exc
    d.audit(actor(request), "import_markers", params={"directory": clean["directory"], "imported": len(result["imported"])})
    return result
