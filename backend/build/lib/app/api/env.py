from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from ..ops.envcheck import run_env_check
from .deps import db, settings

router = APIRouter(prefix="/api", tags=["environment"])


@router.get("/health")
def health(request: Request):
    s = settings(request)
    return {"status": "ok", "version": __version__, "mode": s.mode}


@router.get("/env-check")
async def env_check(request: Request, profile_id: int | None = None):
    d = db(request)
    profile = d.get_profile(profile_id) if profile_id is not None else d.active_profile()
    if profile is None:
        raise HTTPException(404, "profile not found")
    result = await run_env_check(settings(request), profile["params"])
    result["profile"] = {"id": profile["id"], "name": profile["name"]}
    return result
