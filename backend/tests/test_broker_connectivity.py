from unittest.mock import Mock

import pytest

from app.brokers.angel_one import AngelOneAdapter


def test_angel_one_connects_with_runtime_credentials(monkeypatch):
    smart_api = Mock()
    smart_api.generateSession.return_value = {"status": True, "message": "SUCCESS"}

    smart_connect = Mock(return_value=smart_api)
    totp = Mock()
    totp.now.return_value = "123456"

    monkeypatch.setattr("app.brokers.angel_one.SmartConnect", smart_connect)
    monkeypatch.setattr("app.brokers.angel_one.pyotp.TOTP", Mock(return_value=totp))

    adapter = AngelOneAdapter()
    result = adapter.connect(
        api_key="test-api-key",
        client_code="ABCD1234",
        password="test-password",
        totp_secret="TESTSECRET",
    )

    assert result == {"broker": "angel_one", "connected": True, "client_code": "ABCD1234"}
    assert adapter.connected is True
    smart_connect.assert_called_once_with(api_key="test-api-key")
    smart_api.generateSession.assert_called_once_with("ABCD1234", "test-password", "123456")


def test_angel_one_rejects_missing_credentials():
    adapter = AngelOneAdapter()
    with pytest.raises(ValueError, match="Missing Angel One authentication fields"):
        adapter.connect(client_code="ABCD1234")


def test_angel_one_does_not_enable_live_order_routing():
    adapter = AngelOneAdapter()
    with pytest.raises(NotImplementedError, match="Live order routing is disabled"):
        adapter.place_order(symbol="NIFTY")
