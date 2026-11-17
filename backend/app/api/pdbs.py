from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import validators as v
from ..ops.base import PlanError
from ..ops.inventory import InventoryError, inventory, query_preflight
from ..ops.load_soe import import_done_markers
from ..sysdba import SysdbaConn, SysPasswordMissing
from .session import session_secrets
from .deps import actor, db, settings

router = APIRouter(prefix="/api/pdbs", tags=["pdbs"])


def _conn(request: Request) -> tuple[dict, SysdbaConn]:
    params = v.profile_params(db(request).active_profile())
    return params, SysdbaConn.from_params(params, session_secrets(request).get("sys_password"))


def need_sys(exc: SysPasswordMissing) -> JSONResponse:
    return JSONResponse({"error": str(exc), "need_sys_password": True}, status_code=428)


@router.get("")
async def list_pdbs(request: Request, prefix: str | None = None):
    params, conn = _conn(request)
    clean = v.validate({"prefix": prefix or params["prefix"]}, {"prefix": "prefix"})["prefix"]
    try:
        return await inventory(settings(request), db(request), clean, conn.require())
    except SysPasswordMissing as exc:
        return need_sys(exc)
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
        live = await query_preflight(settings(request), clean["prefix"], _conn(request)[1].require())
        result = import_done_markers(d, clean["prefix"], clean["directory"], live)
    except SysPasswordMissing as exc:
        return need_sys(exc)
    except (InventoryError, PlanError) as exc:
        raise HTTPException(502 if isinstance(exc, InventoryError) else 422, str(exc)) from exc
    d.audit(actor(request), "import_markers", params={"directory": clean["directory"], "imported": len(result["imported"])})
    return result
