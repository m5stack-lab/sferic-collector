"""Strike enrichment (ADR-0010): attaches a best-effort vendor + CVE-lead
block to each item in a `wifi.scan` / `ble.scan` result at ingest, using
oui.py + cve.py. Runs inline in Collector.dispatch; any lookup failure
degrades to no enrichment rather than failing the strike -- this is a
security *lead*, never a gate on capturing the scan itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .cve import CveClient, match_product
from .oui import OuiResolver, is_locally_administered

log = logging.getLogger(__name__)

# tool -> (list field, mac field, label field) within the decoded payload
_TARGETS = {
    "wifi.scan": ("aps", "bssid", "ssid"),
    "ble.scan": ("devices", "address", "name"),
}


class Enricher:
    def __init__(self, oui: OuiResolver | None = None, cve: CveClient | None = None) -> None:
        self.oui = oui or OuiResolver()
        self.cve = cve or CveClient()

    async def enrich(self, tool: str, payload: dict[str, Any]) -> None:
        """Mutates `payload` in place, adding a `security` block to each
        scanned item. No-op for tools this module doesn't cover."""
        target = _TARGETS.get(tool)
        if target is None:
            return
        list_key, mac_key, label_key = target
        items = payload.get(list_key)
        if not isinstance(items, list):
            return
        await asyncio.gather(
            *(self._enrich_one(it, mac_key, label_key) for it in items if isinstance(it, dict))
        )

    async def _enrich_one(self, item: dict, mac_key: str, label_key: str) -> None:
        mac = item.get(mac_key)
        if not mac:
            return
        if is_locally_administered(mac):
            # a randomized/private address (common for BLE) carries no
            # IEEE-assigned vendor by design -- omit `security` entirely
            # rather than report a misleading "vendor unknown".
            return
        try:
            vendor = await self.oui.vendor_for(mac)
        except Exception:
            log.warning("oui lookup failed for %s", mac, exc_info=True)
            return

        if not vendor:
            item["security"] = {"vendor": None}
            return

        sec: dict[str, Any] = {"vendor": vendor}
        try:
            info = await self.cve.vendor_info(vendor)
        except Exception:
            log.warning("cve vendor lookup failed for %r", vendor, exc_info=True)
            item["security"] = sec
            return

        sec["cve_vendor_known"] = info.slug is not None
        if info.slug:
            product = match_product(info.products, item.get(label_key))
            if product:
                try:
                    result = await self.cve.search(info.slug, product)
                except Exception:
                    log.warning("cve search failed for %s/%s", info.slug, product, exc_info=True)
                    result = None
                if result is not None:
                    sec["matched_product"] = product
                    sec["cve_total"] = result["total"]
                    sec["cve_ids"] = result["ids"]
            sec["note"] = (
                "product name matched heuristically from the device's own label "
                "-- verify before treating as a confirmed finding"
                if sec.get("matched_product")
                else "vendor known to cve-search, but nothing in the scan matched "
                "a specific product -- no version/firmware fingerprinting yet"
            )
        item["security"] = sec

    async def close(self) -> None:
        await self.oui.close()
        await self.cve.close()
