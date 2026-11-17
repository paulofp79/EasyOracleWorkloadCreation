"""argv lists for Swingbench tools (never a shell string) and their masked display."""
from __future__ import annotations

import shlex

from .redact import mask_argv


def oewizard_argv(oewizard: str, connect: str, soe_password: str, scale: str, threads: int,
                  sys_password: str, tablespace: str) -> list[str]:
    """create_swingbench.sh step 2. oewizard only takes passwords as arguments (visible in ps)."""
    return [oewizard, "-cl", "-create", "-cs", connect, "-u", "soe", "-p", soe_password,
            "-scale", scale, "-tc", str(threads), "-dba", "sys as sysdba", "-dbap", sys_password,
            "-ts", tablespace, "-async_off", "-v"]


def display(argv: list[str]) -> str:
    """Shell-quoted command with -p / -dbap values masked."""
    return " ".join(shlex.quote(a) for a in mask_argv(argv))
