"""How privileged SQL reaches the CDB root.

local   sqlplus -L -s / as sysdba
        OS authentication on a database node (ORACLE_SID selects the local instance), as the scripts do.
remote  sqlplus -L -s sys@//<scan>:<port>/<cdb service> as sysdba
        from a separate application server. The logon string has no password, so sqlplus asks for it and
        reads the first line of stdin: the password never appears on a command line. With -L a failed
        logon makes sqlplus exit at once with an error, so no statement runs unconnected (a CONNECT
        inside the script would not stop the script, see PROJECT_CONTEXT gotcha 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    def logon(self) -> str:
        return f"sys@//{self.host}:{self.port}/{self.service}" if self.remote else "/"

    def argv(self) -> list[str]:
        return ["sqlplus", "-L", "-s", self.logon(), "as", "sysdba"]

    def stdin(self, sql: str) -> str:
        if not self.remote:
            return sql
        self.require()
        return f"{self.password}\n{sql}"

    def describe(self) -> str:
        return f"{self.logon()} as sysdba"
