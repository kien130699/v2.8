from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[dict]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        for q in clients:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._clients.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            async with self._lock:
                self._clients.discard(q)


events = EventBus()
