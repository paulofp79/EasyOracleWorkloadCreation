"""Operation base class: validate -> plan (dry run) -> execute(ctx)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from .. import validators as v
from ..config import Settings
from ..db import Database
from ..naming import pdb_range


class PlanError(Exception):
    """The request is valid but cannot run (e.g. the database said no). Shown to the user as is."""


class Operation:
    type: ClassVar[str]
    # field -> validator rule; every field is required after profile defaults are applied
    PARAMS: ClassVar[dict[str, str]]
    # fields that may be absent
    OPTIONAL: ClassVar[dict[str, str]] = {}
    # request field -> profile field used as its default
    PROFILE_DEFAULTS: ClassVar[dict[str, str]] = {}
    # secret name -> validator rule
    SECRETS: ClassVar[dict[str, str]] = {}

    def __init__(self, settings: Settings, db: Database, params: dict[str, Any], secrets: dict[str, str],
                 profile_id: int | None = None):
        self.settings = settings
        self.db = db
        self.params = params
        self.secrets = secrets
        self.profile_id = profile_id

    # ---- construction / validation -------------------------------------------------------
    @classmethod
    def from_request(cls, settings: Settings, db: Database, raw_params: dict[str, Any],
                     raw_secrets: dict[str, Any], profile: dict[str, Any] | None, need_secrets: bool) -> "Operation":
        merged: dict[str, Any] = {}
        if profile:
            for field, pfield in cls.PROFILE_DEFAULTS.items():
                if pfield in profile["params"]:
                    merged[field] = profile["params"][pfield]
        merged.update({k: val for k, val in raw_params.items() if val is not None})
        errors: dict[str, str] = {}
        params: dict[str, Any] = {}
        for k in raw_params:
            if k not in cls.PARAMS and k not in cls.OPTIONAL:
                errors[k] = "unknown field"
        try:
            params.update(v.validate({k: merged[k] for k in cls.PARAMS if k in merged}, cls.PARAMS))
        except v.ValidationError as exc:
            errors.update(exc.errors)
        opt = {k: merged[k] for k in cls.OPTIONAL if k in merged}
        try:
            params.update(v.validate(opt, cls.OPTIONAL, partial=True))
        except v.ValidationError as exc:
            errors.update(exc.errors)
        secrets: dict[str, str] = {}
        if need_secrets:
            try:
                secrets = v.validate({k: raw_secrets.get(k) for k in cls.SECRETS}, cls.SECRETS)
            except v.ValidationError as exc:
                errors.update({f"secrets.{k}": m for k, m in exc.errors.items()})
        unknown = set(raw_secrets) - set(cls.SECRETS)
        for k in unknown:
            errors[f"secrets.{k}"] = "unknown secret"
        if not errors:
            try:
                cls.check(params)
            except v.ValidationError as exc:
                errors.update(exc.errors)
        if errors:
            raise v.ValidationError(errors)
        return cls(settings, db, params, secrets, profile["id"] if profile else None)

    @classmethod
    def check(cls, params: dict[str, Any]) -> None:
        """Cross-field validation hook."""
        if "pdbs" in params:
            prefix = params["prefix"] + "_"
            bad = [p for p in params["pdbs"] if not p.startswith(prefix)]
            if bad:
                raise v.ValidationError({"pdbs": f"not in prefix {params['prefix']}: {', '.join(bad[:5])}"})

    # ---- behaviour -----------------------------------------------------------------------
    def targets(self) -> list[str]:
        if self.params.get("pdbs"):
            return list(self.params["pdbs"])
        return pdb_range(self.params["prefix"], self.params["first"], self.params["last"])

    def secret_values(self) -> list[str]:
        return [s for s in self.secrets.values() if s]

    async def plan(self) -> dict[str, Any]:
        raise NotImplementedError

    async def execute(self, ctx) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    @staticmethod
    def log_files(run_dir: Path, pdb: str) -> list[tuple[str, Path]]:
        return []
