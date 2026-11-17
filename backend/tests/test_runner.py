import asyncio
import os
import stat
import time

import pytest

from app.engine.runner import TIMEOUT_EXIT, ProcessRunner, ToolNotFound, group_alive, proc_starttime
from app.redact import MASK, Redactor

from .conftest import pid_gone

ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


async def test_stdin_output_and_log_file_mode(tmp_path):
    log = tmp_path / "out.log"
    r = await ProcessRunner(ENV).run(["cat"], stdin_text="line1\nline2\n", log_path=log)
    assert r.ok and r.exit_code == 0
    assert r.tail == ["line1", "line2"]
    assert log.read_text() == "line1\nline2\n"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


async def test_secret_never_reaches_log_or_tail(tmp_path):
    log = tmp_path / "out.log"
    runner = ProcessRunner(ENV, Redactor(["Hunter2pw"]))
    r = await runner.run(["sh", "-c", 'read x; echo "connect soe/$x@db"; echo "$x" >&2'],
                         stdin_text="Hunter2pw\n", log_path=log)
    assert "Hunter2pw" not in log.read_text()
    assert "Hunter2pw" not in r.output
    assert r.tail == [f"connect soe/{MASK}@db", MASK]


async def test_nonzero_exit_code():
    r = await ProcessRunner(ENV).run(["sh", "-c", "echo boom; exit 3"])
    assert r.exit_code == 3 and not r.ok and r.tail == ["boom"]


async def test_tool_not_found():
    with pytest.raises(ToolNotFound):
        await ProcessRunner(ENV).run(["definitely-not-a-tool-xyz"])


async def test_own_process_group_and_starttime():
    seen = {}

    def on_start(res):
        seen["pid"], seen["pgid"], seen["st"] = res.pid, os.getpgid(res.pid), res.starttime

    r = await ProcessRunner(ENV).run(["sleep", "0.2"], on_start=on_start)
    assert seen["pid"] == seen["pgid"] == r.pgid      # session/group leader
    assert seen["pgid"] != os.getpgid(0)
    assert isinstance(seen["st"], int)


async def test_timeout_kills_whole_group(reap_strays):
    """sh ignores TERM, its child sleeps; both must be gone after timeout + grace."""
    child = {}

    def on_line(line):
        child["pid"] = int(line)

    t0 = time.monotonic()
    r = await ProcessRunner(ENV).run(
        ["sh", "-c", "trap '' TERM; sleep 300 & echo $!; wait"],
        timeout=0.5, timeout_grace=0.5, on_line=on_line)
    reap_strays.append(child["pid"])
    assert r.timed_out and r.exit_code == TIMEOUT_EXIT
    assert time.monotonic() - t0 < 5
    await asyncio.sleep(0.1)
    assert pid_gone(child["pid"])
    assert not group_alive(r.pgid)


async def test_kill_all_cancels_running_processes(reap_strays):
    runner = ProcessRunner(ENV, kill_grace=0.5)
    pids = []

    def collect(line):
        pids.append(int(line))

    tasks = [asyncio.create_task(runner.run(["sh", "-c", "sleep 300 & echo $!; wait"], on_line=collect))
             for _ in range(5)]
    for _ in range(50):
        if len(pids) == 5:
            break
        await asyncio.sleep(0.05)
    reap_strays.extend(pids)
    pgids = runner.live_pgids
    assert len(pgids) == 5
    await runner.kill_all()
    results = await asyncio.gather(*tasks)
    assert all(r.cancelled for r in results)
    assert all(pid_gone(p) for p in pids)
    assert not any(group_alive(g) for g in pgids)
    # a cancelled runner starts nothing new
    r = await runner.run(["true"])
    assert r.cancelled and r.pid is None


async def test_proc_starttime_of_dead_pid_is_none():
    r = await ProcessRunner(ENV).run(["true"])
    await asyncio.sleep(0.05)
    assert proc_starttime(r.pid) is None or not os.path.exists(f"/proc/{r.pid}")
