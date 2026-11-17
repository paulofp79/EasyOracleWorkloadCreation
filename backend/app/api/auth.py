from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import AUTH_COOKIE, CSRF_COOKIE, IDLE_S, AuthStore, verify_password
from .deps import db, settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def store(request: Request) -> AuthStore:
    return request.app.state.auth


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/state")
def state(request: Request):
    s = store(request).get(request.cookies.get(AUTH_COOKIE))
    return {"auth_enabled": settings(request).auth, "user": s.user if s else None,
            "tls": settings(request).tls}


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request):
    st, ip = store(request), client_ip(request)
    wait = st.locked_for(ip)
    if wait > 0:
        return JSONResponse({"error": f"Too many failed logins; try again in {int(wait) + 1} s."}, status_code=429)
    if not verify_password(body.password, db(request).user_hash(body.username)):
        st.failed(ip)
        db(request).audit(f"{body.username[:32]}@{ip}", "login_failed")
        return JSONResponse({"error": "Wrong user name or password."}, status_code=401)
    st.succeeded(ip)
    token, sess = st.create(body.username)
    db(request).audit(body.username, "login", params={"ip": ip})
    secure = settings(request).secure_cookies
    out = JSONResponse({"user": sess.user, "csrf": sess.csrf})
    out.set_cookie(AUTH_COOKIE, token, max_age=IDLE_S, httponly=True, samesite="strict", secure=secure, path="/")
    # readable by the page on purpose: it sends the value back in X-CSRF-Token (double submit)
    out.set_cookie(CSRF_COOKIE, sess.csrf, max_age=IDLE_S, httponly=False, samesite="strict", secure=secure,
                   path="/")
    return out


@router.post("/logout")
def logout(request: Request):
    s = store(request).get(request.cookies.get(AUTH_COOKIE))
    store(request).drop(request.cookies.get(AUTH_COOKIE))
    if s:
        db(request).audit(s.user, "logout")
    out = JSONResponse({"user": None})
    out.delete_cookie(AUTH_COOKIE, path="/")
    out.delete_cookie(CSRF_COOKIE, path="/")
    return out
