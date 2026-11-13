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
        return {"subscriptions": len(self.descriptors), "authenticated": False}


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
