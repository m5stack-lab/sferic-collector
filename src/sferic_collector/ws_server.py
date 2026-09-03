"""The collector's WebSocket listener (ADR-0009). Stations dial in to
`ws://<collector>:<port>/station`; each accepted socket becomes a
`WebSocketStation` in the fleet for as long as it stays connected.

Runs on Starlette (already a transitive dep via `mcp`), so there is no new
package and it shares a stack with the future dashboard (ADR-0007 §3).
"""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .collector import Collector
from .ws_station import WebSocketStation

_HELLO_TIMEOUT_S = 10.0


def _bearer(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


def make_ws_app(collector: Collector, tokens: set[str] | None = None) -> Starlette:
    """`tokens` is the set of accepted per-station bearer tokens. An empty /
    None set accepts any station (dev only) — the caller logs the warning."""
    allow = tokens or set()

    async def station_endpoint(ws: WebSocket) -> None:
        await ws.accept()

        # Auth: header first (ADR-0009 §6), else hello.token. If a token set
        # is configured, one of them must match.
        header_tok = _bearer(ws)

        try:
            first = json.loads(await ws.receive_text())
        except (WebSocketDisconnect, json.JSONDecodeError):
            await ws.close(code=1002)
            return

        if first.get("type") != "hello":
            await ws.close(code=1002)  # protocol error — hello must be first
            return

        tok = header_tok or first.get("token")
        if allow and tok not in allow:
            await ws.close(code=4001)  # unauthorized (ADR-0009 §3)
            return

        info = first.get("station", {})
        if not info.get("id"):
            await ws.close(code=1002)
            return

        station = WebSocketStation(ws, info)
        await station.start()
        collector.fleet.add(station)
        await ws.send_text(json.dumps({"type": "welcome", "collector": "sferic-collector"}))

        try:
            await station.wait_closed()
        finally:
            collector.fleet.remove(station.id)
            await station.close()

    async def health(_req):
        return JSONResponse({"stations": [s.id for s in collector.fleet.all()]})

    return Starlette(
        routes=[
            WebSocketRoute("/station", station_endpoint),
            Route("/health", health),
        ]
    )
