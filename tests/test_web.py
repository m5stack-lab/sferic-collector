"""The collector HTTP + WS surface (web.make_app): the station endpoint
(ADR-0009) plus the read-only dashboard + JSON APIs (ADR-0007 §3), via
Starlette TestClient."""

import pytest
from starlette.testclient import TestClient

from sferic_collector.collector import Collector
from sferic_collector.models import Strike, now_iso
from sferic_collector.sim import SimulatedStation
from sferic_collector.store import Store
from sferic_collector.transport import Fleet
from sferic_collector.web import make_app


def _collector(tmp_path) -> Collector:
    return Collector(store=Store(tmp_path / "t.db"), fleet=Fleet())


def test_hello_then_welcome_and_fleet_registration(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens={"s3cr3t"}))
    with client.websocket_connect("/station", headers={"authorization": "Bearer s3cr3t"}) as ws:
        ws.send_json(
            {
                "type": "hello",
                "token": "s3cr3t",
                "station": {
                    "id": "stick-net1",
                    "model": "m5stack-sticks3",
                    "toolsets": ["wifi", "sensors"],
                    "radios": ["wifi-2g4"],
                },
            }
        )
        assert ws.receive_json()["type"] == "welcome"
        st = c.fleet.get("stick-net1")
        assert st is not None and st.kind == "ws"
        assert st.model == "m5stack-sticks3"

    # socket closed -> removed from the fleet
    assert c.fleet.get("stick-net1") is None


def test_bad_token_is_rejected(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens={"good"}))
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on the 4001 close
        with client.websocket_connect("/station", headers={"authorization": "Bearer wrong"}) as ws:
            ws.send_json({"type": "hello", "station": {"id": "x", "toolsets": []}})
            ws.receive_json()
    assert c.fleet.all() == []


def test_hello_token_accepted_when_no_header(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens={"tok"}))
    with client.websocket_connect("/station") as ws:
        ws.send_json(
            {"type": "hello", "token": "tok", "station": {"id": "stick-h", "toolsets": ["ir"]}}
        )
        assert ws.receive_json()["type"] == "welcome"
        assert c.fleet.get("stick-h") is not None


def test_dev_mode_no_tokens_accepts_any(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens=set()))
    with client.websocket_connect("/station") as ws:
        ws.send_json({"type": "hello", "station": {"id": "stick-open", "toolsets": []}})
        assert ws.receive_json()["type"] == "welcome"


def test_first_frame_must_be_hello(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens=set()))
    with pytest.raises(Exception):  # noqa: B017 - server closes on protocol error
        with client.websocket_connect("/station") as ws:
            ws.send_json({"type": "cmd", "id": 1, "op": "status"})
            ws.receive_json()
    assert c.fleet.all() == []


def test_health_lists_stations(tmp_path):
    c = _collector(tmp_path)
    client = TestClient(make_app(c, tokens=set()))
    assert client.get("/health").json() == {"stations": []}


# --- dashboard + JSON API (ADR-0007 §3) ---


def test_dashboard_page_served(tmp_path):
    client = TestClient(make_app(_collector(tmp_path), tokens=set()))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "sferic collector" in r.text


def test_api_stations_lists_fleet(tmp_path):
    fleet = Fleet()
    fleet.add(SimulatedStation("sim-1"))
    c = Collector(store=Store(tmp_path / "t.db"), fleet=fleet)
    client = TestClient(make_app(c, tokens=set()))
    rows = client.get("/api/stations").json()
    assert rows[0]["id"] == "sim-1"
    assert rows[0]["transport"] == "sim"
    assert rows[0]["toolsets"] == ["sensors"]


def _seed_strike(store, **kw):
    s = Strike(
        id=kw.get("id", "s1"),
        station=kw.get("station", "stick-1"),
        flash=kw.get("flash"),
        ts=now_iso(),
        toolset=kw.get("toolset", "wifi"),
        kind=kw.get("kind", "scan"),
        payload={"decoded": kw.get("decoded", {"ssid": "HomeNet"})},
        meta={"tool": "wifi.scan"},
    )
    store.add_strike(s)
    return s


def test_api_strikes_and_recall(tmp_path):
    c = _collector(tmp_path)
    _seed_strike(c.store, id="a", decoded={"ssid": "HomeNet", "channel": 6})
    _seed_strike(c.store, id="b", decoded={"ssid": "Cafe"})
    client = TestClient(make_app(c, tokens=set()))

    assert {r["id"] for r in client.get("/api/strikes").json()} == {"a", "b"}
    hit = client.get("/api/recall", params={"q": "HomeNet"}).json()
    assert [r["id"] for r in hit] == ["a"]
    assert client.get("/api/recall", params={"q": "  "}).json() == []


def test_api_flashes_empty(tmp_path):
    client = TestClient(make_app(_collector(tmp_path), tokens=set()))
    assert client.get("/api/flashes").json() == []
