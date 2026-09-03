"""A stand-in for the `sferic-ws` firmware: connects to a running collector's
WebSocket listener and behaves like a station — declares a capability set,
answers `run` / `emit_*` with canned data, and streams synthetic strikes for
`stream_*`. Same wire format as `docs/mcp/ws-transport.md`.

Use it to exercise the collector, the dashboard, and capability routing with
no hardware:

    uv run sferic-collector  # with SFERIC_WS_PORT=8787 SFERIC_WS_TOKENS=tok
    uv run sferic-fake-station --token tok --id stick-fake1
    uv run sferic-fake-station --token tok --id tab5-fake --toolsets sensors,ble

`run_station()` is also what `tests/test_ws_e2e.py` drives in-process.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import time

import websockets

_BOOT = time.monotonic()


def _t() -> str:
    return f"t+{int((time.monotonic() - _BOOT) * 1000)}"


def _canned_run(tool: str, params: dict) -> dict:
    if tool in ("sensors.motion", "sensors.gesture"):
        return {
            "ax": round(random.uniform(-0.05, 0.05), 3),
            "ay": round(random.uniform(-0.05, 0.05), 3),
            "az": round(1.0 + random.uniform(-0.02, 0.02), 3),
            "gyro_dps": round(random.uniform(0, 3), 1),
            "moving": False,
            "face": "flat",
        }
    if tool == "wifi.scan":
        n = random.randint(3, 9)
        return {
            "count": n,
            "aps": [
                {"ssid": f"net-{i}", "rssi": -40 - i * 5, "channel": 1 + i % 11, "sec": "WPA2"}
                for i in range(n)
            ],
        }
    if tool == "ble.scan":
        n = random.randint(2, 6)
        return {"count": n, "devices": [{"name": f"dev-{i}", "rssi": -55 - i} for i in range(n)]}
    if tool == "ir.decode":
        return {"protocol": "NEC", "value": "0x20DF10EF", "bits": 32}
    return {"ok": True, "tool": tool, "params": params}


def _strike_payload(toolset: str) -> dict:
    if toolset == "wifi":
        return {
            "total": random.randint(20, 200),
            "rate": random.randint(1, 90),
            "rssi": -random.randint(40, 90),
        }
    if toolset == "ble":
        return {"adv": random.randint(1, 40), "churn": random.random()}
    return {"peak_hz": random.randint(200, 4000), "mag": random.random()}


class _Session:
    """One connected fake station."""

    def __init__(self, ws, station_id: str, toolsets: list[str], model: str) -> None:
        self.ws = ws
        self.id = station_id
        self.toolsets = toolsets
        self.model = model
        self._stream: asyncio.Task | None = None
        self._stream_flash: str | None = None
        self._stream_count = 0

    async def send(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj))

    async def hello(self) -> None:
        await self.send(
            {
                "type": "hello",
                "station": {
                    "id": self.id,
                    "model": self.model,
                    "fw_version": "0.0.0-fake",
                    "toolsets": self.toolsets,
                    "radios": [],
                },
            }
        )

    async def handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "ping":
            await self.send({"type": "pong"})
            return
        if mtype != "cmd":
            return

        op, mid = msg.get("op"), msg.get("id")
        if op == "run":
            await self.send(
                {
                    "type": "reply",
                    "id": mid,
                    "ok": True,
                    "result": _canned_run(msg.get("tool", ""), msg.get("params") or {}),
                }
            )
        elif op == "stream_start":
            await self._start_stream(mid, msg.get("tool", "wifi.monitor"))
        elif op == "stream_stop":
            await self._stop_stream(mid)
        elif op == "emit_arm":
            await self.send(
                {"type": "reply", "id": mid, "ok": True, "token": "faketok", "expires_ms": 60000}
            )
        elif op == "emit_fire":
            await self.send({"type": "reply", "id": mid, "ok": True, "result": {"fired": True}})
        elif op == "status":
            await self.send(
                {"type": "reply", "id": mid, "ok": True, "uptime_s": int(time.monotonic())}
            )
        else:
            await self.send({"type": "reply", "id": mid, "ok": False, "error": "unknown_op"})

    async def _start_stream(self, mid, tool: str) -> None:
        if self._stream is not None:
            await self.send({"type": "reply", "id": mid, "ok": False, "error": "busy"})
            return
        toolset = tool.split(".", 1)[0]
        self._stream_flash = f"{random.randrange(16**6):06x}"
        self._stream_count = 0
        await self.send({"type": "reply", "id": mid, "ok": True, "flash": self._stream_flash})
        await self.send(
            {"type": "event", "flash": self._stream_flash, "event": "open", "tool": tool}
        )
        self._stream = asyncio.create_task(self._emit_loop(toolset))

    async def _emit_loop(self, toolset: str) -> None:
        try:
            while True:
                await asyncio.sleep(0.3)
                await self.send(
                    {
                        "type": "strike",
                        "v": 1,
                        "flash": self._stream_flash,
                        "id": f"{self._stream_flash}-{self._stream_count}",
                        "station": self.id,
                        "ts": _t(),
                        "toolset": toolset,
                        "kind": "monitor",
                        "payload": {"decoded": _strike_payload(toolset)},
                    }
                )
                self._stream_count += 1
        except asyncio.CancelledError:
            raise

    async def _stop_stream(self, mid) -> None:
        active = self._stream is not None
        if self._stream is not None:
            self._stream.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream
            self._stream = None
        await self.send(
            {
                "type": "event",
                "flash": self._stream_flash,
                "event": "close",
                "count": self._stream_count,
            }
        )
        await self.send(
            {
                "type": "reply",
                "id": mid,
                "ok": active,
                "count": self._stream_count,
                **({} if active else {"error": "not_streaming"}),
            }
        )

    async def close(self) -> None:
        if self._stream is not None:
            self._stream.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream


async def run_station(
    url: str,
    token: str | None = None,
    station_id: str = "stick-fake1",
    toolsets: list[str] | None = None,
    model: str = "m5stack-sticks3",
    reconnect: bool = False,
) -> None:
    """Connect and serve until cancelled. With `reconnect`, retry on drop."""
    toolsets = toolsets or ["wifi", "ble", "sensors"]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    while True:
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                sess = _Session(ws, station_id, toolsets, model)
                await sess.hello()
                try:
                    async for raw in ws:
                        await sess.handle(json.loads(raw))
                finally:
                    await sess.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - dev tool, log and maybe retry
            print(f"[fake-station] {station_id}: {e}")
        if not reconnect:
            return
        await asyncio.sleep(2.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fake sferic station over WebSocket.")
    ap.add_argument("url", nargs="?", default="ws://127.0.0.1:8787/station")
    ap.add_argument("--token")
    ap.add_argument("--id", default="stick-fake1")
    ap.add_argument("--model", default="m5stack-sticks3")
    ap.add_argument(
        "--toolsets",
        default="wifi,ble,sensors",
        help="comma-separated (wifi,ble,ir,sensors,subghz,nrf24)",
    )
    ap.add_argument("--no-reconnect", action="store_true")
    a = ap.parse_args()
    toolsets = [t.strip() for t in a.toolsets.split(",") if t.strip()]
    print(f"[fake-station] {a.id} -> {a.url}  toolsets={toolsets}")
    try:
        asyncio.run(
            run_station(a.url, a.token, a.id, toolsets, a.model, reconnect=not a.no_reconnect)
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
