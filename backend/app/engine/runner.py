"""ProcessRunner: the only place that starts external programs.

* argv lists only (never a shell); the executable is resolved with the child's PATH.
* every child gets its own session/process group (start_new_session=True), so one
  os.killpg() reaches sqlplus, oewizard, charbench and all their Java children.
* secrets go in through stdin (closed right after writing), never argv for sqlplus.
* stdout+stderr are merged, read line by line, redacted, then written to the log file
  (mode 600) and kept as a bounded in-memory tail.
* timeout: TERM the group, wait kill_grace seconds, KILL (same as `timeout -k 5`).
* kill_all(): TERM every live group, wait, KILL survivors (cancel / Stop all).
"""
from __future__ import annotations

import asyncio
import collections
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from ..redact import Redactor

TIMEOUT_EXIT = 124          # what GNU timeout reports; kept for parity with the scripts
LINE_LIMIT = 1 << 20        # longest line read in one piece
DEFAULT_TAIL = 200


class ToolNotFound(Exception):
    pass


@dataclass
class ProcResult:
    argv0: str
    pid: int | None
    pgid: int | None
    starttime: int | None
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False
    started: float = 0.0
    ended: float = 0.0
    tail: list[str] = field(default_factory=list)
    log_path: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.ended - self.started)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled

    @property
    def output(self) -> str:
        return "\n".join(self.tail)


# ---- /proc helpers ------------------------------------------------------------------------

def proc_starttime(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat (clock ticks since boot); identifies a process across PID reuse."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        return int(data[data.rindex(")") + 2:].split()[19])
    except (ValueError, IndexError):
        return None


def _ppid_map() -> dict[int, list[int]]:
    children: dict[int, list[int]] = collections.defaultdict(list)
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            data = Path(f"/proc/{entry}/stat").read_text()
            ppid = int(data[data.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children[ppid].append(int(entry))
    return children


def descendants(pid: int) -> list[int]:
    tree = _ppid_map()
    out, stack = [], list(tree.get(pid, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(tree.get(p, []))
    return out


def group_members(pgid: int) -> list[int]:
    """Live (non-zombie) processes whose process group is pgid."""
    members = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            data = Path(f"/proc/{entry}/stat").read_text()
            fields = data[data.rindex(")") + 2:].split()
        except (OSError, ValueError):
            continue
        if fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(entry))
    return members


def group_alive(pgid: int) -> bool:
    # killpg(pgid, 0) also succeeds for zombies that nobody has reaped yet (for example when
    # PID 1 in a container does not reap orphans), so look at the actual process states.
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return bool(group_members(pgid))


def process_alive(pid: int, starttime: int | None = None) -> bool:
    st = proc_starttime(pid)
    if st is None:
        return False
    if starttime is not None and st != starttime:
        return False  # PID reused by another process
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return False


def signal_group(pgid: int, sig: int, extra_pids: Iterable[int] = ()) -> None:
    """Signal a process group, plus stragglers that left the group (they called setsid)."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    for p in extra_pids:
        try:
            os.kill(p, sig)
        except (ProcessLookupError, PermissionError):
            pass


# ---- runner -------------------------------------------------------------------------------

LineCallback = Callable[[str], None]
StartCallback = Callable[[ProcResult], Awaitable[None] | None]


class ProcessRunner:
    def __init__(self, env: dict[str, str], redactor: Redactor | None = None, kill_grace: float = 2.0):
        self.env = env
        self.redactor = redactor or Redactor()
        self.kill_grace = kill_grace
        self._live: dict[int, asyncio.subprocess.Process] = {}
        self._cancelling = False

    def which(self, cmd: str) -> str | None:
        if "/" in cmd:
            return cmd if os.access(cmd, os.X_OK) and os.path.isfile(cmd) else None
        return shutil.which(cmd, path=self.env.get("PATH", os.defpath))

    @property
    def live_pgids(self) -> list[int]:
        return list(self._live)

    async def run(
        self,
        argv: list[str],
        *,
        stdin_text: str | None = None,
        log_path: str | Path | None = None,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        timeout_grace: float = 5.0,
        on_start: StartCallback | None = None,
        on_line: LineCallback | None = None,
        tail_lines: int = DEFAULT_TAIL,
    ) -> ProcResult:
        exe = self.which(argv[0])
        if exe is None:
            raise ToolNotFound(f"{argv[0]}: not found or not executable")
        if self._cancelling:
            return ProcResult(argv0=argv[0], pid=None, pgid=None, starttime=None, exit_code=None,
                              cancelled=True, started=time.time(), ended=time.time())

        log_fh = None
        if log_path is not None:
            fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            log_fh = os.fdopen(fd, "w", encoding="utf-8", errors="replace", buffering=1)

        started = time.time()
        proc = await asyncio.create_subprocess_exec(
            exe, *argv[1:],
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd) if cwd else None, env=self.env,
            start_new_session=True, limit=LINE_LIMIT,
        )
        self._live[proc.pid] = proc
        res = ProcResult(argv0=argv[0], pid=proc.pid, pgid=proc.pid, starttime=proc_starttime(proc.pid),
                         exit_code=None, started=started,
                         log_path=str(log_path) if log_path is not None else None)
        tail: collections.deque[str] = collections.deque(maxlen=tail_lines)
        try:
            if on_start is not None:
                r = on_start(res)
                if asyncio.iscoroutine(r):
                    await r
            reader = asyncio.create_task(self._pump(proc, tail, log_fh, on_line))
            if stdin_text is not None:
                try:
                    proc.stdin.write(stdin_text.encode())
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                res.timed_out = True
                await self._terminate(proc, timeout_grace)
            # If a descendant escaped the group and holds the pipe, do not wait for it forever.
            try:
                await asyncio.wait_for(asyncio.shield(reader), timeout=2.0)
            except asyncio.TimeoutError:
                reader.cancel()
        except asyncio.CancelledError:
            await self._terminate(proc, self.kill_grace)
            raise
        finally:
            self._live.pop(proc.pid, None)
            if log_fh is not None:
                log_fh.close()
        res.ended = time.time()
        res.exit_code = TIMEOUT_EXIT if res.timed_out else proc.returncode
        res.cancelled = self._cancelling and not res.timed_out and proc.returncode != 0
        res.tail = list(tail)
        return res

    async def _pump(self, proc, tail, log_fh, on_line) -> None:
        stream = proc.stdout
        while True:
            try:
                raw = await stream.readline()
            except ValueError:  # line longer than LINE_LIMIT: take what is buffered
                raw = await stream.read(LINE_LIMIT)
            if not raw:
                break
            line = self.redactor.text(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            tail.append(line)
            if log_fh is not None:
                log_fh.write(line + "\n")
            if on_line is not None:
                on_line(line)

    async def _terminate(self, proc: asyncio.subprocess.Process, grace: float) -> None:
        pgid = proc.pid
        extra = descendants(pgid)
        signal_group(pgid, signal.SIGTERM, extra)
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
        except asyncio.TimeoutError:
            pass
        # KILL whatever is left in the group (including children that ignored TERM).
        signal_group(pgid, signal.SIGKILL, [p for p in extra if process_alive(p)])
        if proc.returncode is None:
            await proc.wait()

    async def kill_all(self) -> None:
        """Stop every process started by this runner: TERM, wait kill_grace, KILL."""
        self._cancelling = True
        procs = list(self._live.values())
        if not procs:
            return
        extra = {p.pid: descendants(p.pid) for p in procs}
        for p in procs:
            signal_group(p.pid, signal.SIGTERM, extra[p.pid])
        deadline = time.monotonic() + self.kill_grace
        while time.monotonic() < deadline and any(p.returncode is None or group_alive(p.pid) for p in procs):
            await asyncio.sleep(0.1)
        for p in procs:
            signal_group(p.pid, signal.SIGKILL, [x for x in extra[p.pid] if process_alive(x)])
        await asyncio.gather(*(p.wait() for p in procs), return_exceptions=True)
