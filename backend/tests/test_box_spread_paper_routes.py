import asyncio
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.scanner import box_spread_paper_routes as routes


@pytest.fixture(autouse=True)
def enable_paper_execution_for_existing_route_tests(monkeypatch):
    # Existing execution-path tests exercise ON behavior explicitly; dedicated tests
    # below verify that OFF blocks new entries before any ledger/quote access.
    monkeypatch.setattr(routes, "_paper_execution_enabled", True)



@pytest.mark.parametrize(("limit", "offset"), [
    (0, 0), (501, 0), (10, -1), (10, 1_000_001),
])
def test_paper_trade_pagination_rejects_out_of_range_values(limit, offset):
    with pytest.raises(HTTPException) as raised:
        routes.trades(limit=limit, offset=offset)
    assert raised.value.status_code == 422


@pytest.mark.parametrize("position_state", ["PENDING", "OPENED"])
def test_paper_trade_position_state_rejects_unknown_values(position_state):
    with pytest.raises(HTTPException) as raised:
        routes.trades(position_state=position_state)
    assert raised.value.status_code == 422


def test_empty_paper_portfolio_summary_is_zeroed_and_paper_only(monkeypatch):
    class EmptySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def scalars(self, statement):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(routes, "SessionLocal", EmptySession)
    result = routes.portfolio_summary(account_key="empty-account")

    assert result["mode"] == "PAPER_ONLY"
    assert result["broker_orders_enabled"] is False
    assert result["trade_count"] == 0
    assert result["open_position_count"] == 0
    assert result["closed_position_count"] == 0
    assert result["realized_pnl"] == "0.00"
    assert result["unrealized_pnl"] == "0.00"
    assert result["net_pnl"] == "0.00"
    assert result["total_charges"] == "0.00"



def test_paper_quote_selector_uses_ask_for_buys_and_bid_for_sells():
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
        # LTP values intentionally disagree with the executable quote sides.
        # Entry pricing must never fall back to last-traded prices.
        "low_call_price": 999, "high_call_price": 888,
        "high_put_price": 777, "low_put_price": 666,
        "low_call_ltp": 555, "high_call_ltp": 444,
        "high_put_ltp": 333, "low_put_ltp": 222,
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


def test_matching_live_row_skips_malformed_strikes_without_crashing(monkeypatch):
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    malformed = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-10-29",
        "low_strike": "not-a-number", "high_strike": 25100, "direction": "LONG",
    }
    matching = {
        **malformed, "low_strike": 25000, "high_strike": 25100,
    }
    monkeypatch.setattr(
        routes, "live_box_spread_opportunities",
        lambda **kwargs: {"data": [malformed, matching]},
    )

    assert routes._matching_live_row(contracts) is matching


@pytest.mark.parametrize(("strike_field", "raw_strike"), [
    ("low_strike", None),
    ("low_strike", "bad"),
    ("high_strike", "NaN"),
    ("high_strike", "inf"),
])
def test_simulation_rejects_malformed_live_strikes_without_server_error(monkeypatch, strike_field, raw_strike):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key=f"malformed-live-{strike_field}-{raw_strike}", account_key="isolated-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-10-29",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    row[strike_field] = raw_strike
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes._ledger, "get_entry_claim", lambda **kwargs: None)
    monkeypatch.setattr(routes._ledger, "list_fills", lambda *args, **kwargs: [])
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})

    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(
            123, routes.SimulateEntry(account_key="isolated-test"),
        ))

    assert raised.value.status_code == 409
    assert "No matching fresh live Box Spread opportunity" in raised.value.detail


@pytest.mark.parametrize("raw_strike", [None, "NaN", "inf", "bad"])
def test_strike_match_rejects_nonfinite_or_malformed_values(raw_strike):
    assert routes._strike_matches(raw_strike, 25000.0) is False


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
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes._ledger, "list_fills", lambda trade_id, account_key="default": [])
    monkeypatch.setattr(routes._ledger, "claim_entry_attempt", lambda **kwargs: {"created": True, "status": "CLAIMED"})
    monkeypatch.setattr(routes._ledger, "complete_entry_attempt", lambda **kwargs: None)
    monkeypatch.setattr(routes._ledger, "get_entry_claim", lambda **kwargs: None)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: {"status": "FILLED"})

    captured = {}

    fills = [
        SimpleNamespace(role=role, action=action, quantity=25, fill_price=price,
                         reference_price=price, slippage=0, notional=price * 25, charges=0)
        for role, action, price in (
            ("LOW_CE", "BUY", 11), ("HIGH_CE", "SELL", 20),
            ("HIGH_PE", "BUY", 31), ("LOW_PE", "SELL", 40),
        )
    ]

    async def execute_intent(**kwargs):
        captured.update(kwargs["reference_prices"])
        return SimpleNamespace(fills=fills, latency_seconds=0.2, slippage_rate=0.0005)

    async def create_intent(**kwargs):
        return None

    monkeypatch.setattr(routes._engine, "create_intent", create_intent)
    monkeypatch.setattr(routes._engine, "execute_intent", execute_intent)
    persisted = {}

    def record_entry_basket(**kwargs):
        persisted.update(kwargs["legs"])
        return [SimpleNamespace(charges=0, virtual_balance=100000) for _ in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")]

    monkeypatch.setattr(routes._ledger, "record_entry_basket", record_entry_basket)

    result = asyncio.run(routes.simulate_entry(
        123,
        routes.SimulateEntry(
            account_key="isolated-test",
            reference_prices={"LOW_CE": 999999, "HIGH_CE": 999999, "HIGH_PE": 999999, "LOW_PE": 999999},
        ),
    ))

    assert captured == {"LOW_CE": 11, "HIGH_CE": 20, "HIGH_PE": 31, "LOW_PE": 40}
    assert {role: item["fill_price"] for role, item in persisted.items()} == captured
    assert result["broker_orders_enabled"] is False




def _exit_basket_route_fixtures(monkeypatch, account_key="basket-route-test"):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="exit-basket-route", account_key=account_key,
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "29OCT2026",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    roles = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
    actions = {"LOW_CE": "BUY", "HIGH_CE": "SELL", "HIGH_PE": "BUY", "LOW_PE": "SELL"}
    trade_row = {
        "trade_id": 456,
        "legs": [{
            "role": role, "open_position_quantity": 25,
            "open_position_action": actions[role],
        } for role in roles],
    }
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "_matching_live_row", lambda contracts: row)
    monkeypatch.setattr(routes._ledger, "list_open_trades", lambda account_key: [trade_row])
    return row, trade_row


def test_duplicate_open_leg_role_blocks_preview_and_exit_before_any_write(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    trade_row = next(
        row for row in routes._ledger.list_open_trades(account_key="basket-route-test")
        if row["trade_id"] == 456
    )
    # Keep all four valid roles but append a duplicate role. A dict-based
    # projection would otherwise silently ignore the extra persisted leg.
    trade_row["legs"].append(dict(trade_row["legs"][0]))
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: pytest.fail("malformed four-leg projection must never write an exit"),
    )
    with pytest.raises(HTTPException) as raised:
        routes.preview_exit_basket(456, account_key="basket-route-test")
    assert raised.value.status_code == 409
    assert "exactly four distinct" in raised.value.detail


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_unequal_open_leg_quantities_block_preview_and_exit_before_any_write(monkeypatch, operation):
    _exit_basket_route_fixtures(monkeypatch)
    trade_row = next(
        row for row in routes._ledger.list_open_trades(account_key="basket-route-test")
        if row["trade_id"] == 456
    )
    trade_row["legs"][-1]["open_position_quantity"] = 24
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: pytest.fail("unequal four-leg quantities must never write an exit"),
    )
    if operation == "preview":
        action = lambda: routes.preview_exit_basket(456, account_key="basket-route-test")
    else:
        action = lambda: routes.exit_basket(456, routes.ExitBasket(
            account_key="basket-route-test", fill_id="unequal-confirm",
            order_type="LIMIT",
        ))
    with pytest.raises(HTTPException) as raised:
        action()
    assert raised.value.status_code == 409
    assert "unequal" in raised.value.detail


def test_exit_preview_returns_all_four_executable_bid_ask_quotes_without_writing(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: {
        "realized_pnl": Decimal("10.00"), "unrealized_pnl": Decimal("20.00"),
    })
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("preview must not write fills")),
    )

    result = routes.preview_exit_basket(456, account_key="basket-route-test")
    assert result["order_type_default"] == "LIMIT"
    assert result["quantity_per_leg"] == 25
    quotes = {leg["role"]: leg for leg in result["legs"]}
    assert set(quotes) == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert quotes["LOW_CE"]["executable_price"] == 10
    assert quotes["HIGH_CE"]["executable_price"] == 21
    assert quotes["HIGH_PE"]["executable_price"] == 30
    assert quotes["LOW_PE"]["executable_price"] == 41
    assert result["estimated_realized_plus_close_pnl"] == "30.00"


def test_exit_basket_rejects_one_unmarketable_limit_without_any_fill(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unmarketable basket must not write")),
    )
    payload = routes.ExitBasket(
        account_key="basket-route-test", fill_id="limit-no-fill",
        order_type="LIMIT",
        limit_prices={"LOW_CE": 999, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41},
    )
    with pytest.raises(HTTPException) as raised:
        routes.exit_basket(456, payload)
    assert raised.value.status_code == 409


def test_exit_basket_confirms_all_four_legs_in_one_ledger_call(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    captured = {}
    receipts = [
        SimpleNamespace(
            fill_id=f"EXIT:BASKET:manual:{role}", role=role,
            action="SELL" if role in {"LOW_CE", "HIGH_PE"} else "BUY",
            quantity=25, fill_price=price, charges=0, cash_delta=0,
        )
        for role, price in (("LOW_CE", 10), ("HIGH_CE", 21), ("HIGH_PE", 30), ("LOW_PE", 41))
    ]
    def record_exit_basket(**kwargs):
        captured.update(kwargs)
        return receipts
    monkeypatch.setattr(routes._ledger, "record_exit_basket", record_exit_basket)
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: {"status": "CLOSED"})

    result = routes.exit_basket(456, routes.ExitBasket(
        account_key="basket-route-test", fill_id="manual",
        order_type="LIMIT",
    ))
    assert set(captured["legs"]) == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert result["status"] == "CLOSED"
    assert len(result["receipts"]) == 4


def test_legacy_single_leg_exit_is_blocked_without_any_ledger_write(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("single-leg endpoint must never write a partial Box Spread exit")

    monkeypatch.setattr(routes._ledger, "record_exit_fill", forbidden)
    with pytest.raises(HTTPException) as raised:
        routes.exit_leg(123, routes.ExitLeg(
            role="LOW_CE", quantity=50, fill_price=1,
            fill_id="legacy-single-leg", account_key="exit-test",
        ))
    assert raised.value.status_code == 409
    assert "four-leg exit preview" in raised.value.detail


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


def test_expiry_settlement_fails_closed_without_trusted_server_source(monkeypatch):
    payload = routes.ExpirySettlement(
        settlement_date=__import__("datetime").date(2026, 10, 29),
        settlement_prices={"LOW_CE": 999999, "HIGH_CE": 0, "HIGH_PE": 999999, "LOW_PE": 0},
        account_key="isolated-test",
    )

    def forbidden_settlement(**kwargs):
        pytest.fail("unverified client prices must never reach the ledger")

    monkeypatch.setattr(routes._ledger, "settle_expiry", forbidden_settlement)
    with pytest.raises(HTTPException) as raised:
        routes.settle_expiry(123, payload)

    assert raised.value.status_code == 503
    assert "verified server-side" in raised.value.detail


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
        ("GET", f"{prefix}/rules"),
        ("POST", f"{prefix}/rules"),
        ("PUT", f"{prefix}/rules/{{rule_id}}"),
        ("GET", f"{prefix}/rules/{{rule_id}}/evaluations"),
        ("GET", "/api/v1/scanner/box-spread/monitor"),
    }
    assert expected <= mounted




def test_private_paper_login_rate_limits_failed_attempts_per_peer_and_clears_on_success(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    peer = SimpleNamespace(client=SimpleNamespace(host="198.51.100.42"))
    routes._clear_paper_login_attempts("198.51.100.42")

    for _ in range(routes._LOGIN_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as raised:
            routes.paper_login(routes.PaperLogin(password="wrong-password"), peer)
        assert raised.value.status_code == 401

    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="owner-only-test-password"), peer)
    assert raised.value.status_code == 429
    assert int(raised.value.headers["Retry-After"]) > 0

    # A successful login clears that peer's failed-attempt bucket once the window
    # has elapsed; force expiry here rather than sleeping in a regression test.
    routes._login_attempts["198.51.100.42"] = [
        stamp - routes._LOGIN_WINDOW_SECONDS - 1
        for stamp in routes._login_attempts["198.51.100.42"]
    ]
    result = routes.paper_login(routes.PaperLogin(password="owner-only-test-password"), peer)
    assert result["token_type"] == "bearer"
    assert "198.51.100.42" not in routes._login_attempts


def test_private_paper_login_trusts_forwarded_chain_only_from_configured_proxy(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    # The left-most value is a spoofed client-supplied prefix. The proxy appends
    # the actual client address, so walking from the right selects that address.
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"x-forwarded-for": "203.0.113.250, 198.51.100.44"},
    )
    routes._clear_paper_login_attempts("198.51.100.44")

    for _ in range(routes._LOGIN_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as raised:
            routes.paper_login(routes.PaperLogin(password="wrong-password"), request)
        assert raised.value.status_code == 401

    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="owner-only-test-password"), request)
    assert raised.value.status_code == 429
    assert "198.51.100.44" in routes._login_attempts
    assert "203.0.113.250" not in routes._login_attempts
    routes._clear_paper_login_attempts("198.51.100.44")


def test_private_paper_login_ignores_forwarded_header_from_untrusted_peer(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    request = SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.43"),
        headers={"x-forwarded-for": "203.0.113.250"},
    )
    routes._clear_paper_login_attempts("198.51.100.43")

    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="wrong-password"), request)
    assert raised.value.status_code == 401
    assert "198.51.100.43" in routes._login_attempts
    assert "203.0.113.250" not in routes._login_attempts
    routes._clear_paper_login_attempts("198.51.100.43")


def test_private_paper_login_uses_socket_peer_not_forwarded_header(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    peer = SimpleNamespace(client=SimpleNamespace(host="198.51.100.43"))
    routes._clear_paper_login_attempts("198.51.100.43")
    for _ in range(routes._LOGIN_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as raised:
            routes.paper_login(routes.PaperLogin(password="wrong-password"), peer)
        assert raised.value.status_code == 401
    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="owner-only-test-password"), peer)
    assert raised.value.status_code == 429
    # No forwarded-address input is consulted by the login function.
    assert "198.51.100.43" in routes._login_attempts
    routes._clear_paper_login_attempts("198.51.100.43")

def test_private_paper_login_fails_closed_when_password_not_configured(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "")
    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="anything"))
    assert raised.value.status_code == 503


def test_private_paper_login_fails_closed_for_weak_configured_password(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "short-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    with pytest.raises(HTTPException) as raised:
        routes.paper_login(routes.PaperLogin(password="short-password"))
    assert raised.value.status_code == 503
    assert "16 characters" in raised.value.detail


def test_private_paper_guard_fails_closed_for_weak_configured_password(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "short-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    with pytest.raises(HTTPException) as raised:
        routes.require_paper_access("Bearer any-token")
    assert raised.value.status_code == 503
    assert "16 characters" in raised.value.detail


def test_private_paper_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
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
                assert any(dependency.dependency is routes.require_paper_access for dependency in route.dependencies), path
    assert protected <= actual


def test_private_paper_guard_rejects_expired_token(monkeypatch):
    from datetime import timedelta

    from app.core.security import create_access_token

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    expired = create_access_token(
        {"sub": "box-spread-paper-owner", "scope": "box-spread-paper"},
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(HTTPException) as raised:
        routes.require_paper_access("Bearer " + expired)
    assert raised.value.status_code == 401
    assert "expired" in raised.value.detail.lower() or "invalid" in raised.value.detail.lower()


@pytest.mark.parametrize(("subject", "scope"), [
    ("box-spread-paper-owner", "general-api"),
    ("another-user", "box-spread-paper"),
])
def test_private_paper_guard_rejects_wrong_scope_or_subject(monkeypatch, subject, scope):
    from app.core.security import create_access_token

    monkeypatch.setattr(routes.settings, "BOX_SPREAD_PAPER_PASSWORD", "owner-only-test-password")
    monkeypatch.setattr(routes.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    token = create_access_token({"sub": subject, "scope": scope})
    with pytest.raises(HTTPException) as raised:
        routes.require_paper_access("Bearer " + token)
    assert raised.value.status_code == 401
    assert "session" in raised.value.detail.lower()


def test_simulate_entry_rejects_existing_durable_claim_before_engine_execution(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="already-claimed", account_key="isolated-test",
    )
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes._ledger, "list_fills", lambda trade_id, account_key="default": [])
    monkeypatch.setattr(routes._ledger, "get_entry_claim", lambda **kwargs: {"status": "CLAIMED"})

    def forbidden(*args, **kwargs):
        pytest.fail("a durable entry claim must block execution before quote/engine work")

    monkeypatch.setattr(routes, "_resolve", forbidden)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", forbidden)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(123, routes.SimulateEntry(account_key="isolated-test")))
    assert raised.value.status_code == 409
    assert "durably claimed" in raised.value.detail


def test_simulate_entry_serializes_concurrent_requests_for_same_trade(monkeypatch):
    active = 0
    maximum_active = 0
    calls = 0

    async def fake_locked_entry(trade_id, payload):
        nonlocal active, maximum_active, calls
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        calls += 1
        return {"trade_id": trade_id, "ok": True}

    monkeypatch.setattr(routes, "_simulate_entry_locked", fake_locked_entry)
    payload = routes.SimulateEntry(account_key="concurrency-test")

    async def invoke_both():
        return await asyncio.gather(
            routes.simulate_entry(987654, payload),
            routes.simulate_entry(987654, payload),
        )

    results = asyncio.run(invoke_both())

    assert len(results) == 2
    assert calls == 2
    assert maximum_active == 1


def test_simulation_atomic_basket_write_failure_blocks_replay_without_partial_fills(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="partial-ledger-write", account_key="partial-write-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "29OCT2026",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    durable_fills = []
    claim_state = {"claimed": False}
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, account_key: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(
        routes._ledger, "get_entry_claim",
        lambda **kwargs: {"status": "CLAIMED"} if claim_state["claimed"] else None,
    )
    monkeypatch.setattr(
        routes._ledger, "claim_entry_attempt",
        lambda **kwargs: claim_state.update(claimed=True) or {"created": True, "status": "CLAIMED"},
    )
    monkeypatch.setattr(
        routes._ledger, "list_fills",
        lambda trade_id, account_key="default": list(durable_fills),
    )

    async def create_intent(**kwargs):
        return None

    fills = [
        SimpleNamespace(role=role, action=action, quantity=1, fill_price=price,
                         reference_price=price, slippage=0, notional=price, charges=0)
        for role, action, price in (
            ("LOW_CE", "BUY", 11), ("HIGH_CE", "SELL", 20),
            ("HIGH_PE", "BUY", 31), ("LOW_PE", "SELL", 40),
        )
    ]

    async def execute_intent(**kwargs):
        return SimpleNamespace(fills=fills, latency_seconds=0.2, slippage_rate=0.0005)

    monkeypatch.setattr(routes._engine, "create_intent", create_intent)
    monkeypatch.setattr(routes._engine, "execute_intent", execute_intent)

    def record_entry_basket(**kwargs):
        # The ledger uses one database transaction for all four legs. A failed
        # basket write must leave no durable partial fills behind.
        raise RuntimeError("simulated database failure during atomic four-leg basket write")

    monkeypatch.setattr(routes._ledger, "record_entry_basket", record_entry_basket)

    with pytest.raises(HTTPException):
        asyncio.run(routes.simulate_entry(
            456, routes.SimulateEntry(account_key="partial-write-test"),
        ))

    assert durable_fills == []

    async def forbidden_execution(**kwargs):
        pytest.fail("entry replay must be blocked after a durable entry claim, even when atomic write left no fills")

    monkeypatch.setattr(routes._engine, "create_intent", forbidden_execution)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(
            456, routes.SimulateEntry(account_key="partial-write-test"),
        ))
    assert raised.value.status_code == 409
    assert "durably claimed" in raised.value.detail



def test_manual_paper_execution_switch_defaults_off_and_requires_explicit_enable(monkeypatch):
    monkeypatch.setattr(routes, "_paper_execution_enabled", False)
    assert routes.get_execution_control()["enabled"] is False
    enabled = routes.set_execution_control(routes.PaperExecutionControl(enabled=True))
    assert enabled["enabled"] is True
    assert enabled["broker_orders_enabled"] is False
    disabled = routes.set_execution_control(routes.PaperExecutionControl(enabled=False))
    assert disabled["enabled"] is False
    assert disabled["restart_default"] == "OFF"


def test_simulate_entry_is_blocked_before_ledger_or_quote_access_when_switch_off(monkeypatch):
    monkeypatch.setattr(routes, "_paper_execution_enabled", False)

    def forbidden(*args, **kwargs):
        pytest.fail("OFF must block entry before loading trade, quotes, or ledger")

    monkeypatch.setattr(routes, "_load_trade_request", forbidden)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(123, routes.SimulateEntry(account_key="toggle-off-test")))
    assert raised.value.status_code == 423
    assert "OFF" in raised.value.detail

def test_switching_off_during_entry_preparation_prevents_durable_claim(monkeypatch):
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="toggle-race-test", account_key="toggle-race-test",
    )
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    row = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "29OCT2026",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    monkeypatch.setattr(routes, "_paper_execution_enabled", True)
    monkeypatch.setattr(routes, "_load_trade_request", lambda *args: request)
    monkeypatch.setattr(routes._ledger, "get_entry_claim", lambda **kwargs: None)
    monkeypatch.setattr(routes._ledger, "list_fills", lambda *args, **kwargs: [])
    claims = []

    def resolve_and_switch_off(payload):
        monkeypatch.setattr(routes, "_paper_execution_enabled", False)
        return contracts

    monkeypatch.setattr(routes, "_resolve", resolve_and_switch_off)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(
        routes._ledger, "claim_entry_attempt",
        lambda **kwargs: claims.append(kwargs) or {"created": True},
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.simulate_entry(
            789, routes.SimulateEntry(account_key="toggle-race-test"),
        ))

    assert raised.value.status_code == 423
    assert claims == []



@pytest.mark.parametrize("bad_price", [0, -1, float("nan"), float("inf"), None, "not-a-price"])
def test_entry_quote_selector_rejects_invalid_prices_before_entry_claim(bad_price):
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    row["low_call_ask"] = bad_price

    with pytest.raises(HTTPException) as raised:
        routes._reference_prices_for_direction(row, "LONG")

    assert raised.value.status_code == 409
    assert "fresh executable entry quote is unavailable for LOW_CE" in raised.value.detail


def test_entry_quote_selector_rejects_missing_executable_quote():
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40,
    }

    with pytest.raises(HTTPException) as raised:
        routes._reference_prices_for_direction(row, "LONG")

    assert raised.value.status_code == 409
    assert "fresh executable entry quote is unavailable for LOW_PE" in raised.value.detail

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("low_call_bid", 12),  # crossed market: bid exceeds ask
        ("high_call_ask", float("nan")),  # even the unselected side must be valid
        ("high_put_bid", 0),
    ],
)
def test_entry_quote_selector_rejects_invalid_two_sided_market(field, value):
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    row[field] = value

    with pytest.raises(HTTPException) as raised:
        routes._reference_prices_for_direction(row, "LONG")

    assert raised.value.status_code == 409
    assert "fresh executable entry quote is unavailable" in raised.value.detail


@pytest.mark.parametrize(
    ("direction", "role", "expected"),
    [
        ("LONG", "LOW_CE", 10),
        ("LONG", "HIGH_CE", 21),
        ("LONG", "HIGH_PE", 30),
        ("LONG", "LOW_PE", 41),
        ("SHORT", "LOW_CE", 11),
        ("SHORT", "HIGH_CE", 20),
        ("SHORT", "HIGH_PE", 31),
        ("SHORT", "LOW_PE", 40),
    ],
)
def test_exit_quote_selector_uses_valid_executable_side(direction, role, expected):
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }

    assert routes._exit_reference_price(row, direction, role) == expected


@pytest.mark.parametrize(
    ("bid_field", "ask_field", "bid", "ask"),
    [
        ("low_call_bid", "low_call_ask", 12, 11),  # crossed
        ("high_call_bid", "high_call_ask", float("nan"), 21),
        ("high_put_bid", "high_put_ask", 30, float("inf")),
        ("low_put_bid", "low_put_ask", 0, 41),
    ],
)
def test_exit_quote_selector_rejects_invalid_two_sided_market(
    bid_field, ask_field, bid, ask,
):
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    row[bid_field] = bid
    row[ask_field] = ask

    with pytest.raises(HTTPException) as raised:
        routes._exit_reference_price(row, "LONG", {
            "low_call_bid": "LOW_CE",
            "high_call_bid": "HIGH_CE",
            "high_put_bid": "HIGH_PE",
            "low_put_bid": "LOW_PE",
        }[bid_field])

    assert raised.value.status_code == 409
    assert "fresh executable exit quote is unavailable" in raised.value.detail

@pytest.mark.parametrize(("direction", "role", "expected"), [
    ("LONG", "LOW_CE", 10), ("LONG", "HIGH_CE", 21),
    ("LONG", "HIGH_PE", 30), ("LONG", "LOW_PE", 41),
    ("SHORT", "LOW_CE", 11), ("SHORT", "HIGH_CE", 20),
    ("SHORT", "HIGH_PE", 31), ("SHORT", "LOW_PE", 40),
])
def test_mark_price_uses_executable_close_side_for_each_signed_leg(direction, role, expected):
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    assert routes._exit_reference_price(row, direction, role) == expected


def test_mark_price_rejects_invalid_two_sided_quote():
    row = {
        "low_call_bid": 12, "low_call_ask": 11,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_put_bid": 30, "high_put_ask": 31,
        "low_put_bid": 40, "low_put_ask": 41,
    }
    with pytest.raises(HTTPException) as raised:
        routes._exit_reference_price(row, "LONG", "LOW_CE")
    assert raised.value.status_code == 409



def test_matching_live_row_reuses_supplied_snapshot_without_scanner_request(monkeypatch):
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000.0, high_strike=25100.0, direction="LONG",
    )
    matching = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-10-29",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
    }

    def forbidden_scanner_call(**kwargs):
        pytest.fail("portfolio must reuse its single shared live snapshot")

    monkeypatch.setattr(routes, "live_box_spread_opportunities", forbidden_scanner_call)
    assert routes._matching_live_row(
        contracts, snapshot={"data": [matching]},
    ) is matching


def test_fresh_marks_can_use_preloaded_trade_and_snapshot(monkeypatch):
    trade = SimpleNamespace(
        id=456, account_key="portfolio-test", underlying="NIFTY",
        instrument_class="INDEX", expiry="2026-10-29",
        low_strike=25000, high_strike=25100, direction="LONG",
        lots=1, entry_spot_price=25050,
    )
    matching = {
        "symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-10-29",
        "low_strike": 25000, "high_strike": 25100, "direction": "LONG",
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    monkeypatch.setattr(
        routes, "_load_trade_request",
        lambda *args, **kwargs: pytest.fail("preloaded trade should avoid another DB read"),
    )
    monkeypatch.setattr(
        routes, "_resolve",
        lambda request: SimpleNamespace(
            underlying="NIFTY", instrument_class="INDEX", expiry="2026-10-29",
            low_strike=25000.0, high_strike=25100.0, direction="LONG",
        ),
    )
    marks = routes._fresh_mark_prices_for_trade(
        456, "portfolio-test", trade_record=trade,
        live_snapshot={"data": [matching]},
    )
    assert marks == {
        "LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41,
    }


def test_exit_preview_recalculates_proposed_limit_prices_and_marks_non_marketable_legs(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    captured = {}
    monkeypatch.setattr(routes._ledger, "get_trade_summary", lambda *args, **kwargs: (
        captured.update(kwargs) or {
            "realized_pnl": Decimal("10.00"), "unrealized_pnl": Decimal("20.00"),
        }
    ))
    payload = routes.ExitPreviewRequest(
        account_key="basket-route-test", order_type="LIMIT",
        limit_prices={"LOW_CE": 9, "HIGH_CE": 20, "HIGH_PE": 30, "LOW_PE": 42},
    )
    result = routes.preview_exit_basket_with_limits(456, payload)
    quotes = {leg["role"]: leg for leg in result["legs"]}
    assert quotes["LOW_CE"]["preview_price"] == 10
    assert quotes["LOW_CE"]["marketable"] is True
    assert quotes["HIGH_CE"]["preview_price"] == 20
    assert quotes["HIGH_CE"]["marketable"] is False
    assert quotes["HIGH_PE"]["preview_price"] == 30
    assert quotes["LOW_PE"]["preview_price"] == 41
    assert result["all_legs_marketable"] is False
    assert result["preview_price_source"] == "INDICATIVE_LIMIT_SCENARIO"
    assert captured["mark_prices"] == {"LOW_CE": 10, "HIGH_CE": 20, "HIGH_PE": 30, "LOW_PE": 41}
    assert result["estimated_realized_plus_close_pnl"] == "30.00"


def test_exit_preview_rejects_incomplete_or_invalid_proposed_limits(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    with pytest.raises(HTTPException) as raised:
        routes.preview_exit_basket_with_limits(456, routes.ExitPreviewRequest(
            account_key="basket-route-test", order_type="LIMIT",
            limit_prices={"LOW_CE": 10},
        ))
    assert raised.value.status_code == 422


def test_market_exit_preview_rejects_limit_prices(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    with pytest.raises(HTTPException) as raised:
        routes.preview_exit_basket_with_limits(456, routes.ExitPreviewRequest(
            account_key="basket-route-test", order_type="MARKET",
            limit_prices={"LOW_CE": 10, "HIGH_CE": 20, "HIGH_PE": 30, "LOW_PE": 40},
        ))
    assert raised.value.status_code == 422
    assert "MARKET preview must not include limit_prices" in raised.value.detail



def test_shared_exit_executor_validates_and_records_all_four_legs_for_market(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: captured.update(kwargs) or [
            SimpleNamespace(fill_id=f"exit-{role}", role=role, action="SELL" if role in {"LOW_CE", "HIGH_PE"} else "BUY",
                             quantity=25, fill_price=kwargs["legs"][role]["fill_price"],
                             charges=Decimal("1.00"), cash_delta=Decimal("10.00"))
            for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
        ],
    )
    monkeypatch.setattr(
        routes._ledger, "get_trade_summary",
        lambda *args, **kwargs: {"status": "CLOSED", "realized_pnl": Decimal("50.00")},
    )

    result = routes._execute_exit_basket_from_row(
        456, "basket-route-test",
        {
            "low_call_bid": 10, "low_call_ask": 11,
            "high_call_bid": 20, "high_call_ask": 21,
            "high_put_bid": 30, "high_put_ask": 31,
            "low_put_bid": 40, "low_put_ask": 41,
        },
        order_type="MARKET", fill_id="shared-market-exit",
    )

    assert result["status"] == "CLOSED"
    assert result["broker_orders_enabled"] is False
    assert result["order_type"] == "MARKET"
    assert set(captured["legs"]) == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert {role: item["fill_price"] for role, item in captured["legs"].items()} == {
        "LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41,
    }


def test_shared_exit_executor_rejects_bad_fourth_leg_before_atomic_write(monkeypatch):
    _exit_basket_route_fixtures(monkeypatch)
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: pytest.fail("invalid four-leg quotes must not write any exit fills"),
    )
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 42, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }
    with pytest.raises(HTTPException) as raised:
        routes._execute_exit_basket_from_row(
            456, "basket-route-test", row,
            order_type="LIMIT", fill_id="invalid-fourth-leg",
        )
    assert raised.value.status_code == 409


@pytest.mark.parametrize(
    ("role", "limit_prices"),
    [
        ("LOW_CE", {"LOW_CE": 11, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 41}),
        ("HIGH_CE", {"LOW_CE": 10, "HIGH_CE": 20, "HIGH_PE": 30, "LOW_PE": 41}),
        ("HIGH_PE", {"LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 31, "LOW_PE": 41}),
        ("LOW_PE", {"LOW_CE": 10, "HIGH_CE": 21, "HIGH_PE": 30, "LOW_PE": 40}),
    ],
)
def test_all_or_none_exit_rejects_basket_if_any_one_leg_is_not_marketable(
    monkeypatch, role, limit_prices,
):
    _exit_basket_route_fixtures(monkeypatch)
    writes = []
    monkeypatch.setattr(
        routes._ledger, "record_exit_basket",
        lambda **kwargs: writes.append(kwargs) or pytest.fail(
            "AON basket must not record any leg when one leg cannot execute"
        ),
    )
    row = {
        "low_call_bid": 10, "low_call_ask": 11,
        "low_call_bid_qty": 25, "low_call_ask_qty": 25,
        "high_call_bid": 20, "high_call_ask": 21,
        "high_call_bid_qty": 25, "high_call_ask_qty": 25,
        "high_put_bid": 30, "high_put_ask": 31,
        "high_put_bid_qty": 25, "high_put_ask_qty": 25,
        "low_put_bid": 40, "low_put_ask": 41,
        "low_put_bid_qty": 25, "low_put_ask_qty": 25,
    }

    with pytest.raises(HTTPException) as raised:
        routes._execute_exit_basket_from_row(
            456, "basket-route-test", row,
            order_type="LIMIT", limit_prices=limit_prices,
            fill_id=f"aon-non-marketable-{role}",
        )

    assert raised.value.status_code == 409
    assert "no legs were exited" in raised.value.detail
    assert writes == []
