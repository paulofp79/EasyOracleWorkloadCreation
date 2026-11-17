from __future__ import annotations

import asyncio
import collections
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import validators as v
from ..engine.events import sse
from ..engine.runs import Conflict, RunManager
from ..ops.base import PlanError
from ..ops.registry import OPERATIONS
from .deps import actor, db, settings

router = APIRouter(prefix="/api/runs", tags=["runs"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
LOG_POLL_S = 0.5
MAX_TAIL = 5000


class RunIn(BaseModel):
    type: str
    params: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    dry_run: bool = False
    profile_id: int | None = None


def manager(request: Request) -> RunManager:
    return request.app.state.runs


def _run_or_404(request: Request, run_id: str) -> dict[str, Any]:
    run = manager(request).run_view(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.post("")
async def create_run(body: RunIn, request: Request):
    op_cls = OPERATIONS.get(body.type)
    if op_cls is None:
        raise v.ValidationError({"type": f"must be one of {', '.join(sorted(OPERATIONS))}"})
    d = db(request)
    profile = d.get_profile(body.profile_id) if body.profile_id is not None else d.active_profile()
    if body.profile_id is not None and profile is None:
        raise v.ValidationError({"profile_id": "profile not found"})
    op = op_cls.from_request(settings(request), d, body.params, body.secrets, profile, need_secrets=not body.dry_run)
    if body.dry_run:
        try:
            return {"plan": await op.plan(), "params": op.params}
        except PlanError as exc:
            raise HTTPException(502, str(exc)) from exc
    try:
        run_id = await manager(request).start(op, actor(request))
    except Conflict as exc:
        return JSONResponse({"error": str(exc), "run_id": exc.run_id}, status_code=409)
    return JSONResponse({"run_id": run_id}, status_code=201)


@router.get("")
def list_runs(request: Request, limit: int = 50, type: str | None = None):
    m = manager(request)
    runs = db(request).list_runs(min(max(limit, 1), 500), type)
    return {"runs": [m.run_view(r["id"]) for r in runs]}


@router.get("/active")
def active_run(request: Request):
    ctx = manager(request).active
    return {"run": manager(request).run_view(ctx.run_id) if ctx else None}


@router.get("/{run_id}")
def get_run(run_id: str, request: Request):
    return _run_or_404(request, run_id)


@router.get("/{run_id}/items")
def get_items(run_id: str, request: Request):
    run = _run_or_404(request, run_id)
    ctx = manager(request).contexts.get(run_id)
    items = [dict(it) for it in sorted(ctx.items.values(), key=lambda i: i["ord"])] if ctx \
        else db(request).get_items(run_id)
    return {"items": items, "event_seq": run["event_seq"]}


@router.get("/{run_id}/events")
async def events(run_id: str, request: Request, last_id: int | None = None,
                 last_event_id: str | None = Header(default=None)):
    _run_or_404(request, run_id)
    if last_event_id and last_event_id.isdigit():
        last_id = int(last_event_id)            # browser reconnect wins over the query parameter
    bus = manager(request).buses.get(run_id)

    async def gen():
        if bus is None:                          # finished before this backend started: REST has everything
            yield sse(None, "end", {"seq": None})
            return
        async for chunk in bus.stream(last_id):
            if await request.is_disconnected():
                return
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{run_id}/cancel", status_code=202)
async def cancel(run_id: str, request: Request):
    _run_or_404(request, run_id)
    if not await manager(request).cancel(run_id, actor(request)):
        raise HTTPException(409, "run is not in progress")
    return {"cancelling": True}


# ---- logs -------------------------------------------------------------------------------------

def _log_files(request: Request, run_id: str, pdb: str) -> tuple[dict, list[tuple[str, Path]]]:
    run = _run_or_404(request, run_id)
    if db(request).get_item(run_id, pdb) is None:          # also prevents any path games
        raise HTTPException(404, "PDB is not part of this run")
    op_cls = OPERATIONS.get(run["type"])
    files = op_cls.log_files(Path(run["run_dir"]), pdb) if op_cls else []
    return run, files


def _tail(path: Path, n: int) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip("\n") for ln in collections.deque(fh, maxlen=n)]


def _file_info(files: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    return [{"index": i, "label": label, "name": p.name, "exists": p.is_file(),
             "size": p.stat().st_size if p.is_file() else 0} for i, (label, p) in enumerate(files)]


@router.get("/{run_id}/items/{pdb}/log")
def item_log(run_id: str, pdb: str, request: Request, tail: int = 500, file: int = 0):
    _, files = _log_files(request, run_id, pdb)
    if not files:
        return {"files": [], "lines": []}
    if not 0 <= file < len(files):
        raise HTTPException(404, "no such log file")
    return {"files": _file_info(files), "file": file, "lines": _tail(files[file][1], min(max(tail, 1), MAX_TAIL))}


@router.get("/{run_id}/items/{pdb}/log/stream")
async def item_log_stream(run_id: str, pdb: str, request: Request, file: int = 0, tail: int = 200):
    _, files = _log_files(request, run_id, pdb)
    if not 0 <= file < len(files):
        raise HTTPException(404, "no such log file")
    path = files[file][1]
    m = manager(request)

    async def gen():
        pos = 0
        if path.is_file():
            lines = _tail(path, min(max(tail, 1), MAX_TAIL))
            pos = path.stat().st_size
            yield sse(None, "lines", {"lines": lines, "reset": True})
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            size = path.stat().st_size if path.is_file() else 0
            if size > pos:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read(size - pos)
                pos = size
                complete, _, _ = chunk.rpartition("\n")
                if complete:
                    yield sse(None, "lines", {"lines": complete.split("\n")})
                    pos -= len(chunk) - len(complete) - 1
                idle = 0
            else:
                idle += 1
                ctx = m.active
                if not (ctx and ctx.run_id == run_id) and idle > 2:
                    yield sse(None, "end", {})
                    return
                if idle % 30 == 0:
                    yield ": keepalive\n\n"
            await asyncio.sleep(LOG_POLL_S)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/{run_id}/log")
def run_log(run_id: str, request: Request, tail: int = 500):
    run = _run_or_404(request, run_id)
    return {"lines": _tail(Path(run["run_dir"]) / "run.log", min(max(tail, 1), MAX_TAIL))}
