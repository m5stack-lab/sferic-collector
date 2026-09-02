"""Live CVE lookup against CIRCL's cve-search public API
(https://cve.circl.lu), keyed off a vendor name resolved by oui.py
(ADR-0010).

No firmware/version fingerprinting exists yet -- a scan only yields a MAC
and a free-text label (SSID / BLE name) -- so this is a *vendor-level*
heuristic: it confirms cve-search knows the vendor and, when the device's
own label textually matches one of that vendor's known product slugs,
narrows to a product-level CVE count. That match is a lead, not a confirmed
finding, and is always surfaced as such.

cve-search vendor slugs don't line up mechanically with the vendor strings
macvendors.com returns (e.g. "TP-LINK TECHNOLOGIES CO.,LTD." vs. its slug
"tp-link"), and there's no reverse-lookup endpoint. Instead of a hand-curated
mapping table (fragile, silently stale), a handful of slug candidates are
derived from the vendor name and each is *probed* against cve-search; the
first one that returns a non-empty product list wins and is cached. This
self-corrects as cve-search's own vendor list evolves.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx

_BASE = "https://cve.circl.lu/api"
_VENDOR_TTL_S = 24 * 3600
_SEARCH_TTL_S = 24 * 3600
_MIN_LABEL_LEN = 4  # shorter substrings match too much free text to be useful

# cve-search product slugs are routinely suffixed with generic words
# ("archer_ax53_firmware") that will never appear in a device's own SSID/name
# -- stripped before token matching so they don't block an otherwise-good hit.
_PRODUCT_NOISE_WORDS = {
    "firmware", "wireless", "router", "gateway", "adapter", "modem",
    "system", "software", "device", "series",
}
_MIN_SOLE_TOKEN_LEN = 6  # a single surviving token must be this specific to count alone

# Legal-entity noise words stripped before slugging a vendor name, e.g.
# "Amazon Technologies Inc." -> "amazon", "Netgear, Inc." -> "netgear".
_SUFFIX_WORDS = {
    "technologies", "technology", "co", "ltd", "inc", "incorporated",
    "corp", "corporation", "systems", "communications", "communication",
    "networks", "network", "electronics", "electronic", "gmbh", "limited",
    "pty", "srl", "group", "holding", "international", "labs",
    "laboratories", "company", "the",
}


def _candidate_slugs(vendor_raw: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", vendor_raw.lower())
    core = [w for w in words if w not in _SUFFIX_WORDS] or words
    if not core:
        return []
    ordered = ["-".join(core), "".join(core), core[0]]
    seen: list[str] = []
    for c in ordered:
        if c and c not in seen:
            seen.append(c)
    return seen


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@dataclass
class VendorCveInfo:
    vendor_raw: str
    slug: str | None  # resolved cve-search vendor slug; None = no candidate matched
    products: list[str] = field(default_factory=list)


def _product_tokens(product: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", product.lower()) if t not in _PRODUCT_NOISE_WORDS]


def match_product(products: list[str], device_label: str | None) -> str | None:
    """Best-effort match between a scanned device's own label (SSID / BLE
    advertised name) and one of the vendor's known cve-search product
    slugs.

    Real SSIDs and cve-search product slugs each carry their own noise --
    an owner-chosen prefix on the SSID ("MyHomeWifi_Archer_AX53"), a generic
    suffix on the slug ("archer_ax53_firmware") -- so a whole-string
    substring check misses obvious matches. Instead every meaningful token
    of the product slug (noise words stripped) must appear in the label;
    a single surviving token only counts if it's specific enough on its own
    to keep the false-positive rate down on short, generic tokens.
    """
    if not device_label:
        return None
    hay = _normalize_label(device_label)
    if len(hay) < _MIN_LABEL_LEN:
        return None

    best: str | None = None
    best_score = 0
    for p in products:
        tokens = _product_tokens(p)
        if not tokens:
            continue
        if len(tokens) == 1 and len(tokens[0]) < _MIN_SOLE_TOKEN_LEN:
            continue
        if all(len(t) >= 2 and t in hay for t in tokens):
            score = sum(len(t) for t in tokens)
            if score > best_score:
                best, best_score = p, score
    return best


class CveClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=8.0)
        self._vendor_cache: dict[str, tuple[float, VendorCveInfo]] = {}
        self._search_cache: dict[tuple[str, str], tuple[float, dict]] = {}

    async def _get_json(self, path: str) -> dict | list | None:
        try:
            r = await self._client.get(f"{_BASE}{path}")
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def vendor_info(self, vendor_raw: str) -> VendorCveInfo:
        now = time.monotonic()
        cached = self._vendor_cache.get(vendor_raw)
        if cached and cached[0] > now:
            return cached[1]

        info = VendorCveInfo(vendor_raw=vendor_raw, slug=None)
        for candidate in _candidate_slugs(vendor_raw):
            data = await self._get_json(f"/browse/{candidate}")
            if isinstance(data, list) and data:
                info = VendorCveInfo(vendor_raw=vendor_raw, slug=candidate, products=data)
                break
        self._vendor_cache[vendor_raw] = (time.monotonic() + _VENDOR_TTL_S, info)
        return info

    async def search(self, slug: str, product: str) -> dict:
        key = (slug, product)
        now = time.monotonic()
        cached = self._search_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        data = await self._get_json(f"/search/{slug}/{product}")
        result = {"total": 0, "ids": []}
        if isinstance(data, dict):
            result = {"total": data.get("total_count", 0), "ids": _extract_ids(data)}
        self._search_cache[key] = (time.monotonic() + _SEARCH_TTL_S, result)
        return result

    async def close(self) -> None:
        await self._client.aclose()


def _extract_ids(data: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for source in data.get("results", {}).values():
        for entry in source:
            cve_id = entry[0].upper() if entry and entry[0] else None
            if cve_id and cve_id not in seen:
                seen.add(cve_id)
                out.append(cve_id)
    return out[:10]
