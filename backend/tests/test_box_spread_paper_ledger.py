from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.scanner.box_spread_paper_ledger import (
    BoxSpreadPaperAccount,
    BoxSpreadPaperFeeSchedule,
    BoxSpreadPaperLedger,
    BoxSpreadPaperLedgerEntry,
    BoxSpreadPaperTradeRecord,
)


@pytest.fixture
def paper_ledger():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = BoxSpreadPaperLedger(factory)
    yield ledger
    engine.dispose()


def _trade(ledger, *, account_key="default", idempotency_key="paper-1", lots=1):
    return ledger.create_trade(
        account_key=account_key,
        idempotency_key=idempotency_key,
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry="2026-10-29",
        low_strike=25000,
        high_strike=25100,
        direction="LONG",
        lots=lots,
        lot_size=25,
    )


def test_persistent_trade_creation_is_idempotent_and_conflicts_rejected(paper_ledger):
    first = _trade(paper_ledger)
    second = _trade(paper_ledger)
    assert first.id == second.id
    assert second.quantity == 25
    with pytest.raises(ValueError, match="different trade"):
        _trade(paper_ledger, lots=2)


def test_fill_ledger_applies_cash_flow_charges_and_virtual_balance(paper_ledger):
    trade = _trade(paper_ledger)
    before = paper_ledger.ensure_account()
    receipt = paper_ledger.record_fill(
        trade_id=trade.id, fill_id="fill-1", role="LOW_CE", action="BUY",
        quantity=25, fill_price="100.00",
    )
    assert receipt.notional == Decimal("2500.00")
    assert receipt.charges > Decimal("0")
    assert receipt.cash_delta == -receipt.notional - receipt.charges
    assert receipt.virtual_balance == before + receipt.cash_delta
    summary = paper_ledger.get_account_summary()
    assert summary["fill_count"] == 1
    assert summary["total_charges"] == receipt.charges
    assert summary["virtual_balance"] == receipt.virtual_balance


def test_duplicate_fill_does_not_double_charge_or_change_balance(paper_ledger):
    trade = _trade(paper_ledger)
    kwargs = dict(
        trade_id=trade.id, fill_id="stable-fill", role="HIGH_CE", action="SELL",
        quantity=25, fill_price="50.00",
    )
    first = paper_ledger.record_fill(**kwargs)
    second = paper_ledger.record_fill(**kwargs)
    assert first.charges == second.charges
    assert first.cash_delta == second.cash_delta
    assert paper_ledger.get_account_summary()["fill_count"] == 1
    with pytest.raises(ValueError, match="different fill data"):
        paper_ledger.record_fill(**{**kwargs, "quantity": 24})


def test_account_balance_and_ledger_are_isolated_by_account_key(paper_ledger):
    trade = _trade(paper_ledger, account_key="alpha")
    paper_ledger.record_fill(
        trade_id=trade.id, account_key="alpha", fill_id="alpha-fill",
        role="LOW_CE", action="BUY", quantity=25, fill_price=100,
    )
    alpha = paper_ledger.get_account_summary("alpha")
    beta = paper_ledger.get_account_summary("beta")
    assert alpha["virtual_balance"] < alpha["starting_balance"]
    assert beta["virtual_balance"] == beta["starting_balance"]
    assert beta["fill_count"] == 0


def test_prevents_overspending_and_invalid_fill_values():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = BoxSpreadPaperLedger(
        factory,
        starting_balance=Decimal("100.00"),
        fee_schedule=BoxSpreadPaperFeeSchedule(
            version="test-zero-fees",
            brokerage_per_fill=Decimal("0"),
            exchange_transaction_rate=Decimal("0"),
            sebi_rate=Decimal("0"),
            gst_rate=Decimal("0"),
            sell_stt_rate=Decimal("0"),
            buy_stamp_rate=Decimal("0"),
        ),
    )
    trade = _trade(ledger)
    with pytest.raises(ValueError, match="insufficient virtual balance"):
        ledger.record_fill(
            trade_id=trade.id, fill_id="too-large", role="LOW_CE",
            action="BUY", quantity=25, fill_price=10,
        )
    with pytest.raises(ValueError, match="positive integer"):
        ledger.record_fill(
            trade_id=trade.id, fill_id="bad-qty", role="LOW_CE",
            action="BUY", quantity=0, fill_price=10,
        )
    with pytest.raises(ValueError, match="positive"):
        ledger.record_fill(
            trade_id=trade.id, fill_id="bad-price", role="LOW_CE",
            action="BUY", quantity=1, fill_price=0,
        )
    engine.dispose()


def test_fee_schedule_is_versioned_and_sell_stt_is_applied():
    schedule = BoxSpreadPaperFeeSchedule()
    buy = schedule.calculate(action="BUY", notional=Decimal("10000"))
    sell = schedule.calculate(action="SELL", notional=Decimal("10000"))
    assert schedule.version
    assert sell > buy


def test_fill_records_survive_ledger_instance_recreation(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="restart-fill", role="LOW_CE",
        action="BUY", quantity=25, fill_price="100.00",
    )
    restored = BoxSpreadPaperLedger(paper_ledger._session_factory)
    summary = restored.get_account_summary()
    fills = restored.list_fills(trade.id)
    assert summary["fill_count"] == 1
    assert fills[0].fill_id == "restart-fill"
    assert fills[0].quantity == 25


def test_persistent_ledger_prevents_cumulative_leg_overfill_and_wrong_action(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="part-a", role="LOW_CE", action="BUY",
        quantity=20, fill_price=100,
    )
    with pytest.raises(ValueError, match="remaining leg quantity"):
        paper_ledger.record_fill(
            trade_id=trade.id, fill_id="part-b", role="LOW_CE", action="BUY",
            quantity=6, fill_price=100,
        )
    with pytest.raises(ValueError, match="does not match"):
        paper_ledger.record_fill(
            trade_id=trade.id, fill_id="wrong-action", role="HIGH_CE", action="BUY",
            quantity=1, fill_price=50,
        )


def test_trade_status_changes_only_when_all_four_legs_fully_fill(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="leg-1", role="LOW_CE", action="BUY",
        quantity=25, fill_price=100,
    )
    with paper_ledger._session_factory() as session:
        first_status = session.get(BoxSpreadPaperTradeRecord, trade.id).status
    assert first_status == "PARTIALLY_FILLED"
    actions = {"HIGH_CE": "SELL", "HIGH_PE": "BUY", "LOW_PE": "SELL"}
    for role, action in actions.items():
        paper_ledger.record_fill(
            trade_id=trade.id, fill_id=f"{role}-full", role=role, action=action,
            quantity=25, fill_price=50,
        )
    with paper_ledger._session_factory() as session:
        final_status = session.get(BoxSpreadPaperTradeRecord, trade.id).status
    assert final_status == "FILLED"


def test_trade_summary_reports_charges_cash_flow_and_mark_to_market_pnl(paper_ledger):
    trade = _trade(paper_ledger)
    fills = [
        ("LOW_CE", "BUY", "100.00"),
        ("HIGH_CE", "SELL", "50.00"),
        ("HIGH_PE", "BUY", "55.00"),
        ("LOW_PE", "SELL", "95.00"),
    ]
    for role, action, price in fills:
        paper_ledger.record_fill(
            trade_id=trade.id, fill_id=f"{role}-entry", role=role,
            action=action, quantity=25, fill_price=price,
        )
    summary = paper_ledger.get_trade_summary(
        trade.id,
        mark_prices={"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
    )
    assert summary["status"] == "FILLED"
    assert summary["fill_count"] == 4
    assert summary["total_charges"] > Decimal("0")
    assert summary["unrealized_pnl"] == -summary["total_charges"]
    assert summary["realized_pnl"] is None


def test_trade_summary_rejects_missing_or_invalid_mark_prices(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="marked", role="LOW_CE", action="BUY",
        quantity=5, fill_price=100,
    )
    with pytest.raises(ValueError, match="include every filled leg"):
        paper_ledger.get_trade_summary(trade.id, mark_prices={})
    with pytest.raises(ValueError, match="mark price must be positive"):
        paper_ledger.get_trade_summary(trade.id, mark_prices={"LOW_CE": 0})
