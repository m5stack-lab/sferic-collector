"""USB-serial station transport — the milestone-2 link (firmware:
`sferic-sticks3` `src/serial_control.cpp`, `docs/mcp/serial-transport.md`).

Sends `>{"id":N,"op":...}` lines, reads the matching `<{...}` reply, ignores
everything else on the wire (the station also logs there). Blocking pyserial
I/O is pushed to a worker thread; a lock serializes the request/reply pairs.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import serial

from .models import Capability
from .transport import StationError, StationToolError, StationTransport

DEFAULT_PORT = "/dev/cu.usbmodem1101"
DEFAULT_BAUD = 115200


class SerialStation(StationTransport):
    kind = "serial"

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baud: int = DEFAULT_BAUD,
        rpc_timeout: float = 5.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self.id = f"serial:{port}"  # replaced by the real id after hello()
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._rpc_timeout = rpc_timeout

    # --- transport ---

    def _open(self) -> serial.Serial:
        if self._ser is None or not self._ser.is_open:
            s = serial.Serial(self.port, self.baud, timeout=0.4)
            s.dtr = True  # StickS3 USB-Serial/JTAG only TXes with DTR asserted
            time.sleep(0.3)
            s.reset_input_buffer()
            self._ser = s
        return self._ser

    def _send(self, s, op: str, **fields) -> int:
        self._next_id += 1
        mid = self._next_id
        s.write((">" + json.dumps({"id": mid, "op": op, **fields}) + "\n").encode())
        s.flush()
        return mid

    def _await_reply(self, s, mid: int, deadline: float, sink=None) -> dict:
        """Read lines until the `<` reply with id `mid`. `!` lines go to
        `sink(obj)` if given, else are ignored (as is log noise). Any `!`
        lines already buffered *after* the matched reply are still routed to
        the sink before returning."""

        def route_bang(raw: str) -> None:
            if sink is not None and raw.startswith("!"):
                try:
                    sink(json.loads(raw[1:]))
                except json.JSONDecodeError:
                    pass

        buf = b""
        while time.monotonic() < deadline:
            chunk = s.read(s.in_waiting or 1)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                raw = line.decode("utf-8", "replace").strip()
                if raw.startswith("<"):
                    try:
                        msg = json.loads(raw[1:])
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == mid:
                        for rest in buf.split(b"\n")[:-1]:
                            route_bang(rest.decode("utf-8", "replace").strip())
                        return msg
                else:
                    route_bang(raw)
        raise StationError(f"{self.port}: timed out waiting for reply id {mid}")

    def _rpc(self, op: str, timeout: float | None = None, **fields) -> dict:
        with self._lock:
            s = self._open()
            mid = self._send(s, op, **fields)
            deadline = time.monotonic() + (timeout or self._rpc_timeout)
            return self._await_reply(s, mid, deadline)

    def _stream_blocking(self, tool: str, params: dict, duration_s: float) -> dict:
        with self._lock:
            s = self._open()
            strikes: list[dict] = []

            def sink(obj):
                if "event" not in obj:  # skip open/close control lines
                    strikes.append(obj)

            start = self._await_reply(
                s,
                self._send(s, "stream_start", tool=tool, params=params),
                time.monotonic() + 8.0,
                sink=sink,
            )
            if not start.get("ok"):
                raise StationToolError(
                    start.get("error", "stream_start_failed"), start.get("message", "")
                )

            buf = b""
            deadline = time.monotonic() + duration_s
            while time.monotonic() < deadline:
                buf += s.read(s.in_waiting or 1)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    raw = line.decode("utf-8", "replace").strip()
                    if raw.startswith("!"):
                        try:
                            sink(json.loads(raw[1:]))
                        except json.JSONDecodeError:
                            pass

            stop = self._await_reply(
                s, self._send(s, "stream_stop"), time.monotonic() + 8.0, sink=sink
            )
            return {
                "flash": start.get("flash"),
                "strikes": strikes,
                "count": stop.get("count", len(strikes)),
            }

    # --- StationTransport ---

    async def capability(self) -> Capability:
        msg = await asyncio.to_thread(self._rpc, "hello")
        st = msg.get("station", {})
        self.id = st.get("id", self.id)
        self.model = st.get("model", "unknown")
        self.fw_version = st.get("fw_version", "unknown")
        return Capability(toolsets=st.get("toolsets", []), radios=st.get("radios", []))

    async def run(self, tool: str, params: dict) -> dict:
        params = params or {}
        # a scan/decode run blocks the firmware for its `ms` window — give the
        # reply that long plus generous slack for stack + serialization.
        budget = float(params.get("ms", 4000)) / 1000.0 + 10.0
        msg = await asyncio.to_thread(self._rpc, "run", budget, tool=tool, params=params)
        if msg.get("ok"):
            return msg.get("result", {})
        raise StationToolError(msg.get("error", "station_error"), msg.get("message", ""))

    async def stream(self, tool: str, params: dict, duration_s: float) -> dict:
        return await asyncio.to_thread(self._stream_blocking, tool, params or {}, duration_s)

    async def emit_arm(self, tool: str, params: dict) -> dict:
        return await asyncio.to_thread(self._rpc, "emit_arm", 15.0, tool=tool, params=params or {})

    async def emit_fire(self, token: str) -> dict:
        # a TV-B-Gone sweep blocks the firmware ~5s; HID types — allow slack.
        return await asyncio.to_thread(self._rpc, "emit_fire", 25.0, token=token)

    async def close(self) -> None:
        if self._ser is not None and self._ser.is_open:
            await asyncio.to_thread(self._ser.close)
