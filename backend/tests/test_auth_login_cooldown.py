import time
from threading import RLock

import pytest

from app.algo import auth as auth_module
from app.algo.auth import AngelOneAuth
from app.core.exceptions import TradingAppException


def test_login_cooldown_suppresses_sdk_login(monkeypatch):
    instance = object.__new__(AngelOneAuth)
    instance._state_lock = RLock()
    instance._login_retry_after = time.monotonic() + 75
    instance._login_failure_count = 1
    instance.is_logged_in = False

    def unexpected_sdk_login(*args, **kwargs):
        pytest.fail("SmartConnect must not be called while login cooldown is active")

    monkeypatch.setattr(auth_module, "SmartConnect", unexpected_sdk_login)

    with pytest.raises(TradingAppException) as caught:
        instance.login()

    assert caught.value.name == "LoginCooldown"
    assert caught.value.status_code == 429



def test_rate_limit_failure_applies_90_second_cooldown(monkeypatch):
    instance = object.__new__(AngelOneAuth)
    instance._state_lock = RLock()
    instance.api_key = "test-api-key"
    instance.client_id = "test-client"
    instance.password = "test-password"
    instance.totp_secret = "TESTSECRET"
    instance.smart_api = None
    instance.session_data = None
    instance.is_logged_in = False
    instance._session_obtained_at = 0.0
    instance._force_fresh_session = False
    instance._login_retry_after = 0.0
    instance._login_failure_count = 0

    class FakeSmartConnect:
        calls = 0

        def __init__(self, api_key):
            pass

        def generateSession(self, client_id, password, totp):
            type(self).calls += 1
            raise RuntimeError("Access denied because of exceeding access rate")

    fake = FakeSmartConnect
    monkeypatch.setattr(auth_module, "SmartConnect", fake)
    monkeypatch.setattr(auth_module.pyotp, "TOTP", lambda secret: type("T", (), {"now": lambda self: "000000"})())

    before = time.monotonic()
    with pytest.raises(TradingAppException):
        instance.login()

    remaining = instance._login_retry_after - before
    assert 89.0 <= remaining <= 91.0
    assert instance._login_failure_count == 1

    with pytest.raises(TradingAppException) as caught:
        instance.login()

    assert caught.value.name == "LoginCooldown"
    assert fake.calls == 1



def test_rate_limit_message_in_broker_response_applies_90_second_cooldown(monkeypatch):
    instance = object.__new__(AngelOneAuth)
    instance._state_lock = RLock()
    instance.api_key = "test-api-key"
    instance.client_id = "test-client"
    instance.password = "test-password"
    instance.totp_secret = "TESTSECRET"
    instance.smart_api = None
    instance.session_data = None
    instance.is_logged_in = False
    instance._session_obtained_at = 0.0
    instance._force_fresh_session = False
    instance._login_retry_after = 0.0
    instance._login_failure_count = 0

    class FakeSmartConnect:
        def __init__(self, api_key):
            pass

        def generateSession(self, client_id, password, totp):
            return {
                "status": False,
                "message": "Access denied because of exceeding access rate",
            }

    monkeypatch.setattr(auth_module, "SmartConnect", FakeSmartConnect)
    monkeypatch.setattr(
        auth_module.pyotp,
        "TOTP",
        lambda secret: type("T", (), {"now": lambda self: "000000"})(),
    )

    before = time.monotonic()
    with pytest.raises(TradingAppException):
        instance.login()

    remaining = instance._login_retry_after - before
    assert 89.0 <= remaining <= 91.0
