import pytest

from app.brokers.mstock import MStockAdapter, MStockAuthenticationError


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def post(self, url, *, headers, data, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append({
            "endpoint": endpoint,
            "headers": headers,
            "data": data,
            "timeout": timeout,
        })
        return FakeResponse(self.responses[endpoint])


def test_type_a_password_login_starts_otp_without_retaining_credentials():
    http = FakeHTTP({
        "login": {"status": "success", "data": {"cid": "ACCOUNT-1", "ugid": "challenge"}},
    })
    adapter = MStockAdapter(http=http)

    result = adapter.begin_login(username="user-1", password="secret")

    assert result == {"broker": "mstock", "status": "OTP_REQUIRED"}
    assert http.calls[0]["endpoint"] == "login"
    assert http.calls[0]["headers"]["X-Mirae-Version"] == "1"
    assert http.calls[0]["data"] == {"username": "user-1", "password": "secret"}
    assert adapter.connected is False
    assert adapter.account_id is None
    assert adapter.access_token is None


def test_type_a_otp_exchange_checks_authenticated_user_id_before_connecting():
    http = FakeHTTP({
        "token": {"status": "success", "data": {
            "user_id": "ACCOUNT-1", "access_token": "short-lived-token",
        }},
    })
    adapter = MStockAdapter(http=http)

    result = adapter.complete_otp(
        api_key="api-key", otp="123456", account_id="ACCOUNT-1"
    )

    assert result == {
        "broker": "mstock", "connected": True,
        "account_id": "ACCOUNT-1", "auth_method": "OTP_OR_TOTP",
    }
    assert http.calls[0]["data"] == {
        "api_key": "api-key", "request_token": "123456", "checksum": "L",
    }
    assert adapter.connected is True
    assert adapter.account_id == "ACCOUNT-1"
    assert adapter.access_token == "short-lived-token"


def test_type_a_totp_exchange_checks_authenticated_user_id_before_connecting():
    http = FakeHTTP({
        "verifytotp": {"status": "success", "data": {
            "user_id": "ACCOUNT-2", "access_token": "totp-token",
        }},
    })
    adapter = MStockAdapter(http=http)

    result = adapter.complete_totp(
        api_key="api-key", totp="654321", account_id="ACCOUNT-2"
    )

    assert result["connected"] is True
    assert result["account_id"] == "ACCOUNT-2"
    assert http.calls[0]["data"] == {"api_key": "api-key", "totp": "654321"}
    assert adapter.access_token == "totp-token"


@pytest.mark.parametrize("method,endpoint,credentials", [
    ("complete_otp", "token", {"api_key": "api-key", "otp": "123456", "account_id": "OTHER"}),
    ("complete_totp", "verifytotp", {"api_key": "api-key", "totp": "123456", "account_id": "OTHER"}),
])
def test_type_a_account_mismatch_rejects_session_without_connecting(method, endpoint, credentials):
    http = FakeHTTP({
        endpoint: {"status": "success", "data": {
            "user_id": "ACTUAL", "access_token": "must-not-be-used",
        }},
    })
    adapter = MStockAdapter(http=http)

    with pytest.raises(MStockAuthenticationError, match="does not match"):
        getattr(adapter, method)(**credentials)

    assert adapter.connected is False
    assert adapter.account_id is None
    assert adapter.access_token is None


@pytest.mark.parametrize("payload", [
    None,
    {"status": "error", "data": None},
    {"status": "success", "data": []},
    {"status": "success", "data": {"user_id": "ACCOUNT-1"}},
    {"status": "success", "data": {"access_token": "token"}},
])
def test_type_a_invalid_session_response_fails_closed(payload):
    http = FakeHTTP({"token": payload})
    adapter = MStockAdapter(http=http)

    with pytest.raises(MStockAuthenticationError):
        adapter.complete_otp(api_key="api-key", otp="123456", account_id="ACCOUNT-1")

    assert adapter.connected is False


def test_type_a_login_rejects_blank_credentials_before_network_call():
    http = FakeHTTP({})
    adapter = MStockAdapter(http=http)

    with pytest.raises(ValueError):
        adapter.begin_login(username=" ", password="secret")
    assert http.calls == []


def test_type_a_disconnect_clears_authenticated_session():
    http = FakeHTTP({
        "verifytotp": {"status": "success", "data": {
            "user_id": "ACCOUNT-1", "access_token": "session-token",
        }},
    })
    adapter = MStockAdapter(http=http)
    adapter.complete_totp(api_key="api-key", totp="123456", account_id="ACCOUNT-1")

    adapter.disconnect()

    assert adapter.connected is False
    assert adapter.api_key is None
    assert adapter.access_token is None
    assert adapter.account_id is None
