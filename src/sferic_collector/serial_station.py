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

    def _rpc(self, op: str, **fields) -> dict:
        with self._lock:
            s = self._open()
            self._next_id += 1
            mid = self._next_id
            s.write((">" + json.dumps({"id": mid, "op": op, **fields}) + "\n").encode())
            s.flush()
            deadline = time.monotonic() + self._rpc_timeout
            while time.monotonic() < deadline:
                raw = s.readline().decode("utf-8", "replace").strip()
                if not raw.startswith("<"):
                    continue  # log line / noise
                try:
                    msg = json.loads(raw[1:])
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == mid:
                    return msg
            raise StationError(f"{self.port}: timed out waiting for reply to {op!r}")

    # --- StationTransport ---

    async def capability(self) -> Capability:
        msg = await asyncio.to_thread(self._rpc, "hello")
        st = msg.get("station", {})
        self.id = st.get("id", self.id)
        self.model = st.get("model", "unknown")
        self.fw_version = st.get("fw_version", "unknown")
        return Capability(toolsets=st.get("toolsets", []), radios=st.get("radios", []))

    async def run(self, tool: str, params: dict) -> dict:
        msg = await asyncio.to_thread(self._rpc, "run", tool=tool, params=params or {})
        if msg.get("ok"):
            return msg.get("result", {})
        raise StationToolError(msg.get("error", "station_error"), msg.get("message", ""))

    async def close(self) -> None:
        if self._ser is not None and self._ser.is_open:
            await asyncio.to_thread(self._ser.close)
