"""How privileged SQL reaches the CDB root.

local   sqlplus -L -s / as sysdba
        OS authentication on a database node (ORACLE_SID selects the local instance), as the scripts do.
remote  sqlplus -L -s /nolog, then on stdin
            CONNECT sys/"<password>"@//<scan>:<port>/<cdb service> AS SYSDBA
        from a separate application server: the same CONNECT syntax run_charbench_all_pdbs.sh uses for
        SOE, so the password never appears on a command line. A failed CONNECT does not stop SQL*Plus
        and may still exit 0 (PROJECT_CONTEXT gotcha 1); nothing runs in the database then (every
        statement fails with SP2-0640 Not connected), and the session selects a marker right after
        CONNECT so callers can tell a failed logon from success: see logon_failure().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parsing import first_error

SESSION_TOKEN = "SYSDBA_SESSION_OK"
# Built by concatenation so the marker only appears in output when the query really ran.
_TOKEN_SQL = "SELECT 'SYSDBA_' || 'SESSION_OK' AS c FROM dual;\n"


class SysPasswordMissing(Exception):
    """Remote mode needs the SYS password and none was given in the request or the browser session."""

    def __init__(self):
        super().__init__("The SYS password is needed for the remote CDB connection: enter it with "
                         "'SYS password' in the top bar (kept in server memory for this browser session).")


@dataclass(frozen=True)
class SysdbaConn:
    mode: str = "local"
    host: str | None = None
    port: int | None = None
    service: str | None = None
    password: str | None = None

    @classmethod
    def from_params(cls, params: dict[str, Any], password: str | None) -> "SysdbaConn":
        mode = params.get("cdb_connect", "local")
        if mode != "remote":
            return cls()
        return cls("remote", params.get("scan_host"), params.get("scan_port"), params.get("cdb_service"), password)

    @property
    def remote(self) -> bool:
        return self.mode == "remote"

    def require(self) -> "SysdbaConn":
        if self.remote and not self.password:
            raise SysPasswordMissing()
        return self

    def connect_identifier(self) -> str:
        return f"//{self.host}:{self.port}/{self.service}"

    def argv(self) -> list[str]:
        if self.remote:
            return ["sqlplus", "-L", "-s", "/nolog"]
        return ["sqlplus", "-L", "-s", "/", "as", "sysdba"]

    def stdin(self, sql: str) -> str:
        if not self.remote:
            return sql
        self.require()
        return f'CONNECT sys/"{self.password}"@{self.connect_identifier()} AS SYSDBA\n{_TOKEN_SQL}{sql}'

    def logon_failure(self, output: str) -> str | None:
        """None when the session was connected; otherwise the reason (first ORA-/TNS-/SP2- line)."""
        if not self.remote or SESSION_TOKEN in output:
            return None
        return first_error(output) or "SYSDBA logon failed (no output from sqlplus)"

    def describe(self) -> str:
        return f"sys@{self.connect_identifier()} as sysdba" if self.remote else "/ as sysdba"
