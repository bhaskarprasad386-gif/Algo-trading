from types import SimpleNamespace
from time import monotonic_ns, time_ns

from app.market_data.contracts import InstrumentKey, InstrumentType, MarketDataRecord
from app.market_data.provider_readiness_bridge import ProviderReadinessBridge


def _record():
    return MarketDataRecord(
        instrument=InstrumentKey("NFO", "NFO", "12345"),
        symbol="NIFTY26NOVFUT",
        instrument_type=InstrumentType.FUTURE,
        timestamp_ns=time_ns(),
        ltp=25000.0,
    )


def test_shoonya_session_ack_is_scoped_to_adapter_epoch_and_subscription_stays_unverified():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 7, True, False, monotonic_ns())
    bridge.shoonya_state("LIVE", None, 7, True, True, monotonic_ns())
    bridge.shoonya_market_event(_record(), 7, True, monotonic_ns())

    snapshot = bridge.snapshot()["shoonya"]
    assert snapshot["generation"] == 7
    assert snapshot["authentication"] == "AUTHENTICATED"
    assert snapshot["authentication_evidence"] == "shoonya_ak_ack"
    assert snapshot["subscription"] == "NOT_VERIFIABLE"
    assert snapshot["feed_ready"] is False
    assert snapshot["parity_eligible"] is False
    assert snapshot["order_eligible"] is False


def test_shoonya_reconnect_invalidates_previous_epoch_evidence():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 2, True, False, monotonic_ns())
    bridge.shoonya_state("LIVE", None, 2, True, True, monotonic_ns())
    bridge.shoonya_state("RECONNECTING", "socket closed", 2, False, False, monotonic_ns())
    snapshot = bridge.snapshot()["shoonya"]
    assert snapshot["transport"] == "CLOSED"
    assert snapshot["authenticated"] is False
    assert snapshot["feed_ready"] is False


def test_angel_observer_never_promotes_authentication_and_isolated_by_socket_group():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=1)
    bridge.angel_market_event(_record(), group, 4, True, True, monotonic_ns())
    snapshots = bridge.snapshot()["angel_one"]
    state = snapshots["3:1"]
    assert state["transport"] == "OPEN"
    assert state["authentication"] == "NOT_VERIFIABLE"
    assert state["authenticated"] is False
    assert state["subscription"] == "NOT_VERIFIABLE"
    assert state["feed_ready"] is False
    assert state["parity_eligible"] is False


def test_angel_stale_group_generation_is_ignored():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=1, shard=0)
    bridge.angel_market_event(_record(), group, 5, False, True, monotonic_ns())
    bridge.angel_market_event(_record(), group, 4, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["1:0"]
    assert state["generation"] == 5
    assert state["authenticated"] is False


def test_bridge_exposes_no_pairing_scanner_or_order_activation():
    snapshot = ProviderReadinessBridge().snapshot()
    assert snapshot["mode"] == "read_only_diagnostics"
    assert snapshot["pairing_enabled"] is False
    assert snapshot["scanner_fanout_enabled"] is False
    assert snapshot["order_eligible"] is False
