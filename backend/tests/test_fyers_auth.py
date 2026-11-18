from __future__ import annotations

import hashlib
from unittest.mock import Mock

import pytest

from app.brokers.fyers_auth import (
    FyersAuthenticationError,
    FyersTypeAAuthenticator,
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


def successful_token(access_token="access-123"):
    return FakeResponse({"s": "ok", "code": 200, "access_token": access_token})


def successful_profile(fy_id="FY12345"):
    return FakeResponse({"s": "ok", "code": 200, "data": {"fy_id": fy_id}})


def test_exchange_uses_documented_hash_and_verifies_profile_identity():
    http = Mock()
    http.post.return_value = successful_token()
    http.get.return_value = successful_profile()
    auth = FyersTypeAAuthenticator(http=http)

    result = auth.exchange_auth_code(
        app_id="APP-100",
        app_secret="private-secret",
        auth_code="one-time-code",
        expected_account_id="FY12345",
    )

    expected_hash = hashlib.sha256(b"APP-100:private-secret").hexdigest()
    assert result.account_id == "FY12345"
    assert result.access_token == "access-123"
    assert http.post.call_args.kwargs["json"] == {
        "grant_type": "authorization_code",
        "appIdHash": expected_hash,
        "code": "one-time-code",
    }
    assert http.get.call_args.kwargs["headers"]["Authorization"] == "APP-100:access-123"


def test_rejects_profile_account_mismatch():
    http = Mock()
    http.post.return_value = successful_token()
    http.get.return_value = successful_profile("OTHER")
    auth = FyersTypeAAuthenticator(http=http)

    with pytest.raises(FyersAuthenticationError, match="does not match"):
        auth.exchange_auth_code(
            app_id="APP-100",
            app_secret="private-secret",
            auth_code="one-time-code",
            expected_account_id="FY12345",
        )


def test_rejects_unsuccessful_token_exchange():
    http = Mock()
    http.post.return_value = FakeResponse(
        {"s": "error", "code": -8, "message": "invalid code"}
    )
    auth = FyersTypeAAuthenticator(http=http)

    with pytest.raises(FyersAuthenticationError, match="rejected"):
        auth.exchange_auth_code(
            app_id="APP-100",
            app_secret="private-secret",
            auth_code="bad-code",
            expected_account_id="FY12345",
        )

    http.get.assert_not_called()


def test_rejects_token_response_without_access_token():
    http = Mock()
    http.post.return_value = FakeResponse({"s": "ok", "code": 200})
    auth = FyersTypeAAuthenticator(http=http)

    with pytest.raises(FyersAuthenticationError, match="missing access_token"):
        auth.exchange_auth_code(
            app_id="APP-100",
            app_secret="private-secret",
            auth_code="one-time-code",
            expected_account_id="FY12345",
        )


def test_rejects_invalid_profile_payload():
    http = Mock()
    http.post.return_value = successful_token()
    http.get.return_value = FakeResponse({"s": "ok", "code": 200, "data": []})
    auth = FyersTypeAAuthenticator(http=http)

    with pytest.raises(FyersAuthenticationError, match="missing account data"):
        auth.exchange_auth_code(
            app_id="APP-100",
            app_secret="private-secret",
            auth_code="one-time-code",
            expected_account_id="FY12345",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_id", ""),
        ("app_secret", ""),
        ("auth_code", ""),
        ("expected_account_id", ""),
    ],
)
def test_blank_credentials_fail_before_network(field, value):
    http = Mock()
    auth = FyersTypeAAuthenticator(http=http)
    values = {
        "app_id": "APP-100",
        "app_secret": "private-secret",
        "auth_code": "one-time-code",
        "expected_account_id": "FY12345",
    }
    values[field] = value

    with pytest.raises(ValueError):
        auth.exchange_auth_code(**values)

    http.post.assert_not_called()
    http.get.assert_not_called()


def test_profile_http_error_is_sanitized():
    http = Mock()
    http.post.return_value = successful_token()
    http.get.return_value = FakeResponse({}, error=RuntimeError("secret token leaked"))
    auth = FyersTypeAAuthenticator(http=http)

    with pytest.raises(FyersAuthenticationError) as error:
        auth.exchange_auth_code(
            app_id="APP-100",
            app_secret="private-secret",
            auth_code="one-time-code",
            expected_account_id="FY12345",
        )

    assert "secret token leaked" not in str(error.value)
