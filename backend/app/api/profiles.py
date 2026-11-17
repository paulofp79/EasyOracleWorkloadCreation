from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import validators as v
from .deps import actor, db

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileIn(BaseModel):
    name: Any = None
    params: dict[str, Any] = {}


def _clean(body: ProfileIn) -> tuple[str, dict[str, Any]]:
    errors: dict[str, str] = {}
    name = ""
    try:
        name = v.profile_name(body.name)
    except v.RuleError as exc:
        errors["name"] = str(exc)
    params: dict[str, Any] = {}
    try:
        params = v.validate_profile_params(body.params)
    except v.ValidationError as exc:
        errors.update({f"params.{k}": msg for k, msg in exc.errors.items()})
    if errors:
        raise v.ValidationError(errors)
    return name, params


@router.get("")
def list_profiles(request: Request):
    return {"profiles": db(request).list_profiles()}


@router.get("/fields")
def fields():
    """Field metadata for forms: rule name and default of every profile parameter."""
    return {"fields": {k: {"rule": r, "default": d} for k, (r, d) in v.PROFILE_FIELDS.items()}}


@router.get("/active")
def active(request: Request):
    p = db(request).active_profile()
    if p is None:
        raise HTTPException(404, "no active profile")
    return p


@router.get("/{pid}")
def get_profile(pid: int, request: Request):
    p = db(request).get_profile(pid)
    if p is None:
        raise HTTPException(404, "profile not found")
    return p


@router.post("", status_code=201)
def create_profile(body: ProfileIn, request: Request):
    name, params = _clean(body)
    d = db(request)
    if d.get_profile_by_name(name):
        raise v.ValidationError({"name": "a profile with this name already exists"})
    p = d.create_profile(name, params, active=d.active_profile() is None)
    d.audit(actor(request), "profile_create", params={"id": p["id"], "name": name})
    return p


@router.put("/{pid}")
def update_profile(pid: int, body: ProfileIn, request: Request):
    d = db(request)
    if d.get_profile(pid) is None:
        raise HTTPException(404, "profile not found")
    name, params = _clean(body)
    other = d.get_profile_by_name(name)
    if other and other["id"] != pid:
        raise v.ValidationError({"name": "a profile with this name already exists"})
    p = d.update_profile(pid, name, params)
    d.audit(actor(request), "profile_update", params={"id": pid, "name": name})
    return p


@router.post("/{pid}/activate")
def activate(pid: int, request: Request):
    p = db(request).activate_profile(pid)
    if p is None:
        raise HTTPException(404, "profile not found")
    db(request).audit(actor(request), "profile_activate", params={"id": pid})
    return p


@router.delete("/{pid}", status_code=204)
def delete(pid: int, request: Request):
    d = db(request)
    if len(d.list_profiles()) <= 1:
        raise HTTPException(409, "cannot delete the last profile")
    if not d.delete_profile(pid):
        raise HTTPException(404, "profile not found")
    d.audit(actor(request), "profile_delete", params={"id": pid})
