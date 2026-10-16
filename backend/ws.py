from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket

SEND_TIMEOUT_S = 2.0


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        # Serializes snapshot-on-connect against broadcasts so each client sees a gap-free seq stream
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket, snapshot: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        await ws.accept()
        async with self._lock:
            payload = await snapshot()
            await ws.send_text(json.dumps({"type": "snapshot", **payload}, separators=(",", ":")))
            self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, envelope: dict[str, Any]) -> None:
        if not envelope["events"]:
            return
        frame = json.dumps({"type": "batch", **envelope}, separators=(",", ":"))
        async with self._lock:
            clients = list(self._clients)
            results = await asyncio.gather(*(self._send(ws, frame) for ws in clients), return_exceptions=True)
            for ws, outcome in zip(clients, results, strict=True):
                if outcome is not True:
                    self.disconnect(ws)

    @staticmethod
    async def _send(ws: WebSocket, frame: str) -> bool:
        # Slow consumers are dropped rather than allowed to stall the publisher
        await asyncio.wait_for(ws.send_text(frame), SEND_TIMEOUT_S)
        return True
