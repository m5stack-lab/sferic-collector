"""The collector's HTTP + WebSocket surface (ADR-0009, ADR-0007 §3).

- `WS  /station`   stations dial in; each accepted socket becomes a
                   `WebSocketStation` in the fleet while connected.
- `GET /`          a self-contained read-only dashboard.
- `GET /api/*`     JSON the dashboard polls (stations, strikes, flashes, recall).
- `GET /health`    liveness.

Runs on Starlette (a transitive dep via `mcp`) — no new package.
"""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .collector import Collector
from .web_dashboard import DASHBOARD_HTML
from .ws_station import WebSocketStation


def _bearer(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


async def _stations_payload(collector: Collector) -> list[dict]:
    out: list[dict] = []
    for st in collector.fleet.all():
        cap = await st.capability()
        out.append(
            {
                "id": st.id,
                "model": st.model,
                "fw_version": st.fw_version,
                "transport": getattr(st, "kind", "unknown"),
                "online": getattr(st, "online", True),
                "toolsets": cap.toolsets,
                "radios": cap.radios,
            }
        )
    return out


def make_app(collector: Collector, tokens: set[str] | None = None) -> Starlette:
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

    async def dashboard(_req: Request):
        return HTMLResponse(DASHBOARD_HTML)

    async def api_stations(_req: Request):
        return JSONResponse(await _stations_payload(collector))

    async def api_strikes(req: Request):
        q = req.query_params
        limit = int(q.get("limit", 50))
        rows = collector.store.strikes(q.get("station"), q.get("flash"), limit)
        return JSONResponse([s.model_dump() for s in rows])

    async def api_flashes(req: Request):
        rows = collector.store.flashes(req.query_params.get("station"))
        return JSONResponse([f.model_dump() for f in rows])

    async def api_recall(req: Request):
        q = req.query_params
        query = q.get("q", "")
        if not query.strip():
            return JSONResponse([])
        rows = collector.store.search(
            query,
            station=q.get("station"),
            toolset=q.get("toolset"),
            kind=q.get("kind"),
            limit=int(q.get("limit", 20)),
        )
        return JSONResponse([s.model_dump() for s in rows])

    async def health(_req: Request):
        return JSONResponse({"stations": [s.id for s in collector.fleet.all()]})

    return Starlette(
        routes=[
            WebSocketRoute("/station", station_endpoint),
            Route("/", dashboard),
            Route("/api/stations", api_stations),
            Route("/api/strikes", api_strikes),
            Route("/api/flashes", api_flashes),
            Route("/api/recall", api_recall),
            Route("/health", health),
        ]
    )
