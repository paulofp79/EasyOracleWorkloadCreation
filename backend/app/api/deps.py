from __future__ import annotations

from fastapi import Request

from ..config import Settings
from ..db import Database


def db(request: Request) -> Database:
    return request.app.state.db


def settings(request: Request) -> Settings:
    return request.app.state.settings


def actor(request: Request) -> str:
    # Until authentication exists (M6) the server only listens on localhost.
    return getattr(request.state, "user", None) or "local"
