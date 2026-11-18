from __future__ import annotations

from .base import Operation
from .check_conn import CheckConnections
from .grow_soe import IncreaseSoe
from .load_soe import LoadSoe
from .pdb_admin import CreatePdbs, DropPdbs
from .workload import Workload

OPERATIONS: dict[str, type[Operation]] = {
    CheckConnections.type: CheckConnections,
    CreatePdbs.type: CreatePdbs,
    DropPdbs.type: DropPdbs,
    LoadSoe.type: LoadSoe,
    IncreaseSoe.type: IncreaseSoe,
    Workload.type: Workload,
}

# Run types whose failed / not-started PDBs can be retried as a new run.
RETRYABLE = {CheckConnections.type, LoadSoe.type, IncreaseSoe.type}
