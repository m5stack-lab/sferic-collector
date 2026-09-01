"""The collector's link to a station.

ADR-0003 §6 fixes the roles (station is the client, link is persistent) but
not the wire format. This is the seam every transport plugs into:

    SimulatedStation   — in-process fake, for developing with no hardware
    SerialStation      — USB-tethered stick over the '>'/'<' line protocol
    WebSocketStation   — station -> Pi over WiFi/WireGuard        (Pi phase; ADR-0009)
"""

from __future__ import annotations

import abc

from .models import Capability


class StationError(Exception):
    """A transport-level failure — link down, timeout, unparseable frame."""


class StationToolError(Exception):
    """The device rejected a tool call with a typed code (e.g. `device_only`,
    `capability_disabled`). The collector passes the code straight through."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class StationTransport(abc.ABC):
    id: str = "unknown"
    model: str = "unknown"
    fw_version: str = "unknown"

    @abc.abstractmethod
    async def capability(self) -> Capability:
        """The station's declared `{toolsets, radios}` (ADR-0003 §3)."""

    @abc.abstractmethod
    async def run(self, tool: str, params: dict) -> dict:
        """Invoke `<toolset>.<verb>` headlessly; return the payload dict.

        Raise StationToolError(code) for a typed device-side rejection,
        StationError for a transport failure.
        """

    async def close(self) -> None:
        return None


class Fleet:
    """The set of stations the collector currently knows. Lookup is by the
    transport's current `.id`, read each call — a transport whose real id only
    lands after its first `hello` (SerialStation) resolves once `capability()`
    has run (e.g. via `sferic.stations`)."""

    def __init__(self) -> None:
        self._stations: list[StationTransport] = []

    def add(self, t: StationTransport) -> None:
        self._stations.append(t)

    def get(self, station_id: str) -> StationTransport | None:
        return next((t for t in self._stations if t.id == station_id), None)

    def all(self) -> list[StationTransport]:
        return list(self._stations)

    async def close(self) -> None:
        for t in self._stations:
            await t.close()
