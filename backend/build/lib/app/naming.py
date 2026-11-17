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
