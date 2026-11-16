from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.brokers import zerodha_routes
from app.brokers.connections import broker_connections
from app.brokers.session_store import broker_sessions
from app.brokers.zerodha_auth import ZerodhaAuthenticatedSession


class FakeClient:
    def __init__(self, *, api_key, access_token="", http=None):
        self.api_key = api_key
        self.access_token = access_token
        self.disconnected = False

    def set_access_token(self, token):
        self.access_token = token

    def profile(self):
        return {"user_id": "AB1234"}

    def logout(self):
        self.disconnected = True
        self.access_token = ""


class FakeAdapter:
    name = "zerodha"

    def __init__(self, client):
        self.client = client
        self.connected = False

    def connect(self, *, access_token, account_id):
        self.client.set_access_token(access_token)
        if self.client.profile()["user_id"] != account_id:
            raise RuntimeError("profile mismatch")
        self.connected = True

    def disconnect(self):
        self.connected = False
        self.client.logout()


def cleanup():
    adapter = broker_sessions.remove(0, "zerodha")
    if adapter:
        adapter.disconnect()
    broker_connections.disconnect(0, "zerodha")


def payload():
    return zerodha_routes.ZerodhaVerifyRequest(
        api_key="api-key",
        api_secret="api-secret",
        request_token="one-time-request-token",
        expected_account_id="AB1234",
    )


def test_verify_binds_identity_and_keeps_trading_disabled(monkeypatch, db_session):
    cleanup()
    monkeypatch.setattr(
        zerodha_routes.ZerodhaTypeAAuthenticator,
        "exchange_request_token",
        lambda self, **kwargs: ZerodhaAuthenticatedSession("AB1234", "access-token"),
    )
    client = FakeClient(api_key="api-key", access_token="access-token")
    monkeypatch.setattr(zerodha_routes, "ZerodhaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(zerodha_routes, "ZerodhaAdapter", FakeAdapter)
    claimed, owner_connected = [], []
    monkeypatch.setattr(
        zerodha_routes,
        "claim_authenticated_account",
        lambda db, broker, account: claimed.append((broker, account)),
    )
    monkeypatch.setattr(
        zerodha_routes,
        "set_owner_connection_state",
        lambda db, value: owner_connected.append(value),
    )
    monkeypatch.setattr(zerodha_routes.trading_safety, "disable", lambda user_id: None)

    result = zerodha_routes.zerodha_verify(payload(), db=db_session, user_id=0)

    assert result["connected"] is True
    assert result["broker"] == "zerodha"
    assert result["account_id"] == "AB1234"
    assert result["real_trading"] is False
    assert result["kill_switch"] is True
    assert claimed == [("zerodha", "AB1234")]
    assert owner_connected == [True]
    assert broker_sessions.get(0, "zerodha").connected is True
    cleanup()


def test_verify_rejects_owner_conflict_and_discards_token(monkeypatch, db_session):
    cleanup()
    monkeypatch.setattr(
        zerodha_routes.ZerodhaTypeAAuthenticator,
        "exchange_request_token",
        lambda self, **kwargs: ZerodhaAuthenticatedSession("AB1234", "access-token"),
    )
    client = FakeClient(api_key="api-key", access_token="access-token")
    monkeypatch.setattr(zerodha_routes, "ZerodhaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(zerodha_routes, "ZerodhaAdapter", FakeAdapter)
    monkeypatch.setattr(
        zerodha_routes,
        "claim_authenticated_account",
        lambda *args: (_ for _ in ()).throw(HTTPException(status_code=409, detail="owner conflict")),
    )

    with pytest.raises(HTTPException) as raised:
        zerodha_routes.zerodha_verify(payload(), db=db_session, user_id=0)

    assert raised.value.status_code == 409
    assert client.disconnected is True
    assert broker_sessions.get(0, "zerodha") is None


def test_rest_client_uses_kite_auth_and_clears_token_on_logout():
    from app.brokers.zerodha_routes import ZerodhaRESTClient

    http = Mock()
    http.delete.return_value.raise_for_status.return_value = None
    client = ZerodhaRESTClient(api_key="api-key", access_token="access-token", http=http)
    client.logout()
    assert client.access_token == ""
    http.delete.assert_called_once()
    assert http.delete.call_args.args[0].endswith("/session/token")
    assert http.delete.call_args.kwargs["headers"]["Authorization"] == "token api-key:access-token"


def test_verify_rejects_failed_token_exchange(monkeypatch, db_session):
    monkeypatch.setattr(
        zerodha_routes.ZerodhaTypeAAuthenticator,
        "exchange_request_token",
        Mock(side_effect=zerodha_routes.ZerodhaAuthenticationError("rejected")),
    )
    with pytest.raises(HTTPException) as raised:
        zerodha_routes.zerodha_verify(payload(), db=db_session, user_id=0)
    assert raised.value.status_code == 401
