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
