"""MAC OUI -> vendor name resolution via the public macvendors.com API
(ADR-0010, sferic-sticks3 repo).

The IEEE OUI registry is ~40k entries and grows continuously, so this
resolves live against a public mirror instead of bundling a static table,
and caches per-OUI (the first 3 octets) since that's the actual assignment
granularity -- every device sharing a manufacturer's block hits the cache.
"""

from __future__ import annotations

import asyncio
import time

import httpx

_API = "https://api.macvendors.com/{mac}"
_TTL_S = 7 * 24 * 3600  # OUI assignments are effectively static
_MIN_INTERVAL_S = 1.05  # macvendors.com free tier is ~1 req/s


def is_locally_administered(mac: str) -> bool:
    """Locally-administered / randomized MACs (U/L bit set on the first
    octet) carry no IEEE-assigned vendor. Common for BLE private-random
    addresses and WiFi MAC-randomized probe requests -- skip the lookup
    rather than querying a random address."""
    try:
        first_octet = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return True
    return bool(first_octet & 0x02)


class OuiResolver:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._cache: dict[str, tuple[float, str | None]] = {}  # oui -> (expiry, vendor)
        self._throttle = asyncio.Lock()
        self._last_call = 0.0

    async def vendor_for(self, mac: str) -> str | None:
        if not mac or is_locally_administered(mac):
            return None
        oui = mac.upper().replace("-", ":")[:8]  # "AA:BB:CC"
        now = time.monotonic()
        cached = self._cache.get(oui)
        if cached and cached[0] > now:
            return cached[1]

        async with self._throttle:
            wait = _MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
            try:
                resp = await self._client.get(_API.format(mac=mac))
            except httpx.HTTPError:
                return None  # transient failure -- don't cache, retry later

        vendor = resp.text.strip() if resp.status_code == 200 else None
        self._cache[oui] = (time.monotonic() + _TTL_S, vendor)
        return vendor

    async def close(self) -> None:
        await self._client.aclose()
