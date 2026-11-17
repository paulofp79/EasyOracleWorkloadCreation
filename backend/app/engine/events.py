"""Per-run event bus with sequence numbers, a replay buffer and SSE formatting.

Clients first fetch the REST snapshot (which carries `event_seq`), then subscribe with
last_id = event_seq. Events after that are replayed from the ring buffer; if the gap is
older than the buffer, the client gets `resync` and refetches the snapshot.
"""
from __future__ import annotations

import asyncio
import collections
import json
from typing import Any, AsyncIterator

BUFFER = 2000
KEEPALIVE_S = 15.0


def sse(seq: int | None, event: str, data: Any) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class EventBus:
    def __init__(self, run_id: str, buffer: int = BUFFER):
        self.run_id = run_id
        self.seq = 0
        self.buffer: collections.deque[tuple[int, str, Any]] = collections.deque(maxlen=buffer)
        self._subs: set[asyncio.Queue] = set()
        self.closed = False

    def publish(self, event: str, data: Any) -> int:
        self.seq += 1
        ev = (self.seq, event, data)
        self.buffer.append(ev)
        for q in self._subs:
            q.put_nowait(ev)
        return self.seq

    def close(self) -> None:
        self.closed = True
        for q in self._subs:
            q.put_nowait(None)

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    async def stream(self, last_id: int | None, keepalive: float = KEEPALIVE_S) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)          # subscribe before replaying so nothing falls in between
        try:
            sent = self.seq if last_id is None else last_id
            if last_id is not None and last_id < self.seq:
                oldest = self.buffer[0][0] if self.buffer else self.seq + 1
                if last_id + 1 < oldest:
                    yield sse(None, "resync", {"seq": self.seq})
                    sent = self.seq
                else:
                    for ev in list(self.buffer):
                        if ev[0] > last_id:
                            yield sse(*ev)
                            sent = ev[0]
            if self.closed:
                yield sse(None, "end", {"seq": self.seq})
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev is None:
                    yield sse(None, "end", {"seq": self.seq})
                    return
                if ev[0] <= sent:
                    continue
                sent = ev[0]
                yield sse(*ev)
        finally:
            self._subs.discard(q)
