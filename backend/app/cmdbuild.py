"""argv lists for Swingbench tools (never a shell string) and their masked display."""
from __future__ import annotations

import shlex

from .redact import mask_argv


def oewizard_argv(oewizard: str, connect: str, soe_password: str, scale: str, threads: int,
                  sys_password: str, tablespace: str, user: str = "soe") -> list[str]:
    """create_swingbench.sh step 2. oewizard only takes passwords as arguments (visible in ps)."""
    return [oewizard, "-cl", "-create", "-cs", connect, "-u", user, "-p", soe_password,
            "-scale", scale, "-tc", str(threads), "-dba", "sys as sysdba", "-dbap", sys_password,
            "-ts", tablespace, "-async_off", "-v"]


def sbutil_dup_argv(sbutil: str, connect: str, user: str, password: str, parallel: int, dup: int) -> list[str]:
    """Increase an existing SOE schema: sbutil -u soe -p pw -cs //scan/svc -soe -parallel 32 -dup 2."""
    return [sbutil, "-u", user, "-p", password, "-cs", connect, "-soe", "-parallel", str(parallel),
            "-dup", str(dup)]


def display(argv: list[str]) -> str:
    """Shell-quoted command with -p / -dbap values masked."""
    return " ".join(shlex.quote(a) for a in mask_argv(argv))


def charbench_argv(charbench: str, connect: str, soe_password: str, config: str, users: int,
                   disable_txns: str, runtime: str, results: str, user: str = "soe") -> list[str]:
    """run_charbench_all_pdbs.sh workload command; -di is omitted when nothing is disabled."""
    argv = [charbench, "-cs", connect, "-u", user, "-p", soe_password, "-c", config,
            "-v", "users,tpm,tps,errs,vresp", "-intermin", "0", "-intermax", "0", "-min", "0", "-max", "0",
            "-uc", str(users)]
    if disable_txns:
        argv += ["-di", disable_txns]
    return argv + ["-rt", runtime, "-r", results]
