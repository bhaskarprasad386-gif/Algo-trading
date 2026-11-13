from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.market_data import shoonya_supervisor


def descriptor(token="12345"):
    return InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", token),
        symbol=f"NIFTY26NOV{token}CE",
        instrument_type="option",
        exchange="NFO",
        segment="NFO",
        expiry="2026-11-26",
        strike=25000,
        option_type="CE",
        lot_size=65,
        tick_size=0.05,
        underlying="NIFTY",
    )


class FakeAdapter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.descriptors = {}
        self.started = 0
        self.stopped = 0
        self.last_error = None

    def subscribe(self, descriptors):
        for item in descriptors:
            self.descriptors[(item.key.exchange, item.key.token)] = item
        return len(tuple(descriptors))

    def unsubscribe(self, descriptors):
        removed = 0
        for item in descriptors:
            if self.descriptors.pop((item.key.exchange, item.key.token), None):
                removed += 1
        return removed

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def snapshot(self):
        return {"subscriptions": len(self.descriptors), "authenticated": False, "last_error": self.last_error}


def build_supervisor(monkeypatch, **kwargs):
    monkeypatch.setattr(shoonya_supervisor, "ShoonyaWebSocket", FakeAdapter)
    return shoonya_supervisor.ShoonyaMarketDataSupervisor(
        uid="U1", actid="U1", access_token="token", **kwargs
    )


def test_supervisor_is_shadow_only_and_does_not_start_without_subscriptions(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    supervisor.start()
    state = supervisor.snapshot()
    assert state["mode"] == "shadow_only"
    assert state["enabled"] is True
    assert state["state"] == "IDLE_NO_SUBSCRIPTIONS"
    assert state["order_routing_enabled"] is False
    assert state["strategy_fanout_enabled"] is False
    assert supervisor._adapter.started == 0


def test_supervisor_subscribe_registers_then_starts_isolated_adapter(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    item = descriptor()
    assert supervisor.subscribe([item]) == 1
    assert supervisor._adapter.started == 1
    assert supervisor.snapshot()["registry_instruments"] == 1
    assert supervisor.snapshot()["adapter"]["subscriptions"] == 1


def test_supervisor_enforces_configured_cap_before_adapter_mutation(monkeypatch):
    supervisor = build_supervisor(monkeypatch, max_tokens=1)
    supervisor.register([descriptor("1")])
    try:
        supervisor.register([descriptor("2")])
    except ValueError as exc:
        assert "cap" in str(exc)
    else:
        raise AssertionError("configured subscription cap must be enforced")
    assert supervisor.snapshot()["registry_instruments"] == 1


def test_supervisor_rejects_descriptor_conflicts_atomically(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    first = descriptor()
    supervisor.register([first])
    conflicting = InstrumentDescriptor(
        key=first.key, symbol="DIFFERENT", instrument_type="option",
        exchange="NFO", segment="NFO", expiry="2026-11-26",
        strike=25000, option_type="CE", lot_size=65, tick_size=0.05,
        underlying="NIFTY",
    )
    try:
        supervisor.register([descriptor("2"), conflicting])
    except ValueError:
        pass
    else:
        raise AssertionError("conflicting descriptor must be rejected")
    assert supervisor.snapshot()["registry_instruments"] == 1


def test_supervisor_unsubscribe_removes_owned_descriptor_and_stop_is_idempotent(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    item = descriptor()
    supervisor.subscribe([item])
    assert supervisor.unsubscribe([item]) == 1
    assert supervisor.snapshot()["registry_instruments"] == 0
    supervisor.stop()
    supervisor.stop()
    assert supervisor._adapter.stopped == 1


def test_supervisor_tick_and_order_callbacks_only_update_shadow_counters(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    supervisor._observe_tick({"symbol": "NIFTY", "ltp": 100})
    supervisor._observe_order_update({"status": "COMPLETE", "order_id": "sensitive"})
    state = supervisor.snapshot()
    assert state["ticks_observed"] == 1
    assert state["order_events_observed"] == 1
    assert state["order_routing_enabled"] is False
    assert state["strategy_fanout_enabled"] is False



def test_supervisor_rolls_back_registry_when_adapter_rejects_subscription(monkeypatch):
    supervisor = build_supervisor(monkeypatch)

    def reject(_descriptors):
        raise ValueError("adapter rejected subscription")

    supervisor._adapter.subscribe = reject
    try:
        supervisor.subscribe([descriptor()])
    except ValueError as exc:
        assert "adapter rejected" in str(exc)
    else:
        raise AssertionError("adapter rejection must propagate")
    assert supervisor.snapshot()["registry_instruments"] == 0
    assert supervisor._adapter.started == 0


def test_authenticated_subscription_routes_use_supervisor_provider(monkeypatch):
    from app.core.config import settings
    from app.market_data import routes

    class RouteSupervisor:
        def __init__(self):
            self.added = []
            self.removed = []

        def subscribe(self, descriptors):
            self.added.extend(descriptors)
            return len(descriptors)

        def unsubscribe(self, descriptors):
            self.removed.extend(descriptors)
            return len(descriptors)

    supervisor = RouteSupervisor()
    monkeypatch.setattr(settings, "SHOONYA_MARKET_DATA_ENABLED", True)
    monkeypatch.setattr(routes, "_shoonya_supervisor_provider", lambda: supervisor)
    payload = routes.ShoonyaSubscriptionRequest(instruments=[{
        "exchange": "NFO", "segment": "NFO", "token": "12345",
        "symbol": "NIFTY26NOV25000CE", "instrument_type": "option",
        "expiry": "2026-11-26", "strike": 25000, "option_type": "CE",
        "lot_size": 65, "tick_size": 0.05, "underlying": "NIFTY",
    }])

    added = routes.add_shoonya_subscriptions(payload, _owner_id=0)
    removed = routes.remove_shoonya_subscriptions(payload, _owner_id=0)
    assert added["subscribed"] == 1
    assert removed["unsubscribed"] == 1
    assert supervisor.added[0].key == supervisor.removed[0].key
    assert added["order_routing_enabled"] is False
    assert removed["strategy_fanout_enabled"] is False


def test_shoonya_subscription_route_fails_closed_when_disabled(monkeypatch):
    from fastapi import HTTPException
    from app.core.config import settings
    from app.market_data import routes

    monkeypatch.setattr(settings, "SHOONYA_MARKET_DATA_ENABLED", False)
    monkeypatch.setattr(routes, "_shoonya_supervisor_provider", None)
    payload = routes.ShoonyaSubscriptionRequest(instruments=[{
        "exchange": "NFO", "segment": "NFO", "token": "12345",
        "symbol": "NIFTY26NOV25000CE", "instrument_type": "option",
        "expiry": "2026-11-26", "strike": 25000, "option_type": "CE",
    }])
    try:
        routes.add_shoonya_subscriptions(payload, _owner_id=0)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("disabled Shoonya subscriptions must fail closed")


def test_supervisor_redacts_credentials_from_public_diagnostics(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    supervisor._observe_state("AUTH_FAILED", "auth failed for token")
    state = supervisor.snapshot()
    assert "token" not in (state["state_reason"] or "")
    supervisor._adapter.last_error = "transport failed token"
    state = supervisor.snapshot()
    assert "token" not in (state["adapter"]["last_error"] or "")


def test_shoonya_option_subscription_requires_complete_contract_metadata():
    from app.market_data.routes import ShoonyaInstrumentInput

    incomplete = ShoonyaInstrumentInput(
        exchange="NFO", segment="NFO", token="12345",
        symbol="NIFTY26NOV25000CE", instrument_type="option",
    )
    try:
        incomplete.to_descriptor()
    except ValueError as exc:
        assert "expiry, strike and option_type" in str(exc)
    else:
        raise AssertionError("incomplete option metadata must be rejected")


def test_supervisor_cleans_adapter_and_registry_when_start_fails(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    item = descriptor()

    def fail_start():
        raise RuntimeError("worker startup failed")

    supervisor._adapter.start = fail_start
    try:
        supervisor.subscribe([item])
    except RuntimeError as exc:
        assert "worker startup failed" in str(exc)
    else:
        raise AssertionError("adapter startup failure must propagate")

    assert supervisor.snapshot()["registry_instruments"] == 0
    assert supervisor.registry.get(item.key) is None
    assert supervisor._adapter.descriptors == {}


def test_supervisor_unsubscribe_removes_inactive_registry_descriptor(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    item = descriptor()
    supervisor.subscribe([item])
    assert supervisor.unsubscribe([item]) == 1
    assert supervisor.registry.get(item.key) is None


def test_register_only_does_not_start_websocket(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    supervisor.register([descriptor()])
    supervisor.start()
    assert supervisor._adapter.started == 0


def test_adapter_rejection_does_not_unsubscribe_existing_adapter_token(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    existing = descriptor()
    supervisor.subscribe([existing])
    # Simulate an adapter-level conflict not represented by the supervisor's
    # InstrumentKey (e.g. same exchange/token but different segment identity).
    conflicting = InstrumentDescriptor(
        key=InstrumentKey("NFO", "OTHER", existing.key.token),
        symbol="CONFLICT", instrument_type="option", exchange="NFO",
        segment="OTHER", expiry="2026-11-26", strike=25000,
        option_type="CE", lot_size=65, tick_size=0.05, underlying="NIFTY",
    )
    try:
        supervisor.subscribe([conflicting])
    except ValueError:
        pass
    else:
        raise AssertionError("adapter token conflict must be rejected")
    assert supervisor._adapter.descriptors[("NFO", existing.key.token)] == existing
    assert supervisor.snapshot()["registry_instruments"] == 1


def test_supervisor_rejects_same_adapter_token_with_different_segment(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    original = descriptor()
    supervisor.subscribe([original])
    conflicting = InstrumentDescriptor(
        key=InstrumentKey("NFO", "OTHER", original.key.token),
        symbol=original.symbol, instrument_type=original.instrument_type,
        exchange="NFO", segment="OTHER", expiry=original.expiry,
        strike=original.strike, option_type=original.option_type,
        lot_size=original.lot_size, tick_size=original.tick_size,
        underlying=original.underlying,
    )
    try:
        supervisor.subscribe([conflicting])
    except ValueError as exc:
        assert "adapter token" in str(exc)
    else:
        raise AssertionError("same broker token cannot have multiple segment descriptors")
    assert supervisor._adapter.descriptors[("NFO", original.key.token)] == original
    assert supervisor.snapshot()["registry_instruments"] == 1


def test_supervisor_rejects_mismatched_unsubscribe_without_removing_live_token(monkeypatch):
    supervisor = build_supervisor(monkeypatch)
    original = descriptor()
    supervisor.subscribe([original])
    wrong = InstrumentDescriptor(
        key=InstrumentKey("NFO", "OTHER", original.key.token),
        symbol=original.symbol, instrument_type=original.instrument_type,
        exchange="NFO", segment="OTHER", expiry=original.expiry,
        strike=original.strike, option_type=original.option_type,
        lot_size=original.lot_size, tick_size=original.tick_size,
        underlying=original.underlying,
    )
    try:
        supervisor.unsubscribe([wrong])
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched unsubscribe descriptor must be rejected")
    assert supervisor._adapter.descriptors[("NFO", original.key.token)] == original
    assert supervisor.snapshot()["registry_instruments"] == 1


def test_shoonya_market_data_control_routes_require_owner_auth():
    from app.market_data.shoonya_owner_auth import require_shoonya_owner_access
    from app.market_data import routes

    for route in routes.router.routes:
        if route.path.endswith("/shoonya/subscriptions"):
            assert any(
                dependency.call is require_shoonya_owner_access
                for dependency in route.dependant.dependencies
            ), route.path


def test_shoonya_subscription_request_rejects_duplicate_broker_tokens():
    from app.market_data.routes import ShoonyaSubscriptionRequest, _shoonyadescriptors

    instrument = {
        "exchange": "NFO", "segment": "NFO", "token": "12345",
        "symbol": "NIFTY26NOV25000CE", "instrument_type": "option",
        "expiry": "2026-11-26", "strike": 25000, "option_type": "CE",
    }
    payload = ShoonyaSubscriptionRequest(instruments=[instrument, instrument])
    try:
        _shoonyadescriptors(payload)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate broker tokens must be rejected before mutation")
