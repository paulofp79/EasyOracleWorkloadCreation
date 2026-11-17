"""FastAPI application factory."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import validators as v
from .api import env, profiles
from .config import FRONTEND_DIST, STUB_DIR, Settings, load_settings
from .db import Database

log = logging.getLogger("orchestrator")


def seed_profiles(db: Database, settings: Settings) -> None:
    if db.list_profiles():
        return
    params = v.profile_defaults()
    name = "default"
    if settings.simulate:
        name = "simulation"
        params.update(
            scan_host="sim-scan.example.com",
            oewizard_path=str(STUB_DIR / "oewizard"),
            charbench_path=str(STUB_DIR / "charbench"),
            config_xml=str(STUB_DIR / "sim-swingbench" / "configs" / "SOE_Server_Side_V2.xml"),
            log_dir=str(settings.sim_root / "logs"),
            load_first=1, load_last=250,
        )
    db.create_profile(name, v.validate_profile_params(params), active=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        seed_profiles(db, settings)
        app.state.db = db
        log.info("mode=%s db=%s", settings.mode, settings.db_path)
        yield
        db.close()

    app = FastAPI(title="PDB Swingbench Orchestrator", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings

    @app.exception_handler(v.ValidationError)
    async def _validation(_: Request, exc: v.ValidationError):
        return JSONResponse({"error": "validation failed", "errors": exc.errors}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        errors = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return JSONResponse({"error": "validation failed", "errors": errors}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception):
        ref = uuid.uuid4().hex[:8]
        log.exception("internal error ref=%s", ref)
        return JSONResponse({"error": "internal error", "ref": ref}, status_code=500)

    app.include_router(env.router)
    app.include_router(profiles.router)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        dist = FRONTEND_DIST.resolve()
        target = (dist / path).resolve()
        if path and target.is_file() and dist in target.parents:
            return FileResponse(target)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"error": "frontend not built; run `make frontend`"}, status_code=404)

    return app
