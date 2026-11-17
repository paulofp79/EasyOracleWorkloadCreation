from __future__ import annotations

from .base import Operation
from .check_conn import CheckConnections
from .load_soe import LoadSoe
from .pdb_admin import CreatePdbs, DropPdbs
from .workload import Workload

OPERATIONS: dict[str, type[Operation]] = {
    CheckConnections.type: CheckConnections,
    CreatePdbs.type: CreatePdbs,
    DropPdbs.type: DropPdbs,
    LoadSoe.type: LoadSoe,
    Workload.type: Workload,
}

# Run types whose failed / not-started PDBs can be retried as a new run.
RETRYABLE = {CheckConnections.type, LoadSoe.type}
