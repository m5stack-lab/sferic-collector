"""Wire + storage models. Strike / Flash mirror the firmware's living spec
(sferic repo `docs/mcp/strike-format.md`, v1); Capability / StationInfo come
from ADR-0003 §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# Toolset ids are open strings ("wifi" | "ble" | "ir" | "sensors" | "subghz"
# | "nrf24" | ...) — a new device type just declares a different list.


class Capability(BaseModel):
    """What a station can do, declared on connect (ADR-0003 §3)."""

    toolsets: list[str] = Field(default_factory=list)
    radios: list[str] = Field(default_factory=list)


class StationInfo(BaseModel):
    """The collector's view of one station, returned by `sferic.stations`."""

    id: str
    model: str = "unknown"
    fw_version: str = "unknown"
    online: bool = True
    capability: Capability = Field(default_factory=Capability)


class Strike(BaseModel):
    """One captured event (strike-format.md v1). The collector assigns `id`
    and normalizes `ts` to wall-clock on ingest."""

    v: int = 1
    id: str
    station: str
    flash: str | None = None
    ts: str
    toolset: str
    kind: str
    label: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class Flash(BaseModel):
    """A capture session grouping strikes."""

    v: int = 1
    id: str
    station: str
    toolset: str
    label: str | None = None
    opened_at: str
    closed_at: str | None = None
    strike_count: int = 0


class RunResult(BaseModel):
    """What a `<toolset>.<verb>` MCP call returns. On success `strike` holds
    the stored record; on failure `error` is the typed code (ADR-0003 §3)."""

    ok: bool
    strike: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None


def now_iso() -> str:
    """Current wall-clock as ISO 8601 with a trailing Z."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _flatten(obj: Any, _depth: int = 0) -> str:
    """A decoded payload -> a flat "key value key value" string for indexing."""
    if _depth > 6:
        return ""
    if isinstance(obj, dict):
        return " ".join(f"{k} {_flatten(v, _depth + 1)}" for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return " ".join(_flatten(x, _depth + 1) for x in list(obj)[:40])
    if isinstance(obj, bool):
        return "yes" if obj else "no"
    return str(obj)


def strike_text(s: Strike) -> str:
    """The searchable projection of a strike: identity + flattened decoded
    payload. Used by the recall index (`Store.search`) and, later, as the
    document a semantic backend embeds."""
    parts = [s.toolset, s.kind, s.station]
    if s.label:
        parts.append(s.label)
    tool = s.meta.get("tool")
    if isinstance(tool, str):
        parts.append(tool)
    if s.flash:
        parts.append(s.flash)
    decoded = s.payload.get("decoded", s.payload) if isinstance(s.payload, dict) else s.payload
    parts.append(_flatten(decoded))
    return " ".join(p for p in parts if p)
