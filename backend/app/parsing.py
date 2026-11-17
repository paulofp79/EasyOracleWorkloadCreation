"""Parsers for sqlplus output. Success is judged by explicit tokens, never by exit code alone."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sqlbuild import PDB_TOKEN, SOE_CHECK_TOKEN

ORA_LINE = re.compile(r"(ORA|TNS|SP2)-[0-9]+")
TIMEOUT_CODES = (124, 137)          # GNU timeout / SIGKILL, as the script checks
DETAIL_MAX = 200


def first_error(output: str) -> str:
    """First line containing ORA-/TNS-/SP2-, else the first non-blank line (as `grep -m1` would)."""
    lines = output.splitlines()
    for line in lines:
        if ORA_LINE.search(line):
            return line.strip()
    for line in lines:
        if line.strip():
            return line.strip()
    return ""


def _clean(detail: str) -> str:
    return detail.replace("\t", " ")[:DETAIL_MAX]


@dataclass
class CheckResult:
    status: str            # OK | FAIL
    detail: str            # instance name when OK, reason when FAIL
    instance: str | None = None


def parse_check(pdb: str, output: str, exit_code: int | None, timed_out: bool, timeout_s: int) -> CheckResult:
    """Same decision as check_pdb() in run_charbench_all_pdbs.sh."""
    line = next((ln for ln in output.splitlines() if ln.startswith(SOE_CHECK_TOKEN)), None)
    if line is not None:
        parts = line.split("|")
        parts += [""] * (4 - len(parts))
        con, inst, ntab = (re.sub(r"\s", "", p) for p in parts[1:4])
        if con.upper() != pdb.upper():
            return CheckResult("FAIL", _clean(f"connected to container {con}, expected {pdb}"))
        if ntab != "4":
            return CheckResult("FAIL", _clean(f"SOE schema missing or incomplete ({ntab}/4 core tables)"))
        return CheckResult("OK", _clean(inst), inst)
    if timed_out or exit_code in TIMEOUT_CODES:
        return CheckResult("FAIL", f"timed out after {timeout_s}s")
    reason = first_error(output) or f"no output from sqlplus (exit code {exit_code})"
    return CheckResult("FAIL", _clean(reason))


@dataclass
class PdbRow:
    name: str
    con_uid: str
    rw_instances: int = 0
    restricted: bool = False
    instances: list[dict] = field(default_factory=list)   # {inst_id, instance, open_mode, restricted}


def parse_inventory(output: str) -> dict[str, PdbRow]:
    pdbs: dict[str, PdbRow] = {}
    for line in output.splitlines():
        if not line.startswith(PDB_TOKEN):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7 or not parts[3].isdigit():
            continue
        _, name, con_uid, inst_id, mode, inst_name, restricted = parts[:7]
        row = pdbs.setdefault(name, PdbRow(name, con_uid))
        row.instances.append({"inst_id": int(inst_id), "instance": inst_name, "open_mode": mode,
                              "restricted": restricted.upper() == "YES"})
        if mode == "READ WRITE":
            row.rw_instances += 1
        row.restricted = row.restricted or restricted.upper() == "YES"
    return pdbs


def parse_preflight(output: str) -> dict[str, tuple[str, int]]:
    """name|con_uid|rw lines -> {name: (con_uid, rw)}; malformed lines are skipped like the script does."""
    out: dict[str, tuple[str, int]] = {}
    for line in output.splitlines():
        parts = [re.sub(r"\s", "", p) for p in line.split("|")]
        if len(parts) == 3 and parts[0] and parts[1] and parts[2].isdigit():
            out[parts[0]] = (parts[1], int(parts[2]))
    return out
