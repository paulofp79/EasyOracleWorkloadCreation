"""Processes that outlived the backend (a crash or kill -9 while a run was in progress).

Every started process is recorded with pid, pgid (== pid, each child leads its own session) and
its /proc start time. A recorded process counts as still alive when
  * the leader pid exists, is not a zombie and has the same start time (not a reused PID), or
  * the leader is gone but live members of its process group remain (e.g. Java children),
    and no *different* process now holds the leader's pid.
"""
from __future__ import annotations

import asyncio
import collections
import os
import signal
import time
from pathlib import Path
from typing import Any, Iterable

KILL_GRACE = 2.0


def proc_table() -> dict[int, tuple[int, int, str]]:
    """pid -> (pgrp, starttime, state) for every process, in one /proc scan."""
    table: dict[int, tuple[int, int, str]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            data = Path(f"/proc/{entry}/stat").read_text()
            f = data[data.rindex(")") + 2:].split()
            table[int(entry)] = (int(f[2]), int(f[19]), f[0])
        except (OSError, ValueError, IndexError):
            continue
    return table


def alive_items(items: Iterable[dict[str, Any]], table: dict | None = None) -> dict[str, dict[str, Any]]:
    """{pdb: {pid, pgid, members}} for recorded processes that are verifiably still running."""
    table = proc_table() if table is None else table
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for pid, (pgrp, _, state) in table.items():
        if state != "Z":
            groups[pgrp].append(pid)
    alive: dict[str, dict[str, Any]] = {}
    for it in items:
        pid = it.get("pid")
        if not pid:
            continue
        pgid = it.get("pgid") or pid
        start = it.get("proc_starttime")
        leader = table.get(pid)
        if leader is not None and start is not None and leader[1] != start:
            continue                                   # the pid now belongs to another process
        members = groups.get(pgid, [])
        leader_ok = leader is not None and leader[2] != "Z"
        if leader_ok or members:
            alive[it["pdb"]] = {"pid": pid, "pgid": pgid, "members": sorted(members)}
    return alive


async def kill_groups(alive: dict[str, dict[str, Any]], items: list[dict[str, Any]],
                      grace: float = KILL_GRACE) -> dict[str, list[str]]:
    """TERM every group (and stragglers), wait, KILL what is left, then verify."""
    def send(sig: int) -> None:
        for info in alive.values():
            try:
                os.killpg(info["pgid"], sig)
            except (ProcessLookupError, PermissionError):
                pass
            for m in info["members"]:
                try:
                    os.kill(m, sig)
                except (ProcessLookupError, PermissionError):
                    pass

    send(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and alive_items([it for it in items if it["pdb"] in alive]):
        await asyncio.sleep(0.1)
    send(signal.SIGKILL)
    await asyncio.sleep(0.2)
    still = alive_items([it for it in items if it["pdb"] in alive])
    return {"stopped": sorted(set(alive) - set(still)), "still_alive": sorted(still)}
