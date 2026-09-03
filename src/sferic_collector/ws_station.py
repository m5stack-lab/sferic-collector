"""WebSocket station transport (ADR-0009). The station is the WS *client*
(firmware `sferic-sticks3` `src/ws_station.cpp`); the collector runs the
listener (`ws_server.py`) and wraps each accepted socket in one of these.

Wire format (`sferic` repo `docs/mcp/ws-transport.md`): one JSON object per
text frame, `type` in {hello, welcome, cmd, reply, strike, event, ping,
pong}. Same op vocabulary as the serial link — only the framing differs.

`ws` is any object with async `send_text(str)`, `receive_text() -> str`,
`close()` — a Starlette `WebSocket` in production, a fake in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from .models import Capability
from .transport import StationError, StationToolError, StationTransport


class WebSocketStation(StationTransport):
    kind = "ws"

    def __init__(self, ws: Any, station_info: dict, rpc_timeout: float = 5.0) -> None:
        self._ws = ws
        self.id = station_info.get("id", "ws:unknown")
        self.model = station_info.get("model", "unknown")
        self.fw_version = station_info.get("fw_version", "unknown")
        self._cap = Capability(
            toolsets=station_info.get("toolsets", []),
            radios=station_info.get("radios", []),
        )
        self.online = True
        self._rpc_timeout = rpc_timeout
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._stream_buf: list[dict] | None = None
        self._closed = asyncio.Event()
        self._reader: asyncio.Task | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        with contextlib.suppress(Exception):
            await self._ws.close()
        self._shutdown()

    def _shutdown(self) -> None:
        if self._closed.is_set():
            return
        self.online = False
        self._closed.set()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(StationError(f"{self.id}: link closed"))
        self._pending.clear()

    async def _read_loop(self) -> None:
        try:
            while True:
                raw = await self._ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._route(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            # WebSocketDisconnect and friends — the link is gone.
            pass
        finally:
            self._shutdown()

    async def _route(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "reply":
            fut = self._pending.pop(msg.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
        elif mtype in ("strike", "event"):
            if self._stream_buf is not None:
                self._stream_buf.append(msg)
        elif mtype == "ping":
            with contextlib.suppress(Exception):
                await self._ws.send_text('{"type":"pong"}')
        # "pong", "hello", "welcome" — nothing to do here.

    # --- rpc ---

    async def _cmd(self, op: str, timeout: float | None = None, **fields) -> dict:
        if self._closed.is_set():
            raise StationError(f"{self.id}: link closed")
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send_text(json.dumps({"type": "cmd", "id": mid, "op": op, **fields}))
        try:
            return await asyncio.wait_for(fut, timeout or self._rpc_timeout)
        except TimeoutError as e:
            self._pending.pop(mid, None)
            raise StationError(f"{self.id}: timed out waiting for reply id {mid}") from e

    # --- StationTransport ---

    async def capability(self) -> Capability:
        return self._cap

    async def run(self, tool: str, params: dict) -> dict:
        params = params or {}
        budget = float(params.get("ms", 4000)) / 1000.0 + 10.0
        msg = await self._cmd("run", budget, tool=tool, params=params)
        if msg.get("ok"):
            return msg.get("result", {})
        raise StationToolError(msg.get("error", "station_error"), msg.get("message", ""))

    async def stream(self, tool: str, params: dict, duration_s: float) -> dict:
        self._stream_buf = []
        try:
            start = await self._cmd("stream_start", 8.0, tool=tool, params=params or {})
            if not start.get("ok"):
                raise StationToolError(
                    start.get("error", "stream_start_failed"), start.get("message", "")
                )
            await asyncio.sleep(duration_s)
            stop = await self._cmd("stream_stop", 8.0)
            strikes = [s for s in self._stream_buf if s.get("type") == "strike"]
            return {
                "flash": start.get("flash"),
                "strikes": strikes,
                "count": stop.get("count", len(strikes)),
            }
        finally:
            self._stream_buf = None

    async def emit_arm(self, tool: str, params: dict) -> dict:
        return await self._cmd("emit_arm", 15.0, tool=tool, params=params or {})

    async def emit_fire(self, token: str) -> dict:
        return await self._cmd("emit_fire", 25.0, token=token)
