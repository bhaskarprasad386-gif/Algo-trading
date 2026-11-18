from fastapi import HTTPException

from app.brokers import mstock_routes
from app.brokers.mstock import MStockAuthenticationError
from app.brokers.connections import broker_connections
from app.brokers.session_store import broker_sessions


class FakeAdapter:
    name = "mstock"
    def __init__(self, *, outcome=None, error=None):
        self.outcome = outcome or {"broker": "mstock", "connected": True, "account_id": "M123"}
        self.error = error
        self.connected = False
        self.disconnected = False
        self.login_args = None

    def begin_login(self, **kwargs):
        self.login_args = kwargs
        return {"broker": "mstock", "status": "OTP_REQUIRED"}

    def complete_otp(self, **kwargs):
        if self.error:
            raise self.error
        self.connected = True
        return self.outcome

    def complete_totp(self, **kwargs):
        if self.error:
            raise self.error
        self.connected = True
        return self.outcome

    def disconnect(self):
        self.connected = False
        self.disconnected = True


def _cleanup():
    adapter = broker_sessions.remove(0, "mstock")
    if adapter is not None:
        adapter.disconnect()
    broker_connections.disconnect(0, "mstock")


def test_mstock_login_route_starts_otp_without_persisting_credentials(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(mstock_routes, "MStockAdapter", lambda: adapter)

    result = mstock_routes.mstock_begin_login(
        mstock_routes.MStockLoginRequest(username="client", password="secret"),
        user_id=0,
    )

    assert result == {"broker": "mstock", "status": "OTP_REQUIRED"}
    assert adapter.login_args == {"username": "client", "password": "secret"}
    assert adapter.disconnected is True
    assert broker_sessions.get(0, "mstock") is None


def test_mstock_otp_route_binds_broker_returned_account_and_keeps_trading_off(monkeypatch, db_session):
    _cleanup()
    adapter = FakeAdapter(outcome={"broker": "mstock", "connected": True, "account_id": "M123"})
    claimed = []
    connected = []
    monkeypatch.setattr(mstock_routes, "MStockAdapter", lambda: adapter)
    monkeypatch.setattr(mstock_routes, "claim_authenticated_account", lambda db, broker, account: claimed.append((broker, account)))
    monkeypatch.setattr(mstock_routes, "set_owner_connection_state", lambda db, value: connected.append(value))
    monkeypatch.setattr(mstock_routes.trading_safety, "disable", lambda user_id: None)

    result = mstock_routes.mstock_verify_login(
        mstock_routes.MStockVerifyRequest(
            api_key="api-key", account_id="M123", auth_method="OTP", otp="123456"
        ),
        db=db_session,
        user_id=0,
    )

    assert result["connected"] is True
    assert result["account_id"] == "M123"
    assert result["real_trading"] is False
    assert result["kill_switch"] is True
    assert claimed == [("mstock", "M123")]
    assert connected == [True]
    assert broker_sessions.get(0, "mstock") is adapter
    assert adapter.connected is True
    _cleanup()


def test_mstock_totp_route_uses_totp_exchange(monkeypatch, db_session):
    _cleanup()
    adapter = FakeAdapter()
    calls = []
    adapter.complete_totp = lambda **kwargs: calls.append(kwargs) or adapter.outcome
    monkeypatch.setattr(mstock_routes, "MStockAdapter", lambda: adapter)
    monkeypatch.setattr(mstock_routes, "claim_authenticated_account", lambda *args: None)
    monkeypatch.setattr(mstock_routes, "set_owner_connection_state", lambda *args: None)
    monkeypatch.setattr(mstock_routes.trading_safety, "disable", lambda user_id: None)

    mstock_routes.mstock_verify_login(
        mstock_routes.MStockVerifyRequest(
            api_key="api-key", account_id="M123", auth_method="TOTP", totp="654321"
        ),
        db=db_session,
        user_id=0,
    )

    assert calls == [{"api_key": "api-key", "totp": "654321", "account_id": "M123"}]
    _cleanup()


def test_mstock_account_mismatch_never_creates_session(monkeypatch, db_session):
    _cleanup()
    adapter = FakeAdapter(error=MStockAuthenticationError("account mismatch"))
    monkeypatch.setattr(mstock_routes, "MStockAdapter", lambda: adapter)

    try:
        mstock_routes.mstock_verify_login(
            mstock_routes.MStockVerifyRequest(
                api_key="api-key", account_id="WRONG", auth_method="OTP", otp="123456"
            ),
            db=db_session,
            user_id=0,
        )
        assert False, "expected authentication rejection"
    except HTTPException as exc:
        assert exc.status_code == 401

    assert broker_sessions.get(0, "mstock") is None
    assert adapter.disconnected is True


def test_mstock_owner_conflict_is_preserved_and_session_is_discarded(monkeypatch, db_session):
    _cleanup()
    adapter = FakeAdapter()
    monkeypatch.setattr(mstock_routes, "MStockAdapter", lambda: adapter)
    monkeypatch.setattr(
        mstock_routes,
        "claim_authenticated_account",
        lambda *args: (_ for _ in ()).throw(HTTPException(status_code=409, detail="owner conflict")),
    )

    try:
        mstock_routes.mstock_verify_login(
            mstock_routes.MStockVerifyRequest(
                api_key="api-key", account_id="M123", auth_method="OTP", otp="123456"
            ),
            db=db_session,
            user_id=0,
        )
        assert False, "expected owner conflict"
    except HTTPException as exc:
        assert exc.status_code == 409

    assert broker_sessions.get(0, "mstock") is None
    assert adapter.disconnected is True


def test_mstock_verify_requires_only_selected_auth_factor():
    payload = mstock_routes.MStockVerifyRequest(
        api_key="api-key", account_id="M123", auth_method="OTP", otp="123456", totp="654321"
    )

    try:
        mstock_routes.mstock_verify_login(payload, db=None, user_id=0)
        assert False, "expected input rejection"
    except HTTPException as exc:
        assert exc.status_code == 422
