"""Audit log: who started, stopped, dropped, logged in, changed profiles or passwords (PROJECT_CONTEXT §10)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def audit(request: Request, limit: int = 100, offset: int = 0, action: str | None = None,
          actor: str | None = None, run_id: str | None = None):
    entries, total = db(request).audit_entries(min(max(limit, 1), 1000), max(offset, 0), action or None,
                                               (actor or "").strip()[:64] or None, run_id or None)
    return {"entries": entries, "total": total, "actions": db(request).audit_actions()}
