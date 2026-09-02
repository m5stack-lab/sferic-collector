"""oui.py / cve.py / enrichment.py against a fake HTTP transport — no real
network calls, no rate limiting waits."""

import asyncio

import httpx
import pytest

from sferic_collector.cve import CveClient, _candidate_slugs, match_product
from sferic_collector.enrichment import Enricher
from sferic_collector.oui import OuiResolver, is_locally_administered


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    """OuiResolver paces requests to ~1/s for the real macvendors.com API;
    skip that here so the suite stays fast against the fake transport."""

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)


_VENDOR_BY_MAC = {
    "10:20:30:00:00:01": "Example Widget Co.,Ltd.",
}
_PRODUCTS_BY_SLUG = {
    "example-widget": ["gizmo-9000", "widget-classic"],
}
_CVES = {
    ("example-widget", "gizmo-9000"): {
        "total_count": 2,
        "results": {"nvd": [["cve-2021-0001", {}], ["cve-2021-0002", {}]]},
    },
}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.macvendors.com" in url:
        mac = url.rsplit("/", 1)[-1]
        vendor = _VENDOR_BY_MAC.get(mac)
        return httpx.Response(200, text=vendor) if vendor else httpx.Response(404)
    if "/browse/" in url:
        slug = url.rsplit("/", 1)[-1]
        products = _PRODUCTS_BY_SLUG.get(slug, [])
        return httpx.Response(200, json=products)
    if "/search/" in url:
        _, slug, product = url.rsplit("/", 2)
        data = _CVES.get((slug, product))
        return httpx.Response(200, json=data) if data else httpx.Response(200, json={})
    return httpx.Response(404)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


# --- oui.py ---


def test_locally_administered_mac_skipped():
    assert is_locally_administered("02:00:00:00:00:01") is True
    assert is_locally_administered("10:20:30:00:00:01") is False


async def test_oui_resolves_known_vendor():
    resolver = OuiResolver(client=_client())
    assert await resolver.vendor_for("10:20:30:00:00:01") == "Example Widget Co.,Ltd."
    await resolver.close()


async def test_oui_unknown_vendor_returns_none():
    resolver = OuiResolver(client=_client())
    assert await resolver.vendor_for("11:22:33:00:00:01") is None
    await resolver.close()


async def test_oui_skips_randomized_mac_without_a_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    resolver = OuiResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await resolver.vendor_for("02:11:22:00:00:01") is None
    assert calls == []
    await resolver.close()


# --- cve.py ---


def test_candidate_slugs_strip_suffix_words():
    assert _candidate_slugs("Example Widget Co.,Ltd.") == [
        "example-widget",
        "examplewidget",
        "example",
    ]


def test_match_product_requires_specific_substring():
    products = ["gizmo-9000", "widget-classic"]
    assert match_product(products, "Gizmo 9000 - Guest") == "gizmo-9000"
    assert match_product(products, "tp") is None  # too short to mean anything
    assert match_product(products, None) is None


async def test_vendor_info_probes_candidates_and_caches():
    client = _client()
    cve = CveClient(client=client)
    info = await cve.vendor_info("Example Widget Co.,Ltd.")
    assert info.slug == "example-widget"
    assert "gizmo-9000" in info.products

    # second call must hit the cache, not issue another request
    calls = []
    orig_get = client.get

    async def counting_get(*a, **kw):
        calls.append(1)
        return await orig_get(*a, **kw)

    client.get = counting_get
    again = await cve.vendor_info("Example Widget Co.,Ltd.")
    assert again is info or again.slug == info.slug
    assert calls == []
    await cve.close()


async def test_vendor_info_no_candidate_matches():
    cve = CveClient(client=_client())
    info = await cve.vendor_info("Totally Unknown Corp")
    assert info.slug is None
    assert info.products == []
    await cve.close()


async def test_search_returns_total_and_ids():
    cve = CveClient(client=_client())
    result = await cve.search("example-widget", "gizmo-9000")
    assert result["total"] == 2
    assert result["ids"] == ["CVE-2021-0001", "CVE-2021-0002"]
    await cve.close()


# --- enrichment.py ---


async def test_enrich_wifi_scan_attaches_security_block():
    e = Enricher(oui=OuiResolver(client=_client()), cve=CveClient(client=_client()))
    payload = {
        "aps": [
            {"ssid": "Gizmo 9000", "bssid": "10:20:30:00:00:01"},
            {"ssid": "Unknown AP", "bssid": "11:22:33:00:00:01"},
        ]
    }
    await e.enrich("wifi.scan", payload)

    matched, unknown = payload["aps"]
    assert matched["security"]["vendor"] == "Example Widget Co.,Ltd."
    assert matched["security"]["cve_vendor_known"] is True
    assert matched["security"]["matched_product"] == "gizmo-9000"
    assert matched["security"]["cve_total"] == 2
    assert "CVE-2021-0001" in matched["security"]["cve_ids"]

    assert unknown["security"] == {"vendor": None}
    await e.close()


async def test_enrich_omits_security_for_randomized_ble_address():
    """A BLE private-random address carries no IEEE-assigned vendor by
    design -- distinct from a public address we just couldn't resolve."""
    e = Enricher(oui=OuiResolver(client=_client()), cve=CveClient(client=_client()))
    payload = {"devices": [{"name": "Phone", "address": "02:11:22:00:00:01"}]}
    await e.enrich("ble.scan", payload)
    assert "security" not in payload["devices"][0]
    await e.close()


async def test_enrich_ignores_unrelated_tools():
    e = Enricher(oui=OuiResolver(client=_client()), cve=CveClient(client=_client()))
    payload = {"moving": True}
    await e.enrich("sensors.motion", payload)
    assert payload == {"moving": True}
    await e.close()


async def test_enrich_survives_lookup_failure():
    """A network error resolving the vendor degrades to 'vendor unknown'
    rather than losing the strike or raising out of enrich()."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    e = Enricher(
        oui=OuiResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(boom))),
        cve=CveClient(client=httpx.AsyncClient(transport=httpx.MockTransport(boom))),
    )
    payload = {"aps": [{"ssid": "x", "bssid": "10:20:30:00:00:01"}]}
    await e.enrich("wifi.scan", payload)  # must not raise
    assert payload["aps"][0]["security"] == {"vendor": None}
    await e.close()
