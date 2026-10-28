import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.scanner import box_spread_paper_routes as routes


def test_paper_quote_selector_uses_ask_for_buys_and_bid_for_sells():
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40, "low_put_ask": 41,
    }
    assert routes._reference_prices_for_direction(row, "LONG") == {
        "LOW_CE": 11, "HIGH_CE": 20, "HIGH_PE": 31, "LOW_PE": 40,
    }
    assert routes._reference_prices_for_direction(row, "SHORT") == {
        "LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41,
    }


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-10-29", "2026-10-29"),
    ("29OCT2026", "2026-10-29"),
    ("29-Oct-26", "2026-10-29"),
    ("20261029", "2026-10-29"),
])
def test_paper_expiry_comparison_canonicalizes_instrument_master_formats(raw, expected):
    assert routes._canonical_expiry(raw) == expected


def test_paper_error_handler_preserves_http_status():
    with pytest.raises(HTTPException) as raised:
        routes._error(HTTPException(status_code=409, detail="stale quotes"))
    assert raised.value.status_code == 409


def test_simulation_rejects_missing_fresh_backend_opportunity_before_execution(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="missing-live-row", account_key="isolated-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": []})

    async def forbidden_execution(**kwargs):
        pytest.fail("paper engine must not start without a matching fresh backend row")

    monkeypatch.setattr(routes._engine, "create_intent", forbidden_execution)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(
            123,
            routes.SimulateEntry(
                account_key="isolated-test",
                reference_prices={
                    "LOW_CE": 1, "HIGH_CE": 1, "HIGH_PE": 1, "LOW_PE": 1,
                },
            ),
        ))

    assert raised.value.status_code == 409
    assert "No matching fresh live Box Spread opportunity" in raised.value.detail


def test_simulation_blocks_duplicate_replay_when_durable_fills_exist(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="already-filled", account_key="isolated-test",
    )
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(
        routes._ledger, "list_fills",
        lambda trade_id, account_key="default": [object()],
    )

    def forbidden(*args, **kwargs):
        pytest.fail("duplicate entry must be blocked before resolving contracts or reading new quotes")

    monkeypatch.setattr(routes, "_resolve", forbidden)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", forbidden)

    async def forbidden_execution(**kwargs):
        pytest.fail("duplicate entry must not execute again")

    monkeypatch.setattr(routes._engine, "create_intent", forbidden_execution)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(
            123, routes.SimulateEntry(account_key="isolated-test"),
        ))

    assert raised.value.status_code == 409
    assert "already has durable fills" in raised.value.detail


def test_simulation_uses_backend_quote_sides_not_browser_prices(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="backend-prices-only", account_key="isolated-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "29OCT2026",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40, "low_put_ask": 41,
    }
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes._ledger, "list_fills", lambda trade_id, account_key="default": [])
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: {"status": "FILLED"})

    async def create_intent(**kwargs):
        return None

    captured = {}

    async def execute_intent(**kwargs):
        captured.update(kwargs["reference_prices"])
        return SimpleNamespace(fills=[], latency_seconds=0.2, slippage_rate=0.0005)

    monkeypatch.setattr(routes._engine, "create_intent", create_intent)
    monkeypatch.setattr(routes._engine, "execute_intent", execute_intent)

    result = asyncio.run(routes.simulate_entry(
        123,
        routes.SimulateEntry(
            account_key="isolated-test",
            reference_prices={"LOW_CE": 999999, "HIGH_CE": 999999, "HIGH_PE": 999999, "LOW_PE": 999999},
        ),
    ))

    assert captured == {"LOW_CE": 11, "HIGH_CE": 20, "HIGH_PE": 31, "LOW_PE": 40}
    assert result["broker_orders_enabled"] is False



def test_exit_uses_fresh_backend_executable_quote_not_browser_price(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="exit-backend-price", account_key="exit-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "29OCT2026",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40, "low_put_ask": 41,
    }
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    captured = {}

    def record_exit_fill(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            trade_id=kwargs["trade_id"], fill_id=kwargs["fill_id"],
            role=kwargs["role"], action="SELL", quantity=kwargs["quantity"],
            fill_price=kwargs["fill_price"], charges=0, cash_delta=0,
            virtual_balance=10000000,
        )

    monkeypatch.setattr(routes._ledger, "record_exit_fill", record_exit_fill)
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: {"status": "OPEN"})
    # Browser price is omitted: the API must use the fresh backend executable quote.
    result = routes.exit_leg(123, routes.ExitLeg(
        role="LOW_CE", quantity=50,
        fill_id="exit-fresh-quote", account_key="exit-test",
    ))

    assert captured["fill_price"] == 10
    assert captured["account_key"] == "exit-test"
    assert result["receipt"]["fill_price"] == "10.0"
    assert result["broker_orders_enabled"] is False


def test_exit_rejects_when_matching_fresh_backend_quote_is_missing(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="exit-no-quote", account_key="exit-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": []})

    def forbidden(**kwargs):
        pytest.fail("exit ledger must not record a fill without a matching fresh quote")

    monkeypatch.setattr(routes._ledger, "record_exit_fill", forbidden)
    with pytest.raises(HTTPException) as raised:
        routes.exit_leg(123, routes.ExitLeg(
            role="LOW_CE", quantity=50, fill_price=1,
            fill_id="exit-no-fresh-quote", account_key="exit-test",
        ))
    assert raised.value.status_code == 409
    assert "No matching fresh live Box Spread opportunity" in raised.value.detail

def test_recovery_endpoint_is_read_only_and_scoped_to_requested_account(monkeypatch):
    open_trades = [
        {"trade_id": 321, "idempotency_key": "durable-key", "underlying": "NIFTY",
         "expiry": "2026-10-29", "status": "PARTIALLY_FILLED", "quantity": 50},
    ]
    requested_accounts = []

    def list_open_trades(*, account_key="default"):
        requested_accounts.append(account_key)
        # Model the ledger's account filter: another account has no open trades.
        return open_trades if account_key == "recovery-test" else []

    monkeypatch.setattr(routes._ledger, "list_open_trades", list_open_trades)

    result = routes.recovery(account_key="recovery-test")
    other_account_result = routes.recovery(account_key="different-account")

    assert requested_accounts == ["recovery-test", "different-account"]
    assert result["mode"] == "PAPER_ONLY"
    assert result["broker_orders_enabled"] is False
    assert result["recovery_mode"] == "READ_ONLY"
    assert result["automatic_replay"] is False
    assert result["count"] == 1
    assert result["data"] == open_trades
    assert other_account_result["count"] == 0
    assert other_account_result["data"] == []


def test_engine_idempotency_key_isolated_by_account_and_trade():
    base = routes._engine_key(
        trade_id=123, account_key="alpha", idempotency_key="same-key",
    )
    other_account = routes._engine_key(
        trade_id=123, account_key="beta", idempotency_key="same-key",
    )
    other_trade = routes._engine_key(
        trade_id=124, account_key="alpha", idempotency_key="same-key",
    )

    assert base != other_account
    assert base != other_trade
    assert len(base) <= 200
    long_key = routes._engine_key(
        trade_id=123, account_key="alpha", idempotency_key="x" * 200,
    )
    assert len(long_key) <= 200
    assert long_key != routes._engine_key(
        trade_id=123, account_key="alpha", idempotency_key="y" * 200,
    )


def test_ledger_fill_id_stays_within_persistent_column_limit_for_long_keys():
    fill_id = routes._ledger_fill_id("x" * 200, "LOW_CE")
    assert len(fill_id) <= 128
    assert fill_id.endswith(":LOW_CE")
    assert fill_id == routes._ledger_fill_id("x" * 200, "LOW_CE")
    assert fill_id != routes._ledger_fill_id("y" * 200, "LOW_CE")


def test_paper_api_endpoints_are_mounted_in_application():
    # Import the production application only for this integration-level assertion.
    from app.main import app

    # FastAPI's newer router implementation may keep included routers lazy
    # in app.routes. OpenAPI resolves the complete mounted route table reliably.
    mounted = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    prefix = "/api/v1/scanner/box-spread-paper"
    expected = {
        ("GET", f"{prefix}/account"),
        ("GET", f"{prefix}/trades"),
        ("POST", f"{prefix}/trades"),
        ("POST", f"{prefix}/trades/{{trade_id}}/simulate-entry"),
        ("POST", f"{prefix}/trades/{{trade_id}}/exit"),
        ("POST", f"{prefix}/trades/{{trade_id}}/settle-expiry"),
        ("GET", f"{prefix}/trades/{{trade_id}}/ledger"),
        ("GET", f"{prefix}/recovery"),
    }
    assert expected <= mounted



def test_private_paper_login_fails_closed_when_password_not_configured(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "")
    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="anything"))
    assert raised.value.status_code == 503


def test_private_paper_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="wrong-password"))
    assert raised.value.status_code == 401


def test_private_paper_login_issues_token_and_guard_accepts_owner_scope(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    result = routes.paper_login(routes.PaperLogin(password="owner-only-test-password"))
    assert result["token_type"] == "bearer"
    claims = routes.require_paper_access("Bearer " + result["access_token"])
    assert claims["sub"] == "box-spread-paper-owner"
    assert claims["scope"] == "box-spread-paper"


def test_private_paper_guard_rejects_missing_and_invalid_tokens(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    for authorization in (None, "Bearer invalid-token"):
        with pytest.raises(HTTPException) as raised:
            routes.require_paper_access(authorization)
        assert raised.value.status_code == 401


def test_all_paper_ledger_routes_have_auth_dependency():
    protected = {
        ("GET", "/account"), ("GET", "/trades"), ("POST", "/trades"),
        ("POST", "/trades/{trade_id}/simulate-entry"),
        ("POST", "/trades/{trade_id}/exit"),
        ("POST", "/trades/{trade_id}/settle-expiry"),
        ("GET", "/trades/{trade_id}/ledger"), ("GET", "/recovery"),
    }
    actual = set()
    for route in routes.router.routes:
        path = route.path.removeprefix(routes.router.prefix)
        methods = getattr(route, "methods", set())
        for method in methods:
            if method in {"GET", "POST"} and path != "/auth/login":
                actual.add((method, path))
                assert any(dependency.call is routes.require_paper_access for dependency in route.dependencies), path
    assert protected <= actual
