from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.brokers import shoonya_routes
from app.brokers.shoonya_auth import ShoonyaAuthenticatedSession
from app.brokers.connections import broker_connections
from app.brokers.session_store import BrokerSessionStore, broker_sessions


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


@pytest.fixture(autouse=True)
def configure_server_side_shoonya_oauth_credentials(monkeypatch):
    # OAuth route tests exercise configured server credentials by default;
    # tests for missing configuration explicitly override these settings.
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "server-client")
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_SECRET_CODE", "server-secret")


def test_auth_url_returns_authorize_url_and_correlated_flow(monkeypatch):
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "client-100")
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
        owner_id=0,
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
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "server-client")
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_SECRET_CODE", "server-secret")
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    exchange_calls = []
    def exchange(self, **kwargs):
        exchange_calls.append(kwargs)
        return ShoonyaAuthenticatedSession("SH12345", "SH12345", "token")
    monkeypatch.setattr(shoonya_routes.ShoonyaOAuthAuthenticator, "exchange_auth_code", exchange)
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
            client_id="untrusted-request-client",
            secret_code="untrusted-request-secret",
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
    assert exchange_calls == [{"client_id": "server-client", "secret_code": "server-secret", "auth_code": "auth-code", "expected_uid": "SH12345", "expected_account_id": "SH12345"}]
    store.consume.assert_called_once_with(
        owner_id=0,
        flow_id="flow-id-1234567890",
        client_id="server-client",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert broker_sessions.get(0, "shoonya").connected is True
    cleanup()


def test_superseded_oauth_attempt_does_not_claim_owner_or_connection(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
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

    def supersede_during_oauth(self, **kwargs):
        # Simulate a later reconnect beginning while this OAuth exchange is slow.
        broker_sessions.reserve_replacement(0, "shoonya")
        return ShoonyaAuthenticatedSession("SH12345", "SH12345", "token")

    monkeypatch.setattr(
        shoonya_routes.ShoonyaOAuthAuthenticator,
        "exchange_auth_code",
        supersede_during_oauth,
    )
    client = FakeClient(uid="SH12345", account_id="SH12345", access_token="token")
    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)

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
    assert claims == []
    assert connected == []
    assert broker_sessions.get(0, "shoonya") is None
    assert client.disconnected is True
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


def test_shoonya_rest_client_uses_bearer_and_jdata_for_read_only_books():
    from app.brokers.shoonya_routes import ShoonyaRESTClient

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    http = Mock()
    http.post.side_effect = [
        Response({"stat": "Ok", "uid": "SH12345", "actid": "SH12345"}),
        Response([{"stat": "Ok", "norenordno": "O1"}]),
        Response([{"stat": "Ok", "flid": "T1"}]),
    ]
    client = ShoonyaRESTClient(
        uid="SH12345",
        account_id="SH12345",
        access_token="token-value",
        http=http,
    )

    assert client.get_user_details()["uid"] == "SH12345"
    assert client.get_order_book()[0]["norenordno"] == "O1"
    assert client.get_trade_book()[0]["flid"] == "T1"

    calls = http.post.call_args_list
    assert calls[0].args[0].endswith("/UserDetails")
    assert calls[1].args[0].endswith("/OrderBook")
    assert calls[2].args[0].endswith("/TradeBook")
    assert all(call.kwargs["headers"]["Authorization"] == "Bearer token-value" for call in calls)
    assert all(call.kwargs["headers"]["Content-Type"] == "text/plain" for call in calls)
    assert calls[0].kwargs["data"] == 'jData={"uid":"SH12345"}'
    assert calls[1].kwargs["data"] == 'jData={"uid":"SH12345"}'
    assert calls[2].kwargs["data"] == 'jData={"uid":"SH12345","actid":"SH12345"}'


def test_auth_url_uses_server_configured_client_id_when_request_blank(monkeypatch):
    store = Mock()
    store.create.return_value = "flow-id-server-config"
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "configured-client")
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")

    result = shoonya_routes.shoonya_auth_url(
        shoonya_routes.ShoonyaAuthUrlRequest(
            uid="SH12345",
            account_id="SH12345",
        ),
        user_id=0,
    )

    assert "client_id=configured-client" in result["authorization_url"]
    store.create.assert_called_once_with(
        owner_id=0,
        client_id="configured-client",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )


def test_verify_requires_server_credentials_even_when_request_supplies_credentials(monkeypatch, db_session):
    store = Mock()
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_SECRET_CODE", "")
    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_verify(
            shoonya_routes.ShoonyaVerifyRequest(
                client_id="untrusted-client",
                secret_code="request-body-secret",
                auth_code="auth-code",
                flow_id="flow-id-1234567890",
            ),
            db=db_session,
            user_id=0,
        )
    assert raised.value.status_code == 503
    store.consume.assert_not_called()


def test_verify_rejects_request_only_client_id_when_server_client_id_missing(monkeypatch, db_session):
    store = Mock()
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_SECRET_CODE", "configured-secret")

    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_verify(
            shoonya_routes.ShoonyaVerifyRequest(
                client_id="untrusted-request-client",
                secret_code="untrusted-request-secret",
                auth_code="auth-code",
                flow_id="flow-id-1234567890",
            ),
            db=db_session,
            user_id=0,
        )

    assert raised.value.status_code == 503
    store.consume.assert_not_called()


def test_auth_url_rejects_request_only_client_id(monkeypatch):
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_auth_url(
            shoonya_routes.ShoonyaAuthUrlRequest(
                client_id="untrusted-request-client",
                uid="SH12345",
                account_id="SH12345",
            ),
            user_id=0,
        )
    assert raised.value.status_code == 503


def test_auth_url_prefers_server_client_id_over_request_value(monkeypatch):
    store = Mock()
    store.create.return_value = "flow-id-env-priority"
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setattr(shoonya_routes.settings, "SHOONYA_OAUTH_CLIENT_ID", "server-client")
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")

    result = shoonya_routes.shoonya_auth_url(
        shoonya_routes.ShoonyaAuthUrlRequest(
            client_id="untrusted-request-client",
            uid="SH12345",
            account_id="SH12345",
        ),
        user_id=0,
    )

    assert "client_id=server-client" in result["authorization_url"]
    assert "untrusted-request-client" not in result["authorization_url"]
    store.create.assert_called_once_with(
        owner_id=0,
        client_id="server-client",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )


def test_rest_client_expired_token_is_cleared_and_not_sent(monkeypatch):
    from app.brokers.shoonya_routes import ShoonyaRESTClient

    http = Mock()
    client = ShoonyaRESTClient(
        uid="SH12345",
        account_id="SH12345",
        access_token="expired-token",
        expires_at_monotonic=10.0,
        http=http,
    )
    monkeypatch.setattr(shoonya_routes, "monotonic", lambda: 10.0)
    with pytest.raises(RuntimeError, match="token expired"):
        client.get_user_details()
    assert client.access_token == ""
    assert client.expires_at_monotonic is None
    http.post.assert_not_called()


def test_authenticator_sets_24_hour_monotonic_expiry(monkeypatch):
    from app.brokers import shoonya_auth


    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"stat": "Ok", "susertoken": "tok", "uid": "SH12345", "actid": "SH12345"}

    http = Mock()
    http.post.return_value = Response()
    monkeypatch.setattr(shoonya_auth, "monotonic", lambda: 100.0)
    session = shoonya_auth.ShoonyaOAuthAuthenticator(http=http).exchange_auth_code(
        client_id="cid", secret_code="secret", auth_code="auth", expected_uid="SH12345", expected_account_id="SH12345"
    )
    assert session.expires_at_monotonic == 100.0 + 86400

def test_expired_token_cleanup_skips_remote_logout(monkeypatch):
    from app.brokers.shoonya_routes import ShoonyaRESTClient

    http = Mock()
    client = ShoonyaRESTClient(
        uid="SH12345",
        account_id="SH12345",
        access_token="expired-token",
        expires_at_monotonic=10.0,
        http=http,
    )
    monkeypatch.setattr(shoonya_routes, "monotonic", lambda: 10.0)

    client.disconnect()

    assert client.access_token == ""
    assert client.expires_at_monotonic is None
    http.post.assert_not_called()
    http.close.assert_called_once()

def test_session_replacement_keeps_new_adapter_when_old_disconnect_fails():
    owner_id = 99173

    class OldAdapter(FakeAdapter):
        def disconnect(self):
            raise RuntimeError("provider logout failed")

    old = OldAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="old"))
    new = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="new"))
    broker_sessions.replace(owner_id, "shoonya", old)

    error = broker_sessions.replace_and_disconnect_previous(owner_id, "shoonya", new)

    assert isinstance(error, RuntimeError)
    assert broker_sessions.get(owner_id, "shoonya") is new
    removed = broker_sessions.remove(owner_id, "shoonya")
    assert removed is new
    new.disconnect()

def test_concurrent_session_replacements_leave_single_active_adapter():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    owner_id = 99174
    barrier = Barrier(2)

    class Adapter:
        name = "shoonya"

        def __init__(self):
            self.disconnected = False

        def disconnect(self):
            self.disconnected = True

    adapters = [Adapter(), Adapter()]

    def replace(adapter):
        barrier.wait(timeout=2)
        return broker_sessions.replace_and_disconnect_previous(owner_id, "shoonya", adapter)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(replace, adapters))
        active = broker_sessions.get(owner_id, "shoonya")
        assert active in adapters
        assert active.disconnected is False
        assert all(result is None for result in results)
    finally:
        broker_sessions.remove(owner_id, "shoonya")

def test_remove_if_current_does_not_remove_replacement_adapter():
    owner_id = 99175
    old = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="old"))
    new = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="new"))
    broker_sessions.replace(owner_id, "shoonya", old)
    broker_sessions.replace(owner_id, "shoonya", new)

    assert broker_sessions.remove_if_current(owner_id, "shoonya", old) is False
    assert broker_sessions.get(owner_id, "shoonya") is new
    assert broker_sessions.remove_if_current(owner_id, "shoonya", new) is True
    assert broker_sessions.get(owner_id, "shoonya") is None

def test_verify_metadata_failure_removes_only_its_owned_session(monkeypatch, db_session):
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
    class CleanupFailingClient(FakeClient):
        def disconnect(self):
            self.disconnected = True
            raise RuntimeError("transport cleanup failed")

    client = CleanupFailingClient(uid="SH12345", account_id="SH12345", access_token="token")
    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)
    monkeypatch.setattr(shoonya_routes.trading_safety, "disable", lambda user_id: None)
    rollback = Mock()
    monkeypatch.setattr(db_session, "rollback", rollback)
    monkeypatch.setattr(
        shoonya_routes.broker_connections,
        "connect",
        Mock(side_effect=RuntimeError("metadata write failed")),
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

    assert raised.value.status_code == 503
    assert broker_sessions.get(0, "shoonya") is None
    assert broker_connections.get(0, "shoonya") is None
    assert client.disconnected is True
    rollback.assert_called_once()

def test_connection_metadata_commit_rejects_superseded_adapter():
    owner_id = 99176
    old = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="old"))
    current = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="current"))
    broker_sessions.replace(owner_id, "shoonya", current)
    committed = []
    assert broker_sessions.commit_if_current(
        owner_id, "shoonya", old, lambda: committed.append("stale")
    ) is False
    assert committed == []
    assert broker_sessions.commit_if_current(
        owner_id, "shoonya", current, lambda: committed.append("current")
    ) is True
    assert committed == ["current"]
    broker_sessions.remove(owner_id, "shoonya")


def test_metadata_commit_lock_blocks_concurrent_session_replacement():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event


    owner_id = 99177
    old = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="old"))
    new = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="new"))
    broker_sessions.replace(owner_id, "shoonya", old)
    operation_entered = Event()
    release_operation = Event()
    replacement_entered = Event()
    committed = []

    def commit_operation():
        operation_entered.set()
        assert release_operation.wait(timeout=2)
        committed.append("old-session-metadata")


    def replace_session():
        replacement_entered.set()
        return broker_sessions.replace(owner_id, "shoonya", new)



    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            commit_future = pool.submit(
                broker_sessions.commit_if_current, owner_id, "shoonya", old, commit_operation
            )
            assert operation_entered.wait(timeout=2)
            replacement_future = pool.submit(replace_session)
            assert replacement_entered.wait(timeout=2)
            # The callback holds the registry lock, so replacement cannot finish
            # until metadata commit has completed.
            assert replacement_future.done() is False
            release_operation.set()
            assert commit_future.result(timeout=2) is True
            replacement_future.result(timeout=2)
        assert committed == ["old-session-metadata"]
        assert broker_sessions.get(owner_id, "shoonya") is new
    finally:
        release_operation.set()
        broker_sessions.remove(owner_id, "shoonya")


def test_reconnect_reservation_prevents_slow_old_oauth_exchange_from_overwriting_new_session():
    owner_id = 99178
    existing = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="existing"))
    older_attempt = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="older"))
    newer_attempt = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="newer"))
    existing.connected = True
    broker_sessions.replace(owner_id, "shoonya", existing)

    older_generation = broker_sessions.reserve_replacement(owner_id, "shoonya")
    newer_generation = broker_sessions.reserve_replacement(owner_id, "shoonya")

    accepted_old, displaced_old = broker_sessions.replace_if_reserved(
        owner_id, "shoonya", older_attempt, older_generation
    )
    assert accepted_old is False
    assert displaced_old is None
    assert broker_sessions.get(owner_id, "shoonya") is existing
    assert older_attempt.connected is False

    accepted_new, displaced_new = broker_sessions.replace_if_reserved(
        owner_id, "shoonya", newer_attempt, newer_generation
    )
    assert accepted_new is True
    assert displaced_new is existing
    assert broker_sessions.get(owner_id, "shoonya") is newer_attempt
    assert existing.connected is True  # install itself never performs network cleanup

    # Only the winning reconnect owns cleanup of the displaced adapter.
    displaced_new.disconnect()
    assert existing.connected is False
    broker_sessions.remove(owner_id, "shoonya")
    newer_attempt.disconnect()


def test_regular_session_mutation_invalidates_pending_reconnect_reservation():
    owner_id = 99179
    pending = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="pending"))
    installed = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="installed"))
    generation = broker_sessions.reserve_replacement(owner_id, "shoonya")
    broker_sessions.replace(owner_id, "shoonya", installed)

    accepted, displaced = broker_sessions.replace_if_reserved(
        owner_id, "shoonya", pending, generation
    )
    assert accepted is False
    assert displaced is None
    assert broker_sessions.get(owner_id, "shoonya") is installed
    broker_sessions.remove(owner_id, "shoonya")


def test_clear_owner_invalidates_only_that_owners_pending_reconnects():
    store = BrokerSessionStore()
    owner_a, owner_b = 99181, 99182
    adapter_a = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="a"))
    adapter_b = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="b"))
    generation_a = store.reserve_replacement(owner_a, "shoonya")
    generation_b = store.reserve_replacement(owner_b, "shoonya")

    drained = store.clear_owner(owner_a)
    assert drained == []
    assert store.reserve_replacement(owner_a, "shoonya") > generation_a
    accepted_a, previous_a = store.replace_if_reserved(owner_a, "shoonya", adapter_a, generation_a)
    assert accepted_a is False
    assert previous_a is None

    accepted_b, previous_b = store.replace_if_reserved(owner_b, "shoonya", adapter_b, generation_b)
    assert accepted_b is True
    assert previous_b is None
    assert store.get(owner_b, "shoonya") is adapter_b
    store.remove(owner_b, "shoonya")
    adapter_b.disconnect()


def test_disconnect_all_continues_after_cleanup_failure_and_invalidates_reservations():
    store = BrokerSessionStore()
    owner_a, owner_b = 99183, 99184
    generation_a = store.reserve_replacement(owner_a, "shoonya")
    generation_b = store.reserve_replacement(owner_b, "shoonya")

    class DisconnectAdapter:
        name = "shoonya"

        def __init__(self, fails=False):
            self.fails = fails
            self.calls = 0

        def disconnect(self):
            self.calls += 1
            if self.fails:
                raise RuntimeError("simulated logout failure")

    failing = DisconnectAdapter(fails=True)
    healthy = DisconnectAdapter()
    store.replace(owner_a, "shoonya", failing)
    store.replace(owner_b, "shoonya", healthy)

    failures = store.disconnect_all()
    assert len(failures) == 1
    assert failures[0][0] == "shoonya"
    assert isinstance(failures[0][1], RuntimeError)
    assert failing.calls == 1
    assert healthy.calls == 1
    assert store.get(owner_a, "shoonya") is None
    assert store.get(owner_b, "shoonya") is None

    pending_a = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="pending-a"))
    pending_b = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="pending-b"))
    accepted_a, _ = store.replace_if_reserved(owner_a, "shoonya", pending_a, generation_a)
    accepted_b, _ = store.replace_if_reserved(owner_b, "shoonya", pending_b, generation_b)
    assert accepted_a is False
    assert accepted_b is False
    pending_a.disconnect()
    pending_b.disconnect()


def test_global_clear_invalidates_inflight_reconnect_reservation_without_generation_reuse():
    store = BrokerSessionStore()
    owner_id = 99180
    pending = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="pending"))
    generation = store.reserve_replacement(owner_id, "shoonya")
    store.clear()
    next_generation = store.reserve_replacement(owner_id, "shoonya")
    assert next_generation > generation
    accepted, previous = store.replace_if_reserved(owner_id, "shoonya", pending, generation)
    assert accepted is False
    assert previous is None
    assert store.get(owner_id, "shoonya") is None


def test_disconnect_clears_local_token_even_when_remote_logout_and_http_close_fail(monkeypatch):
    from app.brokers.shoonya_routes import ShoonyaRESTClient


    http = Mock()
    http.post.side_effect = RuntimeError("logout network failure")
    http.close.side_effect = RuntimeError("close failure")
    client = ShoonyaRESTClient(
        uid="SH12345",
        account_id="SH12345",
        access_token="valid-token",
        expires_at_monotonic=100.0,
        http=http,
    )
    monkeypatch.setattr(shoonya_routes, "monotonic", lambda: 10.0)


    # Cleanup is best-effort: it cannot mask an upstream auth/session error.
    client.disconnect()
    assert client.access_token == ""
    assert client.expires_at_monotonic is None
    http.post.assert_called_once()
    http.close.assert_called_once()



def test_replacement_reservation_rejects_non_integer_and_non_positive_generations_atomically():
    store = BrokerSessionStore()
    owner_id = 99190
    adapter = FakeAdapter(FakeClient(uid="SH12345", account_id="SH12345", access_token="reserved"))
    generation = store.reserve_replacement(owner_id, "shoonya")

    for invalid in (True, False, 0, -1, 1.0, "1", None):
        with pytest.raises(ValueError, match="replacement generation"):
            store.replace_if_reserved(owner_id, "shoonya", adapter, invalid)
        assert store.get(owner_id, "shoonya") is None

    accepted, previous = store.replace_if_reserved(owner_id, "shoonya", adapter, generation)
    assert accepted is True
    assert previous is None
    assert store.get(owner_id, "shoonya") is adapter
    store.remove(owner_id, "shoonya")


def test_verify_closes_oauth_transport_when_exchange_fails(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    monkeypatch.setattr(shoonya_routes.trading_safety, "disable", lambda user_id: None)

    class Transport:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    transport = Transport()

    class Authenticator:
        def __init__(self):
            self.http = transport

        def exchange_auth_code(self, **kwargs):
            raise shoonya_routes.ShoonyaAuthenticationError("rejected")

    monkeypatch.setattr(shoonya_routes, "ShoonyaOAuthAuthenticator", Authenticator)

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
    assert transport.closed is True
    assert broker_sessions.get(0, "shoonya") is None
    cleanup()


def test_verify_closes_oauth_transport_after_successful_exchange(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    monkeypatch.setattr(shoonya_routes.trading_safety, "disable", lambda user_id: None)
    transport = Mock()

    class Authenticator:
        def __init__(self):
            self.http = transport

        def exchange_auth_code(self, **kwargs):
            return ShoonyaAuthenticatedSession("SH12345", "SH12345", "token")

    client = FakeClient(uid="SH12345", account_id="SH12345", access_token="token")
    monkeypatch.setattr(shoonya_routes, "ShoonyaOAuthAuthenticator", Authenticator)
    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", lambda **kwargs: client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)
    monkeypatch.setattr(shoonya_routes, "claim_authenticated_account", lambda *args: None)
    monkeypatch.setattr(shoonya_routes, "set_owner_connection_state", lambda *args: None)

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
    transport.close.assert_called_once_with()
    assert broker_sessions.get(0, "shoonya").connected is True
    cleanup()

def test_authenticator_revokes_issued_token_when_authenticated_identity_mismatches():
    from app.brokers.shoonya_auth import ShoonyaAuthenticationError, ShoonyaOAuthAuthenticator

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    http = Mock()
    http.post.side_effect = [
        Response({"stat": "Ok", "susertoken": "unexpected-token", "uid": "OTHER", "actid": "OTHER"}),
        Response({"stat": "Ok"}),
    ]

    with pytest.raises(ShoonyaAuthenticationError, match="UID does not match"):
        ShoonyaOAuthAuthenticator(http=http).exchange_auth_code(
            client_id="cid",
            secret_code="secret",
            auth_code="auth",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )

    assert http.post.call_count == 2
    logout = http.post.call_args_list[1]
    assert logout.args[0].endswith("/Logout")
    assert logout.kwargs["headers"]["Authorization"] == "Bearer unexpected-token"
    assert logout.kwargs["data"] == 'jData={"uid":"OTHER"}'


def test_authenticator_preserves_identity_error_when_token_revocation_fails():
    from app.brokers.shoonya_auth import ShoonyaAuthenticationError, ShoonyaOAuthAuthenticator

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    http = Mock()
    http.post.side_effect = [
        Response({"stat": "Ok", "susertoken": "unexpected-token", "uid": "SH12345", "actid": "OTHER"}),
        RuntimeError("logout transport unavailable"),
    ]

    with pytest.raises(ShoonyaAuthenticationError, match="account does not match"):
        ShoonyaOAuthAuthenticator(http=http).exchange_auth_code(
            client_id="cid",
            secret_code="secret",
            auth_code="auth",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )

    assert http.post.call_count == 2


def test_older_oauth_exchange_finishing_after_newer_success_cannot_replace_or_disconnect_it(monkeypatch, db_session):
    cleanup()
    store = Mock()
    store.consume.return_value = Mock(uid="SH12345", account_id="SH12345")
    monkeypatch.setattr(shoonya_routes, "shoonya_oauth_flows", store)
    monkeypatch.setenv("SHOONYA_REDIRECT_URI", "https://example.com/shoonya/callback")
    monkeypatch.setattr(shoonya_routes.trading_safety, "disable", lambda user_id: None)
    monkeypatch.setattr(shoonya_routes, "claim_authenticated_account", lambda *args: None)
    monkeypatch.setattr(shoonya_routes, "set_owner_connection_state", lambda *args: None)

    clients = {}
    adapters = {}

    def make_client(**kwargs):
        client = FakeClient(**kwargs)
        clients[kwargs["access_token"]] = client
        return client

    monkeypatch.setattr(shoonya_routes, "ShoonyaRESTClient", make_client)
    monkeypatch.setattr(shoonya_routes, "ShoonyaAdapter", FakeAdapter)

    request = shoonya_routes.ShoonyaVerifyRequest(
        client_id="ignored",
        secret_code="ignored",
        auth_code="auth",
        flow_id="flow-id-1234567890",
    )
    nested_results = []
    exchange_calls = []

    def exchange(self, **kwargs):
        exchange_calls.append(kwargs)
        if len(exchange_calls) == 1:
            # First OAuth request pauses inside token exchange while a newer
            # request completes the full authentication and session install.
            nested_results.append(
                shoonya_routes.shoonya_verify(request, db=db_session, user_id=0)
            )
            return ShoonyaAuthenticatedSession("SH12345", "SH12345", "older-token")
        return ShoonyaAuthenticatedSession("SH12345", "SH12345", "newer-token")

    monkeypatch.setattr(shoonya_routes.ShoonyaOAuthAuthenticator, "exchange_auth_code", exchange)

    with pytest.raises(HTTPException) as raised:
        shoonya_routes.shoonya_verify(request, db=db_session, user_id=0)

    assert raised.value.status_code == 409
    assert nested_results[0]["connected"] is True
    current = broker_sessions.get(0, "shoonya")
    assert current is not None
    assert current.client.access_token == "newer-token"
    assert current.connected is True
    assert clients["older-token"].disconnected is True
    assert clients["newer-token"].disconnected is False
    cleanup()


def test_authenticator_revokes_issued_token_when_identity_fields_are_missing():
    from app.brokers.shoonya_auth import ShoonyaAuthenticationError, ShoonyaOAuthAuthenticator

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    http = Mock()
    http.post.side_effect = [
        Response({"stat": "Ok", "susertoken": "orphan-token", "actid": "SH12345"}),
        Response({"stat": "Ok"}),
    ]

    with pytest.raises(ShoonyaAuthenticationError, match="missing access token, UID or account identity"):
        ShoonyaOAuthAuthenticator(http=http).exchange_auth_code(
            client_id="cid",
            secret_code="secret",
            auth_code="auth",
            expected_uid="SH12345",
            expected_account_id="SH12345",
        )

    assert http.post.call_count == 2
    logout = http.post.call_args_list[1]
    assert logout.args[0].endswith("/Logout")
    assert logout.kwargs["headers"]["Authorization"] == "Bearer orphan-token"
    assert logout.kwargs["data"] == 'jData={"uid":"SH12345"}'
