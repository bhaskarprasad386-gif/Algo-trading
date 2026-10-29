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
