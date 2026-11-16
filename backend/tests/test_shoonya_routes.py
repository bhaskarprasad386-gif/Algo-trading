from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.brokers import shoonya_routes
from app.brokers.shoonya_auth import ShoonyaAuthenticatedSession
from app.brokers.connections import broker_connections
from app.brokers.session_store import broker_sessions


class FakeClient:
    def __init__(self, **kwargs):
        self.uid = kwargs["uid"]
        self.account_id = kwargs["account_id"]
        self.access_token = kwargs["access_token"]
        self.disconnected = False

    def get_user_details(self):
        return {"stat": "Ok", "uid": self.uid, "actid": self.account_id}

    def disconnect(self):
        self.disconnected = True
        self.access_token = ""


class FakeAdapter:
    name = "shoonya"

    def __init__(self, client):
        self.client = client
        self.connected = False

    def connect(self, *, uid, account_id):
        profile = self.client.get_user_details()
        assert profile["uid"] == uid
        assert profile["actid"] == account_id
        self.connected = True

    def disconnect(self):
        self.connected = False
        self.client.disconnect()


def cleanup():
    adapter = broker_sessions.remove(0, "shoonya")
    if adapter:
        adapter.disconnect()
    broker_connections.disconnect(0, "shoonya")


def test_auth_url_returns_authorize_url_and_correlated_flow(monkeypatch):
    store = Mock()
    store.create.return_value = "flow-id-1234567890"
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")

    result = shoonya_routes.shoonya_auth_url(
        shoonya_routes.ShoonyaAuthUrlRequest(
            client_id="client-100",
            uid="SH12345",
            account_id="SH12345",
        ),
        user_id=0,
    )

    assert result["flow_id"] == "flow-id-1234567890"
    assert "client_id=client-100" in result["authorization_url"]
    assert result["expires_in"] == 600
    store.create.assert_called_once_with(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )


def test_verify_consumes_flow_and_binds_broker_identity(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    monkeypatch.setattr(
        shoonya_routes.ShoonyaOAuthAuthenticator,
        "exchange_auth_code",
        lambda self, **kwargs: ShoonyaAuthenticatedSession("SH12345", "SH12345", "token"),
    )
    client = FakeClient(uid="SH12345", account_id="SH12345", access_token="token")
    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)
    claims, connected = [], []
    monkeypatch.setattr(
        shoonya_routes,
        "claim_authenticated_account",
        lambda db, broker, account: claims.append((broker, account)),
    )
    monkeypatch.setattr(
        shoonya_routes,
        "set_owner_connection_state",
        lambda db, value: connected.append(value),
    )
    monkeypatch.setattr(shoonya_routes.trading_safety, "disable", lambda user_id: None)

    result = shoonya_routes.shoonya_verify(
        shoonya_routes.ShoonyaVerifyRequest(
            client_id="client-100",
            secret_code="secret",
            auth_code="auth-code",
            flow_id="flow-id-1234567890",
        ),
        db=db_session,
        user_id=0,
    )

    assert result["connected"] is True
    assert result["uid"] == "SH12345"
    assert result["account_id"] == "SH12345"
    assert result["real_trading"] is False
    assert result["kill_switch"] is True
    assert claims == [("shoonya", "SH12345")]
    assert connected == [True]
    assert store.consume.called
    assert broker_sessions.get(0, "shoonya").connected is True
    cleanup()


def test_verify_rejects_replayed_flow_before_network(monkeypatch, db_session):
    store = Mock()
    store.consume.side_effect = ValueError("replayed")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    exchange = Mock()
    monkeypatch.setattr(shoonya_routes.ShoonyaOAuthAuthenticator, "exchange_auth_code", exchange)

    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_verify(
            shoonya_routes.ShoonyaVerifyRequest(
                client_id="client-100",
                secret_code="secret",
                auth_code="auth-code",
                flow_id="flow-id-1234567890",
            ),
            db=db_session,
            user_id=0,
        )

    assert raised.value.status_code == 401
    exchange.assert_not_called()


def test_shoonya_rest_client_clears_token_and_logs_out():
    from app.brokers.shoonya_routes import ShoonyaRESTClient

    http = Mock()
    client = ShoonyaRESTClient(
        uid="SH12345",
        account_id="SH12345",
        access_token="secret-token",
        http=http,
    )
    client.disconnect()
    assert client.access_token == ""
    http.post.assert_called_once()
    assert http.post.call_args.args[0].endswith("/Logout")


def test_owner_conflict_discards_session(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    monkeypatch.setattr(
        shoonya_routes.ShoonyaOAuthAuthenticator,
        "exchange_auth_code",
        lambda self, **kwargs: ShoonyaAuthenticatedSession("SH12345", "SH12345", "token"),
    )
    client = FakeClient(uid="SH12345", account_id="SH12345", access_token="token")
    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)
    monkeypatch.setattr(
        shoonya_routes,
        "claim_authenticated_account",
        lambda *args: (_ for _ in ()).throw(HTTPException(status_code=409, detail="owner conflict")),
    )

    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_verify(
            shoonya_routes.ShoonyaVerifyRequest(
                client_id="client-100",
                secret_code="secret",
                auth_code="auth-code",
                flow_id="flow-id-1234567890",
            ),
            db=db_session,
            user_id=0,
        )

    assert raised.value.status_code == 409
    assert client.disconnected is True
    assert broker_sessions.get(0, "shoonya") is None
