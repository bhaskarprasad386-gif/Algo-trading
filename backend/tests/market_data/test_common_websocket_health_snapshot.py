from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry


def test_health_snapshot_avoids_descriptor_roster_and_per_instrument_maps(monkeypatch):
    registry = InstrumentRegistry()
    key = InstrumentKey("NSE", "NSE", "12345")
    registry.register(InstrumentDescriptor(
        key=key,
        symbol="NIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="NIFTY",
    ))
    registry.subscribe("health-test", key, mode=3)
    manager = CommonWebSocketManager(
        registry=registry,
        socket_factory=lambda: object(),
        exchange_type_resolver=lambda _key: 1,
    )

    # A health-only read must not perform descriptor-by-descriptor enrichment.
    monkeypatch.setattr(
        registry,
        "get",
        lambda _key: (_ for _ in ()).throw(AssertionError("full descriptor lookup used")),
    )
    result = manager.health_snapshot()

    assert result["snapshot_kind"] == "health-lite"
    assert result["subscriptions"] == 1
    assert result["active_instruments"] == 1
    assert result["required_socket_groups"]
    assert result["missing_socket_groups"] == result["required_socket_groups"]
    assert result["unconnected_required_groups"] == result["required_socket_groups"]
    assert "subscription_roster" not in result
    assert "raw_frames_by_instrument" not in result
    assert "socket_token_set_mismatches" not in result
    assert "consumer_callback_metrics" not in result


def test_full_snapshot_remains_available_for_explicit_deep_diagnostics():
    registry = InstrumentRegistry()
    key = InstrumentKey("NSE", "NSE", "12345")
    registry.register(InstrumentDescriptor(
        key=key,
        symbol="NIFTY",
        instrument_type="index",
        exchange="NSE",
        segment="NSE",
        underlying="NIFTY",
    ))
    registry.subscribe("diagnostic-test", key, mode=3)
    manager = CommonWebSocketManager(
        registry=registry,
        socket_factory=lambda: object(),
        exchange_type_resolver=lambda _key: 1,
    )

    result = manager.snapshot()

    assert result["subscriptions"] == 1
    assert len(result["subscription_roster"]) == 1
    assert result["subscription_roster"][0]["symbol"] == "NIFTY"
