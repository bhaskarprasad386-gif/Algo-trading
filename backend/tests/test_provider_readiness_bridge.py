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



def test_shoonya_out_of_order_session_callback_cannot_revoke_newer_auth_state():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 9, True, False, 100)
    bridge.shoonya_state("LIVE", None, 9, True, True, 200)
    bridge.shoonya_state("AUTH_FAILED", "late callback", 9, True, False, 150)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "AUTHENTICATED"
    assert state["transport"] == "OPEN"
    assert state["stale_observations_rejected"] == 1


def test_shoonya_auth_rejection_closes_transport_but_preserves_rejection_evidence():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 3, True, False, 100)
    bridge.shoonya_state("AUTH_FAILED", "bad credentials", 3, True, False, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "REJECTED"
    assert state["authenticated"] is False


def test_shoonya_out_of_order_market_observation_is_rejected():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 6, True, False, 100)
    bridge.shoonya_state("LIVE", None, 6, True, True, 200)
    bridge.shoonya_market_event(_record(), 6, True, 300)
    bridge.shoonya_market_event(_record(), 6, True, 250)
    state = bridge.snapshot()["shoonya"]
    assert state["market_event_age_ns"] is not None
    assert state["stale_observations_rejected"] == 1


def test_angel_out_of_order_observation_is_rejected_per_socket_group():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=2)
    bridge.angel_market_event(_record(), group, 8, False, True, 300)
    bridge.angel_market_event(_record(), group, 8, False, True, 250)
    state = bridge.snapshot()["angel_one"]["3:2"]
    assert state["stale_observations_rejected"] == 1
    assert state["authentication"] == "NOT_VERIFIABLE"


def test_angel_non_current_connection_observation_is_ignored():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=0)
    bridge.angel_market_event(_record(), group, 8, False, False, monotonic_ns())
    assert bridge.snapshot()["angel_one"] == {}



def test_shoonya_live_state_without_connected_transport_cannot_authenticate():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 14, True, False, 100)
    bridge.shoonya_state("LIVE", None, 14, False, True, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "UNKNOWN"
    assert state["authenticated"] is False
    assert state["feed_ready"] is False


def test_shoonya_market_event_requires_current_verified_authenticated_session():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 15, True, False, 100)
    bridge.shoonya_market_event(_record(), 15, True, 200)
    state = bridge.snapshot()["shoonya"]
    assert state["authentication"] == "UNKNOWN"
    assert state["market_event_age_ns"] is None
    assert state["feed_ready"] is False


def test_shoonya_market_event_after_auth_rejection_is_ignored():
    bridge = ProviderReadinessBridge()
    bridge.shoonya_state("AUTHENTICATING", None, 16, True, False, 100)
    bridge.shoonya_state("AUTH_FAILED", "rejected", 16, True, False, 200)
    bridge.shoonya_market_event(_record(), 16, True, 300)
    state = bridge.snapshot()["shoonya"]
    assert state["transport"] == "CLOSED"
    assert state["authentication"] == "REJECTED"
    assert state["market_event_age_ns"] is None


def test_bridge_rejects_invalid_or_future_exchange_timestamp():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=0)
    future_record = _record().model_copy(update={"timestamp_ns": time_ns() + 60_000_000_000})
    bridge.angel_market_event(future_record, group, 21, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["2:0"]
    assert state["canonical_identity_valid"] is True
    assert state["exchange_timestamp_valid"] is True
    assert state["market_event_fresh"] is False
    assert state["feed_ready"] is False


def test_bridge_rejects_missing_canonical_identity():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=1)
    invalid_record = _record().model_copy(update={"symbol": " "})
    bridge.angel_market_event(invalid_record, group, 22, False, True, monotonic_ns())
    state = bridge.snapshot()["angel_one"]["2:1"]
    assert state["canonical_identity_valid"] is False
    assert state["feed_ready"] is False


def test_angel_transport_open_close_events_are_generation_scoped():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=3, shard=4)
    bridge.angel_transport_event("OPEN", group, 31, 100)
    opened = bridge.snapshot()["angel_one"]["3:4"]
    assert opened["transport"] == "OPEN"
    assert opened["authentication"] == "NOT_VERIFIABLE"
    bridge.angel_market_event(_record(), group, 31, False, True, 200)
    assert bridge.snapshot()["angel_one"]["3:4"]["market_event_age_ns"] is not None
    bridge.angel_transport_event("CLOSED", group, 31, 300)
    closed = bridge.snapshot()["angel_one"]["3:4"]
    assert closed["transport"] == "CLOSED"
    assert closed["market_event_age_ns"] is None
    assert closed["authenticated"] is False


def test_angel_old_generation_close_cannot_close_replacement():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=1, shard=2)
    bridge.angel_transport_event("OPEN", group, 41, 100)
    bridge.angel_transport_event("OPEN", group, 42, 200)
    bridge.angel_transport_event("CLOSED", group, 41, 300)
    state = bridge.snapshot()["angel_one"]["1:2"]
    assert state["generation"] == 42
    assert state["transport"] == "OPEN"


def test_angel_closed_generation_rejects_late_market_event():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=3)
    bridge.angel_transport_event("OPEN", group, 51, 100)
    bridge.angel_transport_event("CLOSED", group, 51, 200)
    bridge.angel_market_event(_record(), group, 51, False, True, 300)
    state = bridge.snapshot()["angel_one"]["2:3"]
    assert state["transport"] == "CLOSED"
    assert state["market_event_age_ns"] is None


def test_angel_transport_event_rejects_out_of_order_lifecycle():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=4)
    bridge.angel_transport_event("OPEN", group, 61, 200)
    bridge.angel_transport_event("CLOSED", group, 61, 150)
    state = bridge.snapshot()["angel_one"]["2:4"]
    assert state["transport"] == "OPEN"
    assert state["stale_observations_rejected"] == 1


def test_angel_error_is_terminal_until_a_new_generation_opens():
    bridge = ProviderReadinessBridge()
    group = SimpleNamespace(mode=2, shard=5)
    bridge.angel_transport_event("OPEN", group, 71, 100)
    bridge.angel_transport_event("ERROR", group, 71, 200)
    bridge.angel_transport_event("OPEN", group, 71, 300)
    failed = bridge.snapshot()["angel_one"]["2:5"]
    assert failed["generation"] == 71
    assert failed["transport"] == "CLOSED"
    assert failed["stale_observations_rejected"] == 1

    bridge.angel_transport_event("OPEN", group, 72, 400)
    recovered = bridge.snapshot()["angel_one"]["2:5"]
    assert recovered["generation"] == 72
    assert recovered["transport"] == "OPEN"
    assert recovered["authentication"] == "NOT_VERIFIABLE"
