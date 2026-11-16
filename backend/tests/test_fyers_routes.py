from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.brokers import fyers_routes
from app.brokers.connections import broker_connections
from app.brokers.session_store import broker_sessions
from app.brokers.fyers_auth import FyersAuthenticatedSession


class FakeClient:
    def __init__(self, app_id, access_token):
        self.app_id = app_id
        self.access_token = access_token
        self.disconnected = False

    def get_profile(self):
        return {"s": "ok", "code": 200, "data": {"fy_id": "FY12345"}}

    def disconnect(self):
        self.disconnected = True
        self.access_token = ""


class FakeAdapter:
    name = "fyers"

    def __init__(self, client):
        self.client = client
        self.connected = False
        self.disconnected = False

    def connect(self, *, account_id):
        assert self.client.get_profile()["data"]["fy_id"] == account_id
        self.connected = True

    def disconnect(self):
        self.connected = False
        self.disconnected = True
        self.client.disconnect()


def cleanup():
    adapter = broker_sessions.remove(0, "fyers")
    if adapter:
        adapter.disconnect()
    broker_connections.disconnect(0, "fyers")


def test_auth_url_returns_single_use_state_and_url(monkeypatch):
    store = Mock()
    store.create.return_value = ("a" * 32, "https://api-t1.fyers.in/login?state=" + "a" * 32)
    monkeypatch.setattr(fyers_routes, "fyers_oauth_states", store)

    result = fyers_routes.fyers_auth_url(
        fyers_routes.FyersAuthUrlRequest(
            app_id="APP-100",
            redirect_uri="https://example.com/callback",
        ),
        user_id=0,
    )

    assert result["state"] == "a" * 32
    assert result["expires_in"] == 600
    store.create.assert_called_once_with(
        app_id="APP-100",
        redirect_uri="https://example.com/callback",
    )


def test_auth_url_rejects_unregistered_redirect_shape(monkeypatch):
    store = Mock()
    store.create.side_effect = ValueError("bad redirect")
    monkeypatch.setattr(fyers_routes, "fyers_oauth_states", store)

    with pytest.raises(HTTPException) as raised:
        fyers_routes.fyers_auth_url(
            fyers_routes.FyersAuthUrlRequest(
                app_id="APP-100",
                redirect_uri="http://example.com/callback",
            ),
            user_id=0,
        )
    assert raised.value.status_code == 422


def test_verify_binds_broker_identity_and_keeps_trading_off(monkeypatch, db_session):
    cleanup()
    state_store = Mock()
    monkeypatch.setattr(fyers_routes, "fyers_oauth_states", state_store)
    monkeypatch.setattr(
        fyers_routes.FyersTypeAAuthenticator,
        "exchange_auth_code",
        lambda self, **kwargs: FyersAuthenticatedSession("FY12345", "token-secret"),
    )
    client = FakeClient("APP-100", "token-secret")
    monkeypatch.setattr(fyers_routes, "FyersRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(fyers_routes, "FyersAdapter", FakeAdapter)
    claimed = []
    owner_connected = []
    monkeypatch.setattr(
        fyers_routes,
        "claim_authenticated_account",
        lambda db, broker, account: claimed.append((broker, account)),
    )
    monkeypatch.setattr(
        fyers_routes,
        "set_owner_connection_state",
        lambda db, connected: owner_connected.append(connected),
    )
    monkeypatch.setattr(fyers_routes.trading_safety, "disable", lambda user_id: None)

    result = fyers_routes.fyers_verify(
        fyers_routes.FyersVerifyRequest(
            app_id="APP-100",
            app_secret="secret",
            auth_code="one-time-code",
            state="s" * 32,
            redirect_uri="https://example.com/callback",
            expected_account_id="FY12345",
        ),
        db=db_session,
        user_id=0,
    )

    assert result["connected"] is True
    assert result["account_id"] == "FY12345"
    assert result["real_trading"] is False
    assert result["kill_switch"] is True
    assert claimed == [("fyers", "FY12345")]
    assert owner_connected == [True]
    assert state_store.consume.called
    assert broker_sessions.get(0, "fyers").connected is True
    cleanup()


def test_verify_rejects_replayed_or_invalid_state_before_network(monkeypatch, db_session):
    store = Mock()
    store.consume.side_effect = ValueError("replayed")
    monkeypatch.setattr(fyers_routes, "fyers_oauth_states", store)
    exchange = Mock()
    monkeypatch.setattr(fyers_routes.FyersTypeAAuthenticator, "exchange_auth_code", exchange)

    with pytest.raises(HTTPException) as raised:
        fyers_routes.fyers_verify(
            fyers_routes.FyersVerifyRequest(
                app_id="APP-100",
                app_secret="secret",
                auth_code="one-time-code",
                state="s" * 32,
                redirect_uri="https://example.com/callback",
                expected_account_id="FY12345",
            ),
            db=db_session,
            user_id=0,
        )

    assert raised.value.status_code == 401
    exchange.assert_not_called()


def test_rest_client_clears_token_even_when_remote_logout_fails():
    from app.brokers.fyers_routes import FyersRESTClient

    http = Mock()
    http.post.side_effect = RuntimeError("offline")
    client = FyersRESTClient(app_id="APP-100", access_token="token-secret", http=http)
    client.disconnect()
    assert client.access_token == ""
    http.post.assert_called_once()


def test_verify_owner_conflict_discards_authenticated_session(monkeypatch, db_session):
    cleanup()
    state_store = Mock()
    monkeypatch.setattr(fyers_routes, "fyers_oauth_states", state_store)
    monkeypatch.setattr(
        fyers_routes.FyersTypeAAuthenticator,
        "exchange_auth_code",
        lambda self, **kwargs: FyersAuthenticatedSession("FY12345", "token-secret"),
    )
    client = FakeClient("APP-100", "token-secret")
    monkeypatch.setattr(fyers_routes, "FyersRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(fyers_routes, "FyersAdapter", FakeAdapter)
    monkeypatch.setattr(
        fyers_routes,
        "claim_authenticated_account",
        lambda *args: (_ for _ in ()).throw(HTTPException(status_code=409, detail="owner conflict")),
    )

    with pytest.raises(HTTPException) as raised:
        fyers_routes.fyers_verify(
            fyers_routes.FyersVerifyRequest(
                app_id="APP-100",
                app_secret="secret",
                auth_code="one-time-code",
                state="s" * 32,
                redirect_uri="https://example.com/callback",
                expected_account_id="FY12345",
            ),
            db=db_session,
            user_id=0,
        )

    assert raised.value.status_code == 409
    assert client.disconnected is True
    assert broker_sessions.get(0, "fyers") is None
