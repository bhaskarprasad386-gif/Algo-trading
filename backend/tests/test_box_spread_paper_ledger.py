from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
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
    BoxSpreadPaperEntryClaim,
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
        BoxSpreadPaperEntryClaim.__table__,
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


@pytest.mark.parametrize("expiry", ["29-Oct-2026", "20261029", "2026-W44-4"])
def test_paper_trade_creation_rejects_non_iso_expiry(paper_ledger, expiry):
    with pytest.raises(ValueError, match="expiry must be an ISO date"):
        paper_ledger.create_trade(
            idempotency_key="bad-expiry", underlying="NIFTY",
            instrument_class="INDEX", expiry=expiry,
            low_strike=25000, high_strike=25100, direction="LONG",
            lots=1, lot_size=25,
        )
    assert paper_ledger.list_open_trades() == []


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


def test_concurrent_entry_fills_cannot_overfill_same_leg(tmp_path):
    # Use a file-backed SQLite database so each worker has an independent
    # connection. An in-memory StaticPool would not model concurrent requests.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-ledger-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
        BoxSpreadPaperEntryClaim.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = BoxSpreadPaperLedger(factory)
    trade = _trade(ledger, lots=1)
    barrier = Barrier(2)

    def submit(fill_id):
        barrier.wait(timeout=5)
        try:
            ledger.record_fill(
                trade_id=trade.id, fill_id=fill_id, role="LOW_CE", action="BUY",
                quantity=20, fill_price=100,
            )
            return "accepted"
        except ValueError as exc:
            if "fill quantity exceeds" in str(exc):
                return "rejected"
            raise

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, ("concurrent-a", "concurrent-b")))
        assert sorted(outcomes) == ["accepted", "rejected"]
        fills = ledger.list_fills(trade.id)
        assert sum(fill.quantity for fill in fills if fill.role == "LOW_CE") == 20
        assert ledger.get_account_summary()["fill_count"] == 1
    finally:
        engine.dispose()


def test_entry_fill_rejects_reserved_exit_and_settlement_prefixes(paper_ledger):
    trade = _trade(paper_ledger)
    for fill_id in ("EXIT:manual", "SETTLE:manual", "exit:lowercase"):
        with pytest.raises(ValueError, match="entry fill_id cannot use reserved"):
            paper_ledger.record_fill(
                trade_id=trade.id, fill_id=fill_id, role="LOW_CE", action="BUY",
                quantity=1, fill_price=100,
            )
    assert paper_ledger.get_account_summary()["fill_count"] == 0
    recovered = paper_ledger.list_open_trades()
    assert recovered[0]["legs"][0]["entry_filled_quantity"] == 0
    assert recovered[0]["legs"][0]["exited_quantity"] == 0


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
        BoxSpreadPaperEntryClaim.__table__,
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
    assert summary["realized_pnl"] == Decimal("0.00")


def test_trade_summary_rejects_missing_or_invalid_mark_prices(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="marked", role="LOW_CE", action="BUY",
        quantity=5, fill_price=100,
    )
    with pytest.raises(ValueError, match="include every open leg role"):
        paper_ledger.get_trade_summary(trade.id, mark_prices={})
    with pytest.raises(ValueError, match="mark price must be positive"):
        paper_ledger.get_trade_summary(trade.id, mark_prices={"LOW_CE": 0})


def _fill_all_legs(ledger, trade):
    for role, action, price in [
        ("LOW_CE", "BUY", 100),
        ("HIGH_CE", "SELL", 50),
        ("HIGH_PE", "BUY", 55),
        ("LOW_PE", "SELL", 95),
    ]:
        ledger.record_fill(
            trade_id=trade.id, fill_id=f"entry-{role}", role=role,
            action=action, quantity=25, fill_price=price,
        )


def test_exit_fills_close_positions_and_report_realized_pnl(paper_ledger):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    for role, exit_price in [
        ("LOW_CE", 101), ("HIGH_CE", 49),
        ("HIGH_PE", 56), ("LOW_PE", 94),
    ]:
        paper_ledger.record_exit_fill(
            trade_id=trade.id, fill_id=f"close-{role}", role=role,
            quantity=25, fill_price=exit_price,
        )
    summary = paper_ledger.get_trade_summary(trade.id)
    assert summary["status"] == "CLOSED"
    assert summary["realized_pnl"] is not None
    assert summary["unrealized_pnl"] == Decimal("0.00")
    assert paper_ledger.list_open_trades() == []


def test_exit_fill_idempotency_and_over_exit_protection(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="entry-one", role="LOW_CE", action="BUY",
        quantity=10, fill_price=100,
    )
    kwargs = dict(
        trade_id=trade.id, fill_id="exit-one", role="LOW_CE",
        quantity=5, fill_price=101,
    )
    first = paper_ledger.record_exit_fill(**kwargs)
    second = paper_ledger.record_exit_fill(**kwargs)
    assert first.cash_delta == second.cash_delta
    with pytest.raises(ValueError, match="exceeds open leg quantity"):
        paper_ledger.record_exit_fill(
            trade_id=trade.id, fill_id="exit-too-much", role="LOW_CE",
            quantity=6, fill_price=101,
        )


def test_expiry_settlement_rejects_datetime_timestamp(paper_ledger):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)

    with pytest.raises(ValueError, match="settlement_date must be an ISO date"):
        paper_ledger.settle_expiry(
            trade_id=trade.id,
            settlement_prices={"LOW_CE": 0, "HIGH_CE": 0, "HIGH_PE": 0, "LOW_PE": 0},
            settlement_date=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
        )

    assert paper_ledger.get_trade_summary(trade.id)["status"] == "FILLED"
    assert all(not fill.fill_id.startswith("SETTLE:") for fill in paper_ledger.list_fills(trade.id))


def test_expiry_settlement_requires_expiry_and_explicit_prices(paper_ledger):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    prices = {"LOW_CE": 0, "HIGH_CE": 0, "HIGH_PE": 0, "LOW_PE": 0}
    with pytest.raises(ValueError, match="before expiry"):
        paper_ledger.settle_expiry(
            trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-28",
        )
    with pytest.raises(ValueError, match="include every open leg role"):
        paper_ledger.settle_expiry(
            trade_id=trade.id, settlement_prices={}, settlement_date="2026-10-29",
        )
    # A worthless option can have zero official intrinsic settlement; it is not a stale-LTP fallback.
    settled = paper_ledger.settle_expiry(
        trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
    )
    assert settled["status"] == "CLOSED"


def test_expiry_settlement_validates_all_prices_before_any_fill(paper_ledger):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    balance_before = paper_ledger.get_account_summary()["virtual_balance"]
    fill_count_before = paper_ledger.get_account_summary()["fill_count"]

    # The invalid price is deliberately on the final sorted leg: earlier legs
    # must not be committed before the malformed price is discovered.
    prices = {"LOW_CE": 10, "HIGH_CE": 0.5, "HIGH_PE": 2, "LOW_PE": "not-a-price"}
    with pytest.raises(ValueError, match="settlement price for LOW_PE"):
        paper_ledger.settle_expiry(
            trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
        )

    assert paper_ledger.get_account_summary()["virtual_balance"] == balance_before
    assert paper_ledger.get_account_summary()["fill_count"] == fill_count_before
    assert paper_ledger.get_trade_summary(trade.id)["status"] == "FILLED"
    assert all(not fill.fill_id.startswith("SETTLE:") for fill in paper_ledger.list_fills(trade.id))


@pytest.mark.parametrize("settlement_date", ["20261029", "2026-W44-4"])
def test_expiry_settlement_rejects_noncanonical_date_strings(paper_ledger, settlement_date):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    balance_before = paper_ledger.get_account_summary()["virtual_balance"]

    with pytest.raises(ValueError, match="settlement_date must be an ISO date"):
        paper_ledger.settle_expiry(
            trade_id=trade.id,
            settlement_prices={"LOW_CE": 10, "HIGH_CE": 2, "HIGH_PE": 2, "LOW_PE": 10},
            settlement_date=settlement_date,
        )

    assert paper_ledger.get_account_summary()["virtual_balance"] == balance_before
    assert paper_ledger.get_trade_summary(trade.id)["status"] == "FILLED"
    assert all(not fill.fill_id.startswith("SETTLE:") for fill in paper_ledger.list_fills(trade.id))


def test_expiry_settlement_rejects_trade_without_open_positions(paper_ledger):
    trade = _trade(paper_ledger)
    balance_before = paper_ledger.get_account_summary()["virtual_balance"]

    with pytest.raises(ValueError, match="no open positions to settle"):
        paper_ledger.settle_expiry(
            trade_id=trade.id,
            settlement_prices={"LOW_CE": 10, "HIGH_CE": 2, "HIGH_PE": 2, "LOW_PE": 10},
            settlement_date="2026-10-29",
        )

    assert paper_ledger.get_account_summary()["virtual_balance"] == balance_before
    assert paper_ledger.get_trade_summary(trade.id)["status"] == "CREATED"
    assert paper_ledger.list_fills(trade.id) == []


def test_expiry_settlement_is_idempotent_and_restart_recoverable(paper_ledger):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    prices = {"LOW_CE": 10, "HIGH_CE": 0.5, "HIGH_PE": 2, "LOW_PE": 8}
    # Use positive settlement values and explicit expiry date.
    first = paper_ledger.settle_expiry(
        trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
    )
    balance_after = paper_ledger.get_account_summary()["virtual_balance"]
    second = paper_ledger.settle_expiry(
        trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
    )
    assert first["status"] == "CLOSED"
    assert second["status"] == "CLOSED"
    assert paper_ledger.get_account_summary()["virtual_balance"] == balance_after
    restored = BoxSpreadPaperLedger(paper_ledger._session_factory)
    assert restored.get_trade_summary(trade.id)["realized_pnl"] is not None
    assert restored.list_open_trades() == []


def test_expiry_settlement_retries_after_partial_commit_without_duplicate_fills(paper_ledger, monkeypatch):
    trade = _trade(paper_ledger)
    _fill_all_legs(paper_ledger, trade)
    prices = {"LOW_CE": 10, "HIGH_CE": 0.5, "HIGH_PE": 2, "LOW_PE": 8}
    initial_fill_count = paper_ledger.get_account_summary()["fill_count"]
    original_record_exit_fill = paper_ledger.record_exit_fill
    calls = 0

    def fail_after_one_committed_settlement_leg(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated process interruption")
        return original_record_exit_fill(**kwargs)

    monkeypatch.setattr(paper_ledger, "record_exit_fill", fail_after_one_committed_settlement_leg)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        paper_ledger.settle_expiry(
            trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
        )

    # The first leg committed before interruption; recovery must settle only
    # the still-open legs and preserve the existing durable fill.
    partial = paper_ledger.list_fills(trade.id)
    committed_settlements = [fill for fill in partial if fill.fill_id.startswith("SETTLE:")]
    assert len(committed_settlements) == 1

    # A recovery must reuse the committed leg's price and settlement date.
    # Settlement legs are processed in sorted role order, so assert against
    # the actual durable leg rather than assuming LOW_CE committed first.
    recovered = BoxSpreadPaperLedger(paper_ledger._session_factory)
    changed_prices = dict(prices)
    committed_role = committed_settlements[0].role
    changed_prices[committed_role] = Decimal(committed_settlements[0].fill_price) + Decimal("1")
    with pytest.raises(ValueError, match="settlement price mismatch"):
        recovered.settle_expiry(
            trade_id=trade.id, settlement_prices=changed_prices,
            settlement_date="2026-10-29",
        )
    with pytest.raises(ValueError, match="different date"):
        recovered.settle_expiry(
            trade_id=trade.id, settlement_prices=prices,
            settlement_date="2026-10-30",
        )

    monkeypatch.setattr(paper_ledger, "record_exit_fill", original_record_exit_fill)
    result = recovered.settle_expiry(
        trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
    )

    assert result["status"] == "CLOSED"
    assert result["settled_legs"] == 3
    fills = recovered.list_fills(trade.id)
    assert sum(fill.fill_id.startswith("SETTLE:") for fill in fills) == 4
    assert recovered.get_account_summary()["fill_count"] == initial_fill_count + 4
    assert recovered.list_open_trades() == []

    # A lost response after the final commit is also safe to retry.
    repeated = recovered.settle_expiry(
        trade_id=trade.id, settlement_prices=prices, settlement_date="2026-10-29",
    )
    assert repeated["status"] == "CLOSED"
    assert repeated["settled_legs"] == 4
    assert recovered.get_account_summary()["fill_count"] == initial_fill_count + 4


def test_open_trade_recovery_isolated_by_account(paper_ledger):
    alpha_trade = _trade(
        paper_ledger, account_key="alpha-recovery", idempotency_key="alpha-open",
    )
    beta_trade = _trade(
        paper_ledger, account_key="beta-recovery", idempotency_key="beta-open",
    )
    paper_ledger.record_fill(
        trade_id=alpha_trade.id, account_key="alpha-recovery",
        fill_id="alpha-recovery-fill", role="LOW_CE", action="BUY",
        quantity=3, fill_price=100,
    )
    paper_ledger.record_fill(
        trade_id=beta_trade.id, account_key="beta-recovery",
        fill_id="beta-recovery-fill", role="LOW_CE", action="BUY",
        quantity=4, fill_price=100,
    )

    alpha_rows = paper_ledger.list_open_trades(account_key="alpha-recovery")
    beta_rows = paper_ledger.list_open_trades(account_key="beta-recovery")
    unknown_rows = paper_ledger.list_open_trades(account_key="no-such-account")

    assert [row["trade_id"] for row in alpha_rows] == [alpha_trade.id]
    assert alpha_rows[0]["legs"][0]["entry_filled_quantity"] == 3
    assert [row["trade_id"] for row in beta_rows] == [beta_trade.id]
    assert beta_rows[0]["legs"][0]["entry_filled_quantity"] == 4
    assert unknown_rows == []


def test_open_trade_recovery_preserves_per_leg_entry_exit_progress_and_fill_details(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="recovery-partial", role="LOW_CE",
        action="BUY", quantity=7, fill_price=100,
    )
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="recovery-short-entry", role="HIGH_CE",
        action="SELL", quantity=5, fill_price=50,
    )
    paper_ledger.record_exit_fill(
        trade_id=trade.id, fill_id="recovery-exit", role="LOW_CE",
        quantity=2, fill_price=101,
    )
    recovered = BoxSpreadPaperLedger(paper_ledger._session_factory).list_open_trades()
    assert len(recovered) == 1
    row = recovered[0]
    assert row["trade_id"] == trade.id
    assert row["status"] == "EXITING"
    legs = {leg["role"]: leg for leg in row["legs"]}
    assert legs["LOW_CE"]["entry_filled_quantity"] == 7
    assert legs["LOW_CE"]["entry_remaining_quantity"] == 18
    assert legs["LOW_CE"]["exited_quantity"] == 2
    assert legs["LOW_CE"]["open_position_quantity"] == 5
    assert legs["LOW_CE"]["signed_open_position_quantity"] == 5
    assert legs["LOW_CE"]["open_position_action"] == "BUY"
    assert legs["HIGH_CE"]["open_position_quantity"] == 5
    assert legs["HIGH_CE"]["signed_open_position_quantity"] == -5
    assert legs["HIGH_CE"]["open_position_action"] == "SELL"
    assert {fill["fill_id"] for fill in legs["LOW_CE"]["fills"]} == {
        "recovery-partial", "EXIT:recovery-exit",
    }
    # The HIGH_CE short entry is a real opening fill, not an exit fill.
    assert legs["HIGH_CE"]["entry_filled_quantity"] == 5
    assert legs["HIGH_CE"]["entry_remaining_quantity"] == 20
    assert legs["HIGH_CE"]["exited_quantity"] == 0
    assert len(legs["HIGH_CE"]["fills"]) == 1
    assert legs["HIGH_CE"]["fills"][0]["fill_id"] == "recovery-short-entry"
    assert legs["HIGH_CE"]["fills"][0]["action"] == "SELL"
    assert legs["HIGH_CE"]["fills"][0]["quantity"] == 5
    assert legs["HIGH_CE"]["fills"][0]["fill_price"] == "50.000000"


def test_exit_is_never_blocked_by_low_virtual_balance():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[
        BoxSpreadPaperAccount.__table__,
        BoxSpreadPaperTradeRecord.__table__,
        BoxSpreadPaperLedgerEntry.__table__,
        BoxSpreadPaperEntryClaim.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = BoxSpreadPaperLedger(factory, starting_balance=100)
    trade = _trade(ledger)
    ledger.record_fill(
        trade_id=trade.id, fill_id="short-entry", role="HIGH_CE", action="SELL",
        quantity=25, fill_price=1,
    )
    receipt = ledger.record_exit_fill(
        trade_id=trade.id, fill_id="expensive-close", role="HIGH_CE",
        quantity=25, fill_price=10000,
    )
    assert receipt.virtual_balance < Decimal("0")
    engine.dispose()


def test_partial_exit_separates_realized_and_open_leg_pnl(paper_ledger):
    trade = _trade(paper_ledger)
    paper_ledger.record_fill(
        trade_id=trade.id, fill_id="partial-entry", role="LOW_CE",
        action="BUY", quantity=10, fill_price=100,
    )
    paper_ledger.record_exit_fill(
        trade_id=trade.id, fill_id="partial-exit", role="LOW_CE",
        quantity=4, fill_price=110,
    )
    summary = paper_ledger.get_trade_summary(
        trade.id, mark_prices={"LOW_CE": 105},
    )
    assert summary["realized_pnl"] > Decimal("0")
    assert summary["unrealized_pnl"] > Decimal("0")
    assert summary["status"] == "EXITING"


def test_entry_claim_is_durable_and_one_shot_across_ledger_recreation(paper_ledger):
    trade = _trade(paper_ledger, account_key="claim-recovery", idempotency_key="claim-recovery")
    first = paper_ledger.claim_entry_attempt(
        trade_id=trade.id, account_key="claim-recovery",
    )
    assert first["created"] is True
    assert first["status"] == "CLAIMED"

    restored = BoxSpreadPaperLedger(paper_ledger._session_factory)
    second = restored.claim_entry_attempt(
        trade_id=trade.id, account_key="claim-recovery",
    )
    assert second["created"] is False
    assert second["status"] == "CLAIMED"
    restored.complete_entry_attempt(trade_id=trade.id, account_key="claim-recovery")
    completed = restored.get_entry_claim(trade_id=trade.id, account_key="claim-recovery")
    assert completed["status"] == "COMPLETED"
    assert completed["completed_at"] is not None
    assert restored.get_entry_claim(
        trade_id=trade.id, account_key="claim-recovery",
    )["claimed_at"] == first["claimed_at"]
