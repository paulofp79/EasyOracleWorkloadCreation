from __future__ import annotations

from .base import Operation
from .check_conn import CheckConnections
from .pdb_admin import CreatePdbs, DropPdbs

OPERATIONS: dict[str, type[Operation]] = {
    CheckConnections.type: CheckConnections,
    CreatePdbs.type: CreatePdbs,
    DropPdbs.type: DropPdbs,
}
