"""PDB names, service names and Easy Connect strings (PROJECT_CONTEXT §4.1)."""
from __future__ import annotations


def pdb_name(prefix: str, number: int) -> str:
    return f"{prefix.upper()}_{number:03d}"


def pdb_range(prefix: str, first: int, last: int) -> list[str]:
    return [pdb_name(prefix, i) for i in range(first, last + 1)]


def service_name(pdb: str, suffix: str = "") -> str:
    return f"{pdb.lower()}{suffix}"


def connect_string(host: str, port: int, service: str) -> str:
    return f"//{host}:{port}/{service}"


def pdb_connect_string(pdb: str, host: str, port: int, suffix: str = "") -> str:
    return connect_string(host, port, service_name(pdb, suffix))


def schema_user(schema_prefix: str = "") -> str:
    """Swingbench schema user as shown and recorded: SOE, or <PREFIX>_SOE."""
    return f"{schema_prefix.upper()}_SOE" if schema_prefix else "SOE"


def login_user(schema_prefix: str = "") -> str:
    """User name written into SQL and command lines: 'soe' exactly as the scripts write it, or <PREFIX>_SOE."""
    return schema_user(schema_prefix) if schema_prefix else "soe"
