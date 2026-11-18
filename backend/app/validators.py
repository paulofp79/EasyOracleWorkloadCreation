"""Server-side validation of every user-supplied value (PROJECT_CONTEXT §5).

Values validated here end up in SQL text and in argv lists, so these rules are
the security boundary. The frontend mirrors them (frontend/src/validators.ts);
both are tested against shared/validation-vectors.json.

Each rule takes a raw value (str, int, float or bool, as JSON delivers it) and
returns the normalised value or raises RuleError with a short message.
"""
from __future__ import annotations

import re
from typing import Any, Callable

MAX_PDB_NUMBER = 999
PREFIX_MAX_LEN = 26


class RuleError(ValueError):
    pass


class ValidationError(Exception):
    """Field name -> message. Raised with all errors of a request at once."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


RE_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
RE_POS_INT = re.compile(r"[1-9][0-9]*")
RE_UINT = re.compile(r"[0-9]+")
RE_HOST = re.compile(r"[A-Za-z0-9._-]+")
RE_SUFFIX = re.compile(r"[A-Za-z0-9._-]*")
RE_SCALE = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
RE_TABLESPACE = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")
RE_RUNTIME = re.compile(r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?")
RE_DISABLE = re.compile(r"[A-Za-z0-9,]*")
RE_DELAY = re.compile(r"[0-9]+(?:\.[0-9]+)?")
RE_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _text(v: Any) -> str:
    """Numbers are accepted where the form may send them; bools never are."""
    if isinstance(v, bool) or v is None:
        raise RuleError("is required")
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if not isinstance(v, str):
        raise RuleError("must be a string")
    return v


def prefix(v: Any) -> str:
    s = _text(v)
    if not RE_PREFIX.fullmatch(s):
        raise RuleError("must start with a letter and use only letters, digits and underscore")
    if len(s) > PREFIX_MAX_LEN:
        raise RuleError(f"must be at most {PREFIX_MAX_LEN} characters")
    return s.upper()


def positive_int(v: Any) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        if v < 1:
            raise RuleError("must be a positive integer")
        return v
    s = _text(v)
    if not RE_POS_INT.fullmatch(s):
        raise RuleError("must be a positive integer without leading zeros")
    return int(s)


def pdb_number(v: Any) -> int:
    n = positive_int(v)
    if n > MAX_PDB_NUMBER:
        raise RuleError(f"must be between 1 and {MAX_PDB_NUMBER}")
    return n


def scan_host(v: Any) -> str:
    s = _text(v)
    if not RE_HOST.fullmatch(s):
        raise RuleError("may contain only letters, digits, '.', '_' and '-'")
    if len(s) > 253:
        raise RuleError("is too long")
    return s


def scan_port(v: Any) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        n = v
    else:
        s = _text(v)
        if not RE_UINT.fullmatch(s):
            raise RuleError("must be a number between 1 and 65535")
        n = int(s)
    if not 1 <= n <= 65535:
        raise RuleError("must be a number between 1 and 65535")
    return n


def service_suffix(v: Any) -> str:
    s = "" if v is None else _text(v)
    if not RE_SUFFIX.fullmatch(s):
        raise RuleError("may contain only letters, digits, '.', '_' and '-'")
    if len(s) > 200:
        raise RuleError("is too long")
    return s


def scale(v: Any) -> str:
    s = _text(v)
    if not RE_SCALE.fullmatch(s) or not re.search(r"[1-9]", s):
        raise RuleError("must be a positive number, e.g. 0.1 or 5")
    return s


def tablespace(v: Any) -> str:
    s = _text(v)
    if not RE_TABLESPACE.fullmatch(s) or len(s) > 128:
        raise RuleError("is not a valid tablespace name")
    return s.upper()


def runtime(v: Any) -> str:
    s = _text(v)
    if not RE_RUNTIME.fullmatch(s):
        raise RuleError("must look like HH:MM, e.g. 00:30")
    return s


def disable_txns(v: Any) -> str:
    s = "" if v is None else _text(v)
    if not RE_DISABLE.fullmatch(s):
        raise RuleError("must be a comma-separated list of transaction short names, e.g. SQ,WQ,WA")
    return s


def start_delay(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v < 0:
            raise RuleError("must be a non-negative number of seconds")
        return float(v)
    s = _text(v)
    if not RE_DELAY.fullmatch(s):
        raise RuleError("must be a non-negative number of seconds, e.g. 0 or 0.5")
    return float(s)


def boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v in ("true", "false"):
        return v == "true"
    raise RuleError("must be true or false")


def abs_path(v: Any) -> str:
    s = _text(v)
    if not s.startswith("/"):
        raise RuleError("must be an absolute path")
    if CONTROL_CHARS.search(s) or len(s) > 4096:
        raise RuleError("contains invalid characters")
    return s


def password(v: Any) -> str:
    """Generic secret: non-empty, no control characters (a newline would end the SQL line)."""
    if not isinstance(v, str) or v == "":
        raise RuleError("must not be empty")
    if CONTROL_CHARS.search(v):
        raise RuleError("must not contain control characters or line breaks")
    if len(v) > 1024:
        raise RuleError("is too long")
    return v


def quoted_password(v: Any) -> str:
    """Secret that is placed inside "..." in SQL (SOE, PDB admin, wallet)."""
    s = password(v)
    if '"' in s:
        raise RuleError('must not contain a double quote (")')
    return s


def profile_name(v: Any) -> str:
    s = _text(v).strip()
    if not RE_PROFILE_NAME.fullmatch(s):
        raise RuleError("must be 1-64 characters: letters, digits, space, '.', '_' or '-'")
    return s


RE_PDB_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,25}_[0-9]{3}")


def pdb_names(v: Any) -> list[str]:
    """Explicit PDB selection: list of PREFIX_NNN names, upper-cased, de-duplicated, order kept."""
    if not isinstance(v, list) or not v:
        raise RuleError("must be a non-empty list of PDB names")
    if len(v) > MAX_PDB_NUMBER:
        raise RuleError(f"at most {MAX_PDB_NUMBER} PDBs")
    out: list[str] = []
    for item in v:
        if not isinstance(item, str) or not RE_PDB_NAME.fullmatch(item) or item.endswith("_000"):
            raise RuleError(f"{str(item)[:40]!r} is not a PDB name like PDBPP_001")
        name = item.upper()
        if name not in out:
            out.append(name)
    return out


def existing_action(v: Any) -> str:
    if v not in ("drop", "skip", "abort"):
        raise RuleError("must be drop, skip or abort")
    return v


def confirm_token(v: Any) -> str:
    if not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{16}", v):
        raise RuleError("is not a valid confirmation token; run the plan again")
    return v


def cdb_connect(v: Any) -> str:
    if v not in ("local", "remote"):
        raise RuleError("must be local or remote")
    return v


def cdb_service(v: Any) -> str:
    s = "" if v is None else _text(v)
    if not RE_SUFFIX.fullmatch(s) or len(s) > 200:
        raise RuleError("may contain only letters, digits, '.', '_' and '-'")
    return s


RE_SCHEMA_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,25}")


def schema_prefix(v: Any) -> str:
    """'' for the plain SOE schema, else a prefix giving <PREFIX>_SOE (at most 30 characters)."""
    s = "" if v is None else _text(v)
    if s and not RE_SCHEMA_PREFIX.fullmatch(s):
        raise RuleError("must start with a letter and use letters, digits and underscore (max 26), "
                        "or be empty for SOE")
    return s.upper()


def confirm_delete(v: Any) -> str:
    if v != "DELETE":
        raise RuleError("type DELETE to confirm")
    return v


RULES: dict[str, Callable[[Any], Any]] = {
    "pdb_names": pdb_names,
    "existing_action": existing_action,
    "confirm_token": confirm_token,
    "confirm_delete": confirm_delete,
    "cdb_connect": cdb_connect,
    "schema_prefix": schema_prefix,
    "cdb_service": cdb_service,
    "prefix": prefix, "pdb_number": pdb_number, "positive_int": positive_int,
    "scan_host": scan_host, "scan_port": scan_port, "service_suffix": service_suffix,
    "scale": scale, "tablespace": tablespace, "runtime": runtime, "disable_txns": disable_txns,
    "start_delay": start_delay, "boolean": boolean, "abs_path": abs_path,
    "password": password, "quoted_password": quoted_password, "profile_name": profile_name,
}

# (field name) -> (rule name, default). Non-secret parameters stored in profiles.
PROFILE_FIELDS: dict[str, tuple[str, Any]] = {
    "prefix": ("prefix", "PDBPP"),
    "schema_prefix": ("schema_prefix", ""),
    "create_first": ("pdb_number", 1),
    "create_last": ("pdb_number", 250),
    "load_first": ("pdb_number", 222),
    "load_last": ("pdb_number", 250),
    "workload_first": ("pdb_number", 1),
    "workload_last": ("pdb_number", 250),
    "scan_host": ("scan_host", "scaqaq01-scan1.us.oracle.com"),
    "scan_port": ("scan_port", 1521),
    "service_suffix": ("service_suffix", ""),
    "cdb_connect": ("cdb_connect", "local"),
    "cdb_service": ("cdb_service", ""),
    "rac_cdb": ("boolean", True),
    "scale": ("scale", "0.1"),
    "threads": ("positive_int", 16),
    "tablespace": ("tablespace", "SOETBS"),
    "parallel": ("positive_int", 1),
    "keep_going": ("boolean", False),
    "oewizard_path": ("abs_path", "/home/oracle/swingbench/bin/oewizard"),
    "check_timeout": ("positive_int", 30),
    "check_parallel": ("positive_int", 16),
    "skip_unreachable": ("boolean", False),
    "users": ("positive_int", 8),
    "runtime": ("runtime", "00:30"),
    "charbench_path": ("abs_path", "/home/oracle/swingbench/bin/charbench"),
    "config_xml": ("abs_path", "/home/oracle/swingbench/configs/SOE_Server_Side_V2.xml"),
    "disable_txns": ("disable_txns", "SQ,WQ,WA"),
    "start_delay": ("start_delay", 0.0),
    "log_dir": ("abs_path", "/home/oracle/charbench-logs"),
}

RANGE_PAIRS = [("create_first", "create_last"), ("load_first", "load_last"),
               ("workload_first", "workload_last"), ("first", "last")]


def profile_defaults() -> dict[str, Any]:
    return {k: d for k, (_, d) in PROFILE_FIELDS.items()}


def profile_params(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Stored profile parameters completed with defaults for fields added after the profile was saved."""
    return {**profile_defaults(), **((profile or {}).get("params") or {})}


def validate(data: dict[str, Any], spec: dict[str, str], *, partial: bool = False) -> dict[str, Any]:
    """Validate data against {field: rule}. Unknown fields are errors.

    partial=True allows missing fields (they are simply not returned).
    Raises ValidationError with every problem found.
    """
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}
    for key in data:
        if key not in spec:
            errors[key] = "unknown field"
    for key, rule in spec.items():
        if key not in data:
            if not partial:
                errors[key] = "is required"
            continue
        try:
            clean[key] = RULES[rule](data[key])
        except RuleError as exc:
            errors[key] = str(exc)
    for first, last in RANGE_PAIRS:
        if first in clean and last in clean and clean[first] > clean[last]:
            errors[last] = f"must be greater than or equal to {first.replace('_', ' ')}"
    if clean.get("cdb_connect") == "remote" and "cdb_service" in spec and not clean.get("cdb_service") \
            and "cdb_service" not in errors:
        errors["cdb_service"] = "is required for a remote CDB connection (the CDB root service name)"
    if errors:
        raise ValidationError(errors)
    return clean


def validate_profile_params(data: dict[str, Any]) -> dict[str, Any]:
    """Full profile parameter set; missing fields take their defaults."""
    merged = profile_defaults()
    merged.update(data)
    return validate(merged, {k: r for k, (r, _) in PROFILE_FIELDS.items()})
