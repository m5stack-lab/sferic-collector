"""Store.search — local full-text recall over strikes."""

from sferic_collector.models import Strike, now_iso, strike_text
from sferic_collector.store import Store


def _strike(sid, station="stick-1", toolset="wifi", kind="scan", flash=None, **decoded):
    return Strike(
        id=sid,
        station=station,
        flash=flash,
        ts=now_iso(),
        toolset=toolset,
        kind=kind,
        payload={"decoded": decoded},
        meta={"tool": f"{toolset}.{kind}"},
    )


def test_strike_text_flattens_payload():
    s = _strike("a", ssid="HomeNet", channel=6, security="WPA2", rssi=-42)
    txt = strike_text(s)
    assert "wifi" in txt and "HomeNet" in txt and "channel 6" in txt and "-42" in txt


def test_search_matches_payload_term(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("a", ssid="HomeNet", security="WPA2"))
    db.add_strike(_strike("b", ssid="Cafe", security="OPEN"))
    hits = db.search("HomeNet")
    assert [h.id for h in hits] == ["a"]


def test_search_ranks_stronger_match_first(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("weak", kind="decode", protocol="NEC", note="saw rolling once"))
    db.add_strike(
        _strike("strong", kind="decode", protocol="rolling", note="rolling code rolling frame")
    )
    hits = db.search("rolling")
    assert [h.id for h in hits][:2] == ["strong", "weak"]


def test_search_toolset_filter(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("w", toolset="wifi", kind="scan", ssid="shared-name"))
    db.add_strike(_strike("b", toolset="ble", kind="scan", name="shared-name"))
    assert [h.id for h in db.search("shared-name", toolset="ble")] == ["b"]


def test_search_station_and_kind_filters(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("a", station="stick-1", kind="monitor", rate=99, note="busy channel"))
    db.add_strike(_strike("b", station="stick-2", kind="monitor", rate=1, note="busy channel"))
    db.add_strike(_strike("c", station="stick-1", kind="scan", note="busy channel"))
    assert [h.id for h in db.search("busy", station="stick-1", kind="monitor")] == ["a"]


def test_search_no_match_is_empty(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("a", ssid="HomeNet"))
    assert db.search("nonexistent-token") == []


def test_reindex_on_replace(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("a", ssid="OldName"))
    db.add_strike(_strike("a", ssid="NewName"))  # same id, replaced
    assert db.search("OldName") == []
    assert [h.id for h in db.search("NewName")] == ["a"]


def test_punctuation_in_query_is_safe(tmp_path):
    db = Store(tmp_path / "t.db")
    db.add_strike(_strike("a", value="0x20DF10EF", protocol="NEC"))
    # a query full of FTS metacharacters must not raise the parser
    assert db.search('"quote AND* (paren) -minus:') == []
    assert [h.id for h in db.search("NEC 0x20DF10EF")] == ["a"]
