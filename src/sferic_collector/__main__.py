"""Entry point.

Default: the MCP server over stdio (for `claude mcp add` and other local
MCP clients).

With `SFERIC_WS_PORT` set: also run the HTTP + WebSocket surface (ADR-0009,
ADR-0007 §3) in the same process/event loop — networked stations dial in at
`/station`, and a read-only dashboard is served at `/`.

Env:
  SFERIC_SERIAL_PORT   attach a USB-tethered stick as a station
  SFERIC_WS_PORT       serve the HTTP/WS surface on this port (/station, /, /api/*)
  SFERIC_WS_HOST       bind address (default 0.0.0.0)
  SFERIC_WS_TOKENS     comma-separated accepted per-station bearer tokens;
                       unset/empty accepts any station (dev only)
"""

from __future__ import annotations

import asyncio
import os
import sys

from .collector import Collector
from .server import build_server


def _tokens() -> set[str]:
    return {t.strip() for t in os.environ.get("SFERIC_WS_TOKENS", "").split(",") if t.strip()}


async def _run_with_ws(collector: Collector, port: int) -> None:
    import uvicorn

    from .web import make_app

    tokens = _tokens()
    if not tokens:
        print(
            "[sferic-collector] WS listener: no SFERIC_WS_TOKENS — accepting any station",
            file=sys.stderr,
        )

    app = make_app(collector, tokens)
    config = uvicorn.Config(
        app,
        host=os.environ.get("SFERIC_WS_HOST", "0.0.0.0"),
        port=port,
        log_level="warning",
    )
    ws = uvicorn.Server(config)
    srv = build_server(collector)

    print(
        f"[sferic-collector] http/ws on :{port}  (dashboard /, stations /station)",
        file=sys.stderr,
    )
    ws_task = asyncio.create_task(ws.serve())
    mcp_task = asyncio.create_task(srv.run_stdio_async())
    done, pending = await asyncio.wait({ws_task, mcp_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        t.result()  # surface any exception


def main() -> None:
    port = os.environ.get("SFERIC_WS_PORT")
    if port:
        collector = Collector()
        asyncio.run(_run_with_ws(collector, int(port)))
    else:
        build_server().run()


if __name__ == "__main__":
    main()
