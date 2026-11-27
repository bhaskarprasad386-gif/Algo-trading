from fastapi import HTTPException
import pytest

from app.brokers import routes
from app.brokers.safety import TradingSafetyStore


def test_enable_real_trading_fails_closed_without_owner_auth(monkeypatch):
    store = TradingSafetyStore()
    monkeypatch.setattr(routes, "trading_safety", store)
    payload = routes.RealTradingEnableRequest(confirmation="ENABLE REAL TRADING")

    with pytest.raises(HTTPException) as exc:
        routes.enable_real_trading(payload, user_id=91001)

    assert exc.value.status_code == 503
    state = store.get(91001)
    assert state.real_trading_enabled is False
    assert state.kill_switch is True
    assert state.enabled_at is None


def test_enable_real_trading_still_requires_exact_confirmation(monkeypatch):
    store = TradingSafetyStore()
    monkeypatch.setattr(routes, "trading_safety", store)
    payload = routes.RealTradingEnableRequest(confirmation="yes")

    with pytest.raises(HTTPException) as exc:
        routes.enable_real_trading(payload, user_id=91002)

    assert exc.value.status_code == 400
    state = store.get(91002)
    assert state.real_trading_enabled is False
    assert state.kill_switch is True


def test_capabilities_endpoint_never_advertises_execution_authorization():
    response = routes.broker_capability_matrix(user_id=91003)

    assert response["execution_authorized"] is False
    assert {item["broker"] for item in response["brokers"]} == {
        "angel_one", "zerodha", "fyers", "shoonya", "mstock",
    }
    for item in response["brokers"]:
        assert item["history_provider"] is True
        assert item["can_place_orders"] is False
        assert item["can_cancel_orders"] is False
        assert item["can_modify_orders"] is False
        assert item["live_certified"] is False


def test_connect_endpoint_rejects_non_angel_one_before_using_database():
    payload = routes.ConnectRequest(
        broker="zerodha",
        api_key="not-used",
        client_code="not-used",
        password="not-used",
        totp_secret="not-used",
    )

    with pytest.raises(HTTPException) as exc:
        routes.connect(payload, db=None, user_id=91004)

    assert exc.value.status_code == 400
    assert "Unsupported broker" in exc.value.detail



@pytest.mark.parametrize(
    "endpoint_name",
    ["disable_real_trading", "emergency_kill_switch"],
)
def test_disarm_endpoints_disable_local_state_before_durable_failure(monkeypatch, endpoint_name):
    from types import SimpleNamespace
    from unittest.mock import Mock

    store = TradingSafetyStore()
    store.enable(91005)
    monkeypatch.setattr(routes, "trading_safety", store)

    db = Mock()
    def fail_durable_write(_db):
        assert store.get(91005).real_trading_enabled is False
        assert store.get(91005).kill_switch is True
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(routes, "disable_owner_trading", fail_durable_write)
    endpoint = getattr(routes, endpoint_name)

    with pytest.raises(HTTPException) as exc:
        endpoint(db=db, user_id=91005)

    assert exc.value.status_code == 503
    assert store.get(91005).real_trading_enabled is False
    assert store.get(91005).kill_switch is True
    assert store.get(91005).enabled_at is None
    db.rollback.assert_called_once()


@pytest.mark.parametrize(
    "endpoint_name",
    ["disable_real_trading", "emergency_kill_switch"],
)
def test_disarm_endpoints_confirm_durable_kill_switch_state(monkeypatch, endpoint_name):
    from types import SimpleNamespace

    store = TradingSafetyStore()
    store.enable(91006)
    monkeypatch.setattr(routes, "trading_safety", store)
    persisted = SimpleNamespace(real_trading_enabled=False, kill_switch=True)
    monkeypatch.setattr(routes, "disable_owner_trading", lambda _db: persisted)

    response = getattr(routes, endpoint_name)(db=object(), user_id=91006)

    assert response["real_trading_enabled"] is False
    assert response["kill_switch"] is True
    assert store.get(91006).real_trading_enabled is False
    assert store.get(91006).kill_switch is True
