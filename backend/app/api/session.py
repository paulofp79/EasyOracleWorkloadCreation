"""SYS password for this browser session, kept in server memory only (remote CDB mode)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import validators as v
from ..ops.envcheck import run_env_check
from ..sessions import COOKIE, DEFAULT_HOURS, MAX_HOURS, SessionStore
from ..sysdba import SysdbaConn
from .deps import actor, db, settings

router = APIRouter(prefix="/api/session", tags=["session"])


def store(request: Request) -> SessionStore:
    return request.app.state.sessions


def session_secrets(request: Request) -> dict[str, str]:
    pw = store(request).sys_password(request.cookies.get(COOKIE))
    return {"sys_password": pw} if pw else {}


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _state(request: Request, sid: str | None) -> dict:
    profile = db(request).active_profile()
    params = v.profile_params(profile)
    return {"sys_password_set": store(request).sys_password(sid) is not None,
            "expires_at": _iso(store(request).expires(sid)),
            "cdb_connect": params["cdb_connect"],
            "connection": SysdbaConn.from_params(params, None).describe()}


@router.get("")
def get_session(request: Request):
    return _state(request, request.cookies.get(COOKIE))


class SysIn(BaseModel):
    sys_password: str
    hours: float = DEFAULT_HOURS


@router.put("/sys-password")
async def set_sys_password(body: SysIn, request: Request, response: Response):
    pw = v.validate({"sys_password": body.sys_password}, {"sys_password": "password"})["sys_password"]
    if not 0 < body.hours <= MAX_HOURS:
        raise v.ValidationError({"hours": f"must be between 0 and {MAX_HOURS}"})
    params = v.profile_params(db(request).active_profile())
    if params["cdb_connect"] == "remote":
        # Verify before keeping it, so a typo is reported now and not in the middle of a run.
        res = await run_env_check(settings(request), params, pw)
        chk = next(c for c in res["checks"] if c["id"] == "sysdba")
        if chk["status"] == "fail":
            return JSONResponse({"error": "SYSDBA logon failed", "errors": {"sys_password": chk["error"]}},
                                status_code=422)
    sid = request.cookies.get(COOKIE) or SessionStore.new_id()
    expires = store(request).set_sys_password(sid, pw, body.hours)
    db(request).audit(actor(request), "sys_password_set", params={"hours": body.hours})
    out = JSONResponse({**_state(request, sid), "expires_at": _iso(expires), "sys_password_set": True})
    out.set_cookie(COOKIE, sid, max_age=int(body.hours * 3600), httponly=True, samesite="strict", path="/api")
    return out


@router.delete("/sys-password")
def forget(request: Request):
    store(request).clear(request.cookies.get(COOKIE))
    return _state(request, request.cookies.get(COOKIE))
