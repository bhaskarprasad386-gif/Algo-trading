"""Deterministic crash-boundary regressions for Box Spread paper entry."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.scanner import box_spread_paper_routes as routes
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount, BoxSpreadPaperAuditEvent, BoxSpreadPaperEntryClaim,
    BoxSpreadPaperLedger, BoxSpreadPaperLedgerEntry, BoxSpreadPaperTradeRecord,
)


@pytest.fixture
def durable_ledger():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperAccount.__table__, BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__, BoxSpreadPaperEntryClaim.__table__,
        BoxSpreadPaperAuditEvent.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield BoxSpreadPaperLedger(factory)
    finally:
        engine.dispose()


def _trade(ledger, account_key, idempotency_key):
    return ledger.create_trade(
        account_key=account_key, idempotency_key=idempotency_key,
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000, high_strike=25100, direction="LONG",
        lots=1, lot_size=25, alert_edge=8,
    )


def test_entry_basket_second_flush_failure_rolls_back_every_leg_and_balance(durable_ledger):
    ledger = durable_ledger
    trade = _trade(ledger, "crash-rollback", "rollback-entry")
    ledger.claim_entry_attempt(trade_id=trade.id, account_key="crash-rollback")
    starting_balance = ledger.ensure_account("crash-rollback")
    legs = {
        role: {"fill_id": f"rollback-entry:{role}", "action": action,
               "quantity": 25, "fill_price": price}
        for role, action, price in (
            ("LOW_CE", "BUY", "100"), ("HIGH_CE", "SELL", "90"),
            ("HIGH_PE", "BUY", "80"), ("LOW_PE", "SELL", "70"),
        )
    }
    inserted_leg_flushes = 0

    def fail_on_second_leg_flush(session, flush_context, instances):
        nonlocal inserted_leg_flushes
        if any(isinstance(item, BoxSpreadPaperLedgerEntry) for item in session.new):
            inserted_leg_flushes += 1
            if inserted_leg_flushes == 2:
                raise RuntimeError("injected failure after first leg flush")

    event.listen(Session, "before_flush", fail_on_second_leg_flush)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            ledger.record_entry_basket(
                trade_id=trade.id, account_key="crash-rollback", legs=legs,
            )
    finally:
        event.remove(Session, "before_flush", fail_on_second_leg_flush)

    assert inserted_leg_flushes == 2
    assert ledger.list_fills(trade.id, account_key="crash-rollback") == []
    assert ledger.ensure_account("crash-rollback") == starting_balance
    assert ledger.get_entry_claim(
        trade_id=trade.id, account_key="crash-rollback",
    )["status"] == "CLAIMED"
    assert ledger.get_trade_summary(
        trade.id, account_key="crash-rollback",
    )["status"] == "CREATED"


def test_route_failure_after_durable_claim_leaves_manual_review_and_never_retries(monkeypatch, durable_ledger):
    ledger = durable_ledger
    account_key = "crash-after-claim"
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="claim-before-simulator", account_key=account_key, alert_edge=8,
    )
    trade = _trade(ledger, account_key, request.idempotency_key)
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000.0, high_strike=25100.0, direction="LONG", lot_size=25,
    )
    row = {"symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-11-19",
           "low_strike": 25000, "high_strike": 25100, "direction": "LONG"}
    monkeypatch.setattr(routes, "_ledger", ledger)
    monkeypatch.setattr(routes, "_paper_execution_enabled", True)
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, key: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(routes, "_fresh_entry_depth_selection", lambda *args, **kwargs: {
        "lots": 1, "quantity": 25,
        "prices": {"LOW_CE": 100, "HIGH_CE": 90, "HIGH_PE": 80, "LOW_PE": 70},
    })
    monkeypatch.setattr(routes, "validate_entry_edge", lambda **kwargs: {
        "alert_edge": 8, "minimum_entry_edge": 5, "effective_entry_edge": 7,
    })

    async def fail_simulator(**kwargs):
        raise RuntimeError("injected simulator crash after claim")

    monkeypatch.setattr(routes._engine, "create_intent", fail_simulator)
    with pytest.raises(routes.HTTPException):
        asyncio.run(routes._simulate_entry_locked(
            trade.id, routes.SimulateEntry(account_key=account_key),
        ))

    assert ledger.get_entry_claim(trade_id=trade.id, account_key=account_key)["status"] == "CLAIMED"
    assert ledger.list_fills(trade.id, account_key=account_key) == []
    recovered = ledger.list_open_trades(account_key=account_key)
    assert len(recovered) == 1
    assert recovered[0]["manual_review_required"] is True
    assert recovered[0]["automatic_replay"] is False


def test_lost_http_response_after_commit_replays_original_four_receipts(monkeypatch, durable_ledger):
    ledger = durable_ledger
    account_key = "crash-after-commit"
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="commit-response-lost", account_key=account_key, alert_edge=8,
    )
    trade = _trade(ledger, account_key, request.idempotency_key)
    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000.0, high_strike=25100.0, direction="LONG", lot_size=25,
    )
    row = {"symbol": "NIFTY", "instrument_class": "INDEX", "expiry": "2026-11-19",
           "low_strike": 25000, "high_strike": 25100, "direction": "LONG"}
    monkeypatch.setattr(routes, "_ledger", ledger)
    monkeypatch.setattr(routes, "_paper_execution_enabled", True)
    monkeypatch.setattr(routes, "_load_trade_request", lambda trade_id, key: request)
    monkeypatch.setattr(routes, "_load_trade_quantity", lambda trade_id, key: 25)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(routes, "live_box_spread_opportunities", lambda **kwargs: {"data": [row]})
    monkeypatch.setattr(routes, "_fresh_entry_depth_selection", lambda *args, **kwargs: {
        "lots": 1, "quantity": 25,
        "prices": {"LOW_CE": 100, "HIGH_CE": 90, "HIGH_PE": 80, "LOW_PE": 70},
    })
    monkeypatch.setattr(routes, "validate_entry_edge", lambda **kwargs: {
        "alert_edge": 8, "minimum_entry_edge": 5, "effective_entry_edge": 7,
    })

    roles = (("LOW_CE", "BUY", 100), ("HIGH_CE", "SELL", 90),
             ("HIGH_PE", "BUY", 80), ("LOW_PE", "SELL", 70))
    engine_calls = {"create": 0, "execute": 0}

    async def create_intent(**kwargs):
        engine_calls["create"] += 1

    async def execute_intent(**kwargs):
        engine_calls["execute"] += 1
        fills = [SimpleNamespace(
            role=role, action=action, quantity=25, fill_price=Decimal(price),
            reference_price=Decimal(price), slippage=Decimal("0"),
            notional=Decimal(price) * 25,
        ) for role, action, price in roles]
        return SimpleNamespace(fills=fills, latency_seconds=0.01, slippage_rate=Decimal("0"))

    monkeypatch.setattr(routes._engine, "create_intent", create_intent)
    monkeypatch.setattr(routes._engine, "execute_intent", execute_intent)
    real_summary = ledger.get_trade_summary
    summary_calls = {"count": 0}

    def fail_once_after_commit(*args, **kwargs):
        summary_calls["count"] += 1
        if summary_calls["count"] == 1:
            raise RuntimeError("injected lost-response failure after ledger commit")
        return real_summary(*args, **kwargs)

    monkeypatch.setattr(ledger, "get_trade_summary", fail_once_after_commit)
    with pytest.raises(routes.HTTPException):
        asyncio.run(routes._simulate_entry_locked(
            trade.id, routes.SimulateEntry(account_key=account_key),
        ))

    assert len(ledger.list_fills(trade.id, account_key=account_key)) == 4
    assert ledger.get_entry_claim(trade_id=trade.id, account_key=account_key)["status"] == "COMPLETED"

    replay = asyncio.run(routes._simulate_entry_locked(
        trade.id, routes.SimulateEntry(account_key=account_key),
    ))
    assert replay["replayed"] is True
    assert replay["broker_orders_enabled"] is False
    assert {item["role"] for item in replay["fills"]} == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert len(ledger.list_fills(trade.id, account_key=account_key)) == 4
    assert engine_calls == {"create": 1, "execute": 1}



def test_exit_commit_then_lost_response_replays_from_real_durable_ledger(monkeypatch, durable_ledger):
    """A real four-row DB commit survives a lost response and is replayed once."""
    from contextlib import contextmanager

    ledger = durable_ledger
    account_key = "exit-crash-real-commit"
    request = routes.CreatePaperTrade(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000, high_strike=25100, direction="LONG", lots=1,
        idempotency_key="exit-crash-real-commit", account_key=account_key,
    )
    trade = _trade(ledger, account_key, request.idempotency_key)
    entry_legs = {
        "LOW_CE": {"fill_id": "entry:LOW_CE", "action": "BUY", "quantity": 25, "fill_price": 10},
        "HIGH_CE": {"fill_id": "entry:HIGH_CE", "action": "SELL", "quantity": 25, "fill_price": 20},
        "HIGH_PE": {"fill_id": "entry:HIGH_PE", "action": "BUY", "quantity": 25, "fill_price": 30},
        "LOW_PE": {"fill_id": "entry:LOW_PE", "action": "SELL", "quantity": 25, "fill_price": 40},
    }
    ledger.record_entry_basket(trade_id=trade.id, account_key=account_key, legs=entry_legs)

    contracts = SimpleNamespace(
        underlying="NIFTY", instrument_class="INDEX", expiry="2026-11-19",
        low_strike=25000.0, high_strike=25100.0, direction="LONG", lot_size=25,
    )
    trade_row = {
        "trade_id": trade.id,
        "legs": [
            {"role": role, "open_position_quantity": 25, "open_position_action": action}
            for role, action in (
                ("LOW_CE", "BUY"), ("HIGH_CE", "SELL"),
                ("HIGH_PE", "BUY"), ("LOW_PE", "SELL"),
            )
        ],
    }
    monkeypatch.setattr(routes, "_ledger", ledger)
    monkeypatch.setattr(routes, "_load_trade_request", lambda *args: request)
    monkeypatch.setattr(routes, "_resolve", lambda payload: contracts)
    monkeypatch.setattr(
        routes, "_paper_open_basket_state",
        lambda *args: (trade_row, {role: 25 for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")}),
    )

    prices = {
        "LOW_CE": (10, 11), "HIGH_CE": (20, 21),
        "HIGH_PE": (30, 31), "LOW_PE": (40, 41),
    }

    @contextmanager
    def fake_depth(_contracts):
        records = {}
        for role, (bid, ask) in prices.items():
            records[role] = SimpleNamespace(
                bid=bid, ask=ask, bid_qty=25, ask_qty=25,
                payload={
                    "best_5_buy_data": [{"price": bid * 100, "quantity": 25}],
                    "best_5_sell_data": [{"price": ask * 100, "quantity": 25}],
                },
            )
        yield records

    monkeypatch.setattr(routes, "fresh_box_paper_depth", fake_depth)
    real_summary = ledger.get_trade_summary
    summary_calls = {"count": 0}

    def fail_once_after_commit(*args, **kwargs):
        summary_calls["count"] += 1
        if summary_calls["count"] == 1:
            raise RuntimeError("injected response loss after durable exit commit")
        return real_summary(*args, **kwargs)

    monkeypatch.setattr(ledger, "get_trade_summary", fail_once_after_commit)
    with pytest.raises(RuntimeError, match="response loss"):
        routes._execute_exit_basket_from_row(
            trade.id, account_key, {}, order_type="MARKET", fill_id="exit-response-lost",
        )

    committed = ledger.list_fills(trade.id, account_key=account_key)
    exit_fills = [fill for fill in committed if fill.fill_id.startswith("EXIT:BASKET:")]
    assert len(exit_fills) == 4
    assert {fill.role for fill in exit_fills} == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert ledger.get_trade_summary(trade.id, account_key=account_key)["status"] == "CLOSED"

    def forbidden(*args, **kwargs):
        pytest.fail("durable exit replay must not resolve instruments, read open legs/depth, or write again")

    monkeypatch.setattr(routes, "_resolve", forbidden)
    monkeypatch.setattr(routes, "_paper_open_basket_state", forbidden)
    monkeypatch.setattr(routes, "fresh_box_paper_depth", forbidden)
    monkeypatch.setattr(ledger, "record_exit_basket", forbidden)

    replay = routes._execute_exit_basket_from_row(
        trade.id, account_key, {}, order_type="MARKET", fill_id="exit-response-lost",
    )
    assert replay["idempotent_replay"] is True
    assert replay["status"] == "CLOSED"
    assert replay["exited_lots"] == 1
    assert replay["remaining_open_lots"] == 0
    assert len(replay["receipts"]) == 4
    assert {item["role"] for item in replay["receipts"]} == {"LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"}
    assert replay["broker_orders_enabled"] is False
    assert len(ledger.list_fills(trade.id, account_key=account_key)) == 8
