from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import validators as v
from ..ops.inventory import InventoryError, inventory
from .deps import db, settings

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
