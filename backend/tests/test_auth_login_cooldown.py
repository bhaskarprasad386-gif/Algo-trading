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
