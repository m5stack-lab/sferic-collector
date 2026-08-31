from sferic_collector.models import Flash, Strike, now_iso
from sferic_collector.store import Store


def test_add_and_query_strike(tmp_path):
    st = Store(tmp_path / "t.db")
    s = Strike(id="a1", station="sim-1", ts=now_iso(), toolset="sensors", kind="motion")
    st.add_strike(s)
    got = st.strikes()
    assert len(got) == 1
    assert got[0].id == "a1"
    assert got[0].toolset == "sensors"


def test_strike_station_filter(tmp_path):
    st = Store(tmp_path / "t.db")
    st.add_strike(Strike(id="a", station="sim-1", ts=now_iso(), toolset="sensors", kind="motion"))
    st.add_strike(Strike(id="b", station="sim-2", ts=now_iso(), toolset="sensors", kind="motion"))
    assert {s.id for s in st.strikes(station="sim-1")} == {"a"}
    assert len(st.strikes()) == 2


def test_flash_strike_count(tmp_path):
    st = Store(tmp_path / "t.db")
    st.add_flash(Flash(id="f1", station="sim-1", toolset="ir", opened_at=now_iso()))
    st.add_strike(
        Strike(id="s1", station="sim-1", flash="f1", ts=now_iso(), toolset="ir", kind="nec")
    )
    st.add_strike(
        Strike(id="s2", station="sim-1", flash="f1", ts=now_iso(), toolset="ir", kind="nec")
    )
    assert st.flashes()[0].strike_count == 2
