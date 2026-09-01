"""Dispatch core: capability gating + strike wrapping. Transport-agnostic and
MCP-agnostic so it is unit-testable without either.
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import Strike, now_iso
from .store import Store
from .transport import Fleet, StationError, StationToolError

# tool -> strike `kind` (sub-type within the toolset)
_KIND = {
    "sensors.motion": "motion",
    "sensors.gesture": "gesture",
    "wifi.scan": "scan",
    "ble.scan": "scan",
}


class CollectorError(Exception):
    """A typed failure surfaced to the MCP caller (ADR-0003 §3 vocabulary):
    station_not_found | capability_not_present | capability_disabled |
    device_only | station_error.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Collector:
    def __init__(self, store: Store | None = None, fleet: Fleet | None = None) -> None:
        self.store = store or Store()
        self.fleet = fleet or Fleet()
        # per-(station, tool) disable set — the MCP mirror of ADR-0004's
        # registry flags. Empty for now; wired to real state later.
        self.disabled: set[tuple[str, str]] = set()

    async def dispatch(
        self, tool: str, station: str, params: dict[str, Any] | None = None
    ) -> Strike:
        toolset = tool.split(".", 1)[0]

        st = self.fleet.get(station)
        if st is None:
            raise CollectorError("station_not_found", f"no station {station!r}")

        cap = await st.capability()
        if toolset not in cap.toolsets:
            raise CollectorError(
                "capability_not_present", f"{station!r} has no {toolset!r} toolset"
            )
        if (station, tool) in self.disabled:
            raise CollectorError("capability_disabled", f"{tool!r} disabled on {station!r}")

        try:
            payload = await st.run(tool, params or {})
        except StationToolError as e:
            # the device already classified it — pass the code straight through
            raise CollectorError(e.code, e.message or e.code) from e
        except StationError as e:
            raise CollectorError("station_error", str(e)) from e

        strike = Strike(
            id=uuid.uuid4().hex,
            station=station,
            ts=now_iso(),
            toolset=toolset,
            kind=_KIND.get(tool, "result"),
            payload={"decoded": payload},
            meta={"tool": tool},
        )
        self.store.add_strike(strike)
        return strike
