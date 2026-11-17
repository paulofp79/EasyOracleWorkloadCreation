"""Login for network access (PROJECT_CONTEXT §10).

* Accounts are created on the server: `python -m app set-password [--user NAME]` (no first-visitor setup).
* Passwords are stored as scrypt hashes (stdlib hashlib, per-user random salt).
* A login creates a server-side session (memory only, so a restart logs everyone out) referenced by an
  HttpOnly SameSite=Strict cookie, plus a CSRF token that every state-changing request must echo in the
  X-CSRF-Token header (double-submit: the token also sits in a cookie the page can read).
* Five failed logins from one address lock that address out for a while.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field

AUTH_COOKIE = "orch_auth"
CSRF_COOKIE = "orch_csrf"
CSRF_HEADER = "x-csrf-token"
IDLE_S = 8 * 3600
ABSOLUTE_S = 24 * 3600
MAX_FAILURES = 5
LOCKOUT_S = 60
RE_USER = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,31}")
MIN_PASSWORD = 10

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Spend the same time as a real check so unknown users are not distinguishable by timing.
        hashlib.scrypt(password.encode(), salt=b"0" * 16, n=_N, r=_R, p=_P, dklen=32)
        return False
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


def check_username(name: str) -> str:
    if not RE_USER.fullmatch(name or ""):
        raise ValueError("user names start with a letter and use letters, digits, '.', '_' or '-' (max 32)")
    return name


def check_new_password(pw: str) -> str:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"use at least {MIN_PASSWORD} characters")
    if any(ord(c) < 32 for c in pw):
        raise ValueError("control characters are not allowed")
    return pw


@dataclass
class AuthSession:
    user: str
    csrf: str
    created: float
    last_seen: float


@dataclass
class AuthStore:
    clock: object = time.time
    sessions: dict[str, AuthSession] = field(default_factory=dict)
    failures: dict[str, list[float]] = field(default_factory=dict)

    def _now(self) -> float:
        return self.clock()

    def create(self, user: str) -> tuple[str, AuthSession]:
        token = secrets.token_urlsafe(32)
        now = self._now()
        s = AuthSession(user, secrets.token_urlsafe(24), now, now)
        self.sessions[token] = s
        return token, s

    def get(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        s = self.sessions.get(token)
        now = self._now()
        if s is None or now - s.last_seen > IDLE_S or now - s.created > ABSOLUTE_S:
            self.sessions.pop(token, None)
            return None
        s.last_seen = now
        return s

    def drop(self, token: str | None) -> None:
        self.sessions.pop(token or "", None)

    # ---- brute-force protection --------------------------------------------------------------
    def locked_for(self, client: str) -> float:
        now = self._now()
        recent = [t for t in self.failures.get(client, []) if now - t < LOCKOUT_S]
        self.failures[client] = recent
        if len(recent) >= MAX_FAILURES:
            return LOCKOUT_S - (now - recent[0])
        return 0.0

    def failed(self, client: str) -> None:
        self.failures.setdefault(client, []).append(self._now())

    def succeeded(self, client: str) -> None:
        self.failures.pop(client, None)
