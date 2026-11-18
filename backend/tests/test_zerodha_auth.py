import hashlib

import pytest

from app.brokers.zerodha_auth import (
    ZerodhaAuthenticationError,
    ZerodhaTypeAAuthenticator,
)


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
    def __init__(self, post_response=None, profile_response=None):
        self.post_response = FakeResponse(
            post_response
            if post_response is not None
            else {
                "status": "success",
                "data": {"user_id": "AB1234", "access_token": "token-123"},
            }
        )
        self.profile_response = FakeResponse(
            profile_response
            if profile_response is not None
            else {
                "status": "success",
                "data": {"user_id": "AB1234", "broker": "ZERODHA"},
            }
        )
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.post_response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.profile_response

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return FakeResponse({"status": "success", "data": {}})


def test_zerodha_request_token_exchange_uses_documented_sha256_checksum():
    http = FakeHTTP()
    auth = ZerodhaTypeAAuthenticator(http=http)

    result = auth.exchange_request_token(
        api_key="key-1",
        api_secret="secret-1",
        request_token="request-1",
        expected_account_id="AB1234",
    )

    expected = hashlib.sha256(b"key-1request-1secret-1").hexdigest()
    assert result.account_id == "AB1234"
    assert result.access_token == "token-123"
    post = http.calls[0]
    assert post[0] == "POST"
    assert post[2]["data"] == {
        "api_key": "key-1",
        "request_token": "request-1",
        "checksum": expected,
    }
    assert "secret-1" not in str(result)


def test_zerodha_profile_identity_must_match_exchange_identity():
    http = FakeHTTP(profile_response={
        "status": "success",
        "data": {"user_id": "OTHER", "broker": "ZERODHA"},
    })
    auth = ZerodhaTypeAAuthenticator(http=http)

    with pytest.raises(ZerodhaAuthenticationError, match="does not match"):
        auth.exchange_request_token(
            api_key="key-1",
            api_secret="secret-1",
            request_token="request-1",
            expected_account_id="AB1234",
        )


def test_zerodha_expected_account_must_match_broker_authenticated_profile():
    http = FakeHTTP()
    auth = ZerodhaTypeAAuthenticator(http=http)

    with pytest.raises(ZerodhaAuthenticationError, match="requested account"):
        auth.exchange_request_token(
            api_key="key-1",
            api_secret="secret-1",
            request_token="request-1",
            expected_account_id="WRONG",
        )


def test_zerodha_rejects_missing_access_token():
    http = FakeHTTP(post_response={
        "status": "success",
        "data": {"user_id": "AB1234"},
    })
    auth = ZerodhaTypeAAuthenticator(http=http)

    with pytest.raises(ZerodhaAuthenticationError, match="missing authenticated identity"):
        auth.exchange_request_token(
            api_key="key-1",
            api_secret="secret-1",
            request_token="request-1",
            expected_account_id="AB1234",
        )


def test_zerodha_rejects_failed_token_exchange():
    http = FakeHTTP(post_response={
        "status": "error",
        "message": "TokenException",
    })
    auth = ZerodhaTypeAAuthenticator(http=http)

    with pytest.raises(ZerodhaAuthenticationError, match="rejected"):
        auth.exchange_request_token(
            api_key="key-1",
            api_secret="secret-1",
            request_token="request-1",
            expected_account_id="AB1234",
        )


def test_zerodha_rejects_blank_credentials_without_network_call():
    http = FakeHTTP()
    auth = ZerodhaTypeAAuthenticator(http=http)

    with pytest.raises(ValueError):
        auth.exchange_request_token(
            api_key="key-1",
            api_secret="",
            request_token="request-1",
            expected_account_id="AB1234",
        )

    assert http.calls == []


def test_zerodha_logout_invalidates_access_token():
    http = FakeHTTP()
    auth = ZerodhaTypeAAuthenticator(http=http)

    auth.invalidate_session(api_key="key-1", access_token="token-123")

    method, url, kwargs = http.calls[0]
    assert method == "DELETE"
    assert url.endswith("/session/token")
    assert kwargs["headers"]["Authorization"] == "token key-1:token-123"
