"""Per-browser secrets kept in server memory only (PROJECT_CONTEXT §7: 'remember for this browser
session only if kept in memory server-side with an expiry').

The browser holds a random session id in an HttpOnly, SameSite=Strict cookie; the SYS password stays in
this process and is lost on restart or after the expiry. Nothing is written to disk or the database.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

COOKIE = "orch_sid"
DEFAULT_HOURS = 8
MAX_HOURS = 24


@dataclass
class _Entry:
    sys_password: str
    expires: float


class SessionStore:
    def __init__(self, clock=time.time):
        self._entries: dict[str, _Entry] = {}
        self._clock = clock

    @staticmethod
    def new_id() -> str:
        return secrets.token_urlsafe(32)

    def _purge(self) -> None:
        now = self._clock()
        for sid in [s for s, e in self._entries.items() if e.expires <= now]:
            del self._entries[sid]

    def set_sys_password(self, sid: str, password: str, hours: float = DEFAULT_HOURS) -> float:
        self._purge()
        expires = self._clock() + min(max(hours, 0.01), MAX_HOURS) * 3600
        self._entries[sid] = _Entry(password, expires)
        return expires

    def sys_password(self, sid: str | None) -> str | None:
        self._purge()
        e = self._entries.get(sid or "")
        return e.sys_password if e else None

    def expires(self, sid: str | None) -> float | None:
        self._purge()
        e = self._entries.get(sid or "")
        return e.expires if e else None

    def clear(self, sid: str | None) -> None:
        self._entries.pop(sid or "", None)
