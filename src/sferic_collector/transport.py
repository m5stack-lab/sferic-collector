"""The collector's link to a station.

ADR-0003 §6 fixes the roles (station is the client, link is persistent) but
not the wire format. This is the seam every transport plugs into:

    SimulatedStation   — in-process fake, for developing with no hardware
    SerialStation      — USB-tethered stick on a dev desk        (next)
    WebSocketStation   — station -> Pi over WiFi/WireGuard        (Pi phase; ADR-0009)
"""

from __future__ import annotations

import abc

from .models import Capability


class StationError(Exception):
    """A device-side failure while running a tool."""


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

        Raise StationError on a device-side failure.
        """

    async def close(self) -> None:
        return None


class Fleet:
    """The set of stations the collector currently knows."""

    def __init__(self) -> None:
        self._by_id: dict[str, StationTransport] = {}

    def add(self, t: StationTransport) -> None:
        self._by_id[t.id] = t

    def get(self, station_id: str) -> StationTransport | None:
        return self._by_id.get(station_id)

    def all(self) -> list[StationTransport]:
        return list(self._by_id.values())

    async def close(self) -> None:
        for t in self._by_id.values():
            await t.close()
