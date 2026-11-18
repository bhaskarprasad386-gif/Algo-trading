from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.brokers.shoonya_auth import (
    ShoonyaAuthenticationError,
    ShoonyaOAuthAuthenticator,
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
    def __init__(self, payload=None):
        self.payload = payload or {
            "stat": "Ok",
            "uid": "SH12345",
            "actid": "SH12345",
            "susertoken": "access-token",
        }
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_authorization_url_uses_documented_client_id_parameter():
    url = ShoonyaOAuthAuthenticator.build_authorization_url(client_id="client-100")
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.shoonya.com"
    assert parsed.path == "/OAuthlogin/authorize/oauth"
    assert parse_qs(parsed.query) == {"client_id": ["client-100"]}


def test_authorization_url_rejects_blank_client_id():
    with pytest.raises(ValueError):
        ShoonyaOAuthAuthenticator.build_authorization_url(client_id="")


def test_exchange_uses_documented_checksum_and_jdata_body():
    http = FakeHTTP()
    auth = ShoonyaOAuthAuthenticator(http=http)

    result = auth.exchange_auth_code(
        client_id="client-100",
        secret_code="secret-code",
        auth_code="one-time-code",
        expected_uid="SH12345",
        expected_account_id="SH12345",
    )

    expected = hashlib.sha256(b"client-100secret-codeone-time-code").hexdigest()
    assert result.uid == "SH12345"
    assert result.account_id == "SH12345"
    assert result.access_token == "access-token"
    url, kwargs = http.calls[0]
    assert url.endswith("/GenAcsTok")
    assert kwargs["headers"]["Content-Type"] == "text/plain"
    assert json.loads(kwargs["data"].removeprefix("jData=")) == {
        "code": "one-time-code",
        "checksum": expected,
    }


def test_rejects_authenticated_uid_mismatch():
    http = FakeHTTP({"stat": "Ok", "uid": "OTHER", "actid": "SH12345", "susertoken": "token"})
    auth = ShoonyaOAuthAuthenticator(http=http)
    with pytest.raises(ShoonyaAuthenticationError, match="UID does not match"):
        auth.exchange_auth_code(
            client_id="client-100",
            secret_code="secret-code",
            auth_code="one-time-code",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )


def test_rejects_authenticated_account_mismatch():
    http = FakeHTTP({"stat": "Ok", "uid": "SH12345", "actid": "OTHER", "susertoken": "token"})
    auth = ShoonyaOAuthAuthenticator(http=http)
    with pytest.raises(ShoonyaAuthenticationError, match="account does not match"):
        auth.exchange_auth_code(
            client_id="client-100",
            secret_code="secret-code",
            auth_code="one-time-code",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )


def test_rejects_unsuccessful_exchange():
    http = FakeHTTP({"stat": "Not_Ok", "emsg": "bad code"})
    auth = ShoonyaOAuthAuthenticator(http=http)
    with pytest.raises(ShoonyaAuthenticationError, match="rejected"):
        auth.exchange_auth_code(
            client_id="client-100",
            secret_code="secret-code",
            auth_code="one-time-code",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )


def test_rejects_missing_token_or_identity():
    http = FakeHTTP({"stat": "Ok", "uid": "SH12345", "actid": "SH12345"})
    auth = ShoonyaOAuthAuthenticator(http=http)
    with pytest.raises(ShoonyaAuthenticationError, match="missing access token"):
        auth.exchange_auth_code(
            client_id="client-100",
            secret_code="secret-code",
            auth_code="one-time-code",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", ""),
        ("secret_code", ""),
        ("auth_code", ""),
        ("expected_uid", ""),
        ("expected_account_id", ""),
    ],
)
def test_blank_fields_fail_before_network(field, value):
    http = FakeHTTP()
    auth = ShoonyaOAuthAuthenticator(http=http)
    values = {
        "client_id": "client-100",
        "secret_code": "secret-code",
        "auth_code": "one-time-code",
        "expected_uid": "SH12345",
        "expected_account_id": "SH12345",
    }
    values[field] = value
    with pytest.raises(ValueError):
        auth.exchange_auth_code(**values)
    assert http.calls == []
