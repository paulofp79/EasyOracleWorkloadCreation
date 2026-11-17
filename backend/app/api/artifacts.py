"""Run artifacts: file list, single-file download, zip of the run directory, CSV of the items."""
from __future__ import annotations

import csv
import io
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .deps import db
from .runs import _run_or_404

router = APIRouter(prefix="/api/runs", tags=["artifacts"])

MAX_LIST = 5000
CSV_COLUMNS = ["pdb", "state", "check_status", "check_detail", "instance", "exit_code", "error_count", "first_error",
               "started_at", "ended_at", "duration_s", "pid", "detail"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _run_dir(request: Request, run_id: str) -> tuple[dict, Path]:
    run = _run_or_404(request, run_id)
    path = Path(run["run_dir"] or "")
    if not run["run_dir"] or not path.is_dir():
        raise HTTPException(404, "the run directory no longer exists")
    return run, path.resolve()


def _kind(name: str) -> str:
    if name.endswith(".tsv") or name == "run.log":
        return "summary"
    if name.endswith("-results.xml"):
        return "results"
    return "log"


@router.get("/{run_id}/artifacts")
def list_artifacts(run_id: str, request: Request):
    _, base = _run_dir(request, run_id)
    files = []
    total = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        st = p.stat()
        total += st.st_size
        if len(files) < MAX_LIST:
            rel = p.relative_to(base).as_posix()
            files.append({"path": rel, "size": st.st_size, "mtime": int(st.st_mtime), "kind": _kind(p.name)})
    order = {"summary": 0, "results": 1, "log": 2}
    files.sort(key=lambda f: (order[f["kind"]], f["path"]))
    return {"run_dir": str(base), "files": files, "count": len(files), "total_bytes": total}


@router.get("/{run_id}/files/{path:path}")
def download_file(run_id: str, path: str, request: Request):
    _, base = _run_dir(request, run_id)
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "no such file in this run")
    media = "application/xml" if target.suffix == ".xml" else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media, filename=target.name,
                        content_disposition_type="inline" if target.suffix in (".log", ".tsv") else "attachment")


def _build_zip(base: Path, run_id: str) -> str:
    fd, tmp = tempfile.mkstemp(prefix=f"run-{run_id}-", suffix=".zip")   # mode 0600
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.is_symlink():
                zf.write(p, f"run-{run_id}/{p.relative_to(base).as_posix()}")
    return tmp


@router.get("/{run_id}/artifacts.zip")
async def download_zip(run_id: str, request: Request):
    _, base = _run_dir(request, run_id)
    tmp = await run_in_threadpool(_build_zip, base, run_id)
    return FileResponse(tmp, media_type="application/zip", filename=f"run-{run_id}.zip",
                        background=BackgroundTask(os.unlink, tmp))


def _cell(value) -> str:
    """Neutralise spreadsheet formulas (an ORA- message starting with '-' or '=' must stay text)."""
    if value is None:
        return ""
    s = str(value)
    return "'" + s if s.startswith(FORMULA_START) else s


@router.get("/{run_id}/summary.csv")
def summary_csv(run_id: str, request: Request):
    _run_or_404(request, run_id)
    from ..ops.compare import _dur

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for it in db(request).get_items(run_id):
        d = _dur(it)
        row = {**it, "duration_s": round(d, 1) if d is not None else None}
        w.writerow([_cell(row.get(c)) for c in CSV_COLUMNS])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="run-{run_id}-summary.csv"'})
