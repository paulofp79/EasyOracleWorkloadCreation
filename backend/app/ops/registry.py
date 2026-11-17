from __future__ import annotations

from .base import Operation
from .check_conn import CheckConnections

OPERATIONS: dict[str, type[Operation]] = {
    CheckConnections.type: CheckConnections,
}
