"""Dispatch core: capability gating + strike wrapping. Transport-agnostic and
MCP-agnostic so it is unit-testable without either.
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import Flash, Strike, now_iso
from .store import Store
from .transport import Fleet, StationError, StationToolError

# tool -> strike `kind` (sub-type within the toolset)
_KIND = {
    "sensors.motion": "motion",
    "sensors.gesture": "gesture",
    "wifi.scan": "scan",
    "wifi.probe": "probe",
    "ble.scan": "scan",
    "ir.decode": "decode",
    "ir.capture": "learn",
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

    async def stream_dispatch(
        self, tool: str, station: str, params: dict[str, Any] | None, duration_s: float
    ) -> tuple[str, list[Strike]]:
        """Bounded streaming capture: gate on capability, run the session on
        the station, then persist every strike under one flash."""
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
            res = await st.stream(tool, params or {}, duration_s)
        except StationToolError as e:
            raise CollectorError(e.code, e.message or e.code) from e
        except (StationError, NotImplementedError) as e:
            raise CollectorError("station_error", str(e)) from e

        ingest = now_iso()
        flash_id = res.get("flash") or uuid.uuid4().hex[:12]
        strikes: list[Strike] = []
        for i, o in enumerate(res.get("strikes", [])):
            s = Strike(
                id=o.get("id") or f"{flash_id}-{i}",
                station=station,
                flash=flash_id,
                ts=ingest,  # server-normalized; original in meta.device_ts
                toolset=o.get("toolset", toolset),
                kind=o.get("kind", "stream"),
                payload=o.get("payload", {}),
                meta={"tool": tool, "device_ts": o.get("ts")},
            )
            self.store.add_strike(s)
            strikes.append(s)

        self.store.add_flash(
            Flash(
                id=flash_id,
                station=station,
                toolset=toolset,
                label=tool,
                opened_at=ingest,
                closed_at=now_iso(),
            )
        )
        return flash_id, strikes

    async def emit_dispatch(
        self,
        tool: str,
        station: str,
        params: dict[str, Any] | None,
        confirm_token: str | None,
    ) -> dict:
        """Two-step emit (ADR-0008 §1). No token -> arm and return the token,
        nothing fires. With the token -> fire, store an `emit` strike."""
        toolset = tool.split(".", 1)[0]

        st = self.fleet.get(station)
        if st is None:
            raise CollectorError("station_not_found", f"no station {station!r}")
        cap = await st.capability()
        if toolset not in cap.toolsets:
            raise CollectorError(
                "capability_not_present", f"{station!r} has no {toolset!r} toolset"
            )

        try:
            if confirm_token is None:
                r = await st.emit_arm(tool, params or {})
            else:
                r = await st.emit_fire(confirm_token)
        except (StationError, NotImplementedError) as e:
            raise CollectorError("station_error", str(e)) from e

        if not r.get("ok"):
            raise CollectorError(r.get("error", "emit_failed"), r.get("message", ""))

        if confirm_token is None:
            return {
                "armed": True,
                "confirm_token": r["token"],
                "expires_ms": r.get("expires_ms"),
            }

        result = r.get("result", {})
        strike = Strike(
            id=uuid.uuid4().hex,
            station=station,
            ts=now_iso(),
            toolset=toolset,
            kind="emit",
            payload={"decoded": result},
            meta={"tool": tool},
        )
        self.store.add_strike(strike)
        return {"fired": True, "result": result, "strike": strike.model_dump()}
