from datetime import date, datetime, timedelta

import pytest

from app.backtesting.cash_future_portfolio_runner import run_cash_future_portfolio_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, month, gap):
    return CashFutureHistoryPoint(
        timestamp=ts, symbol="AAA", contract_month=month,
        cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
        lot_size=100, margin_required=4000.0,
        expiry_date=date(2026, 9, 30) if month == "SEP" else date(2026, 10, 30),
    )


def test_rollover_closes_old_contract_at_its_last_observation():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "SEP", 10),
        point(start + timedelta(minutes=1), "SEP", 6),
        point(start + timedelta(minutes=2), "OCT", 12),
    ]
    result = run_cash_future_portfolio_strategy(
        points, lambda current, history: "BUY" if current.gap >= 10 else "HOLD",
        initial_capital=10000,
    )
    rollover = [t for t in result.trades if t["exit_reason"] == "rollover"]
    assert len(rollover) == 1
    assert rollover[0]["contract_month"] == "SEP"
    assert rollover[0]["exit_time"] == (start + timedelta(minutes=1)).isoformat()
    assert result.open_position_count == 1
    assert result.final_reserved_margin == 4000.0


def test_bid_ask_force_exit_fully_closes_old_contract_and_keeps_new_position():
    start = datetime(2026, 9, 2, 10, 0)

    def quoted(ts, month, gap):
        return CashFutureHistoryPoint(
            timestamp=ts, symbol="AAA", contract_month=month,
            cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
            lot_size=100, margin_required=4000.0,
            expiry_date=date(2026, 9, 30) if month == "SEP" else date(2026, 10, 30),
            cash_ask=100.0, future_bid=100.0 + gap,
            cash_bid=100.0, future_ask=100.0 + gap,
            cash_ask_qty=100.0, future_bid_qty=100.0,
            cash_bid_qty=100.0, future_ask_qty=100.0,
        )

    result = run_cash_future_portfolio_strategy(
        [
            quoted(start, "SEP", 10),
            quoted(start + timedelta(minutes=1), "SEP", 8),
            quoted(start + timedelta(minutes=2), "OCT", 12),
        ],
        lambda current, history: "BUY" if current.gap >= 10 else "HOLD",
        initial_capital=10000,
        execution_model="bid_ask",
        rollover_policy="force_exit",
    )
    rollover = [trade for trade in result.trades if trade["exit_reason"] == "rollover"]
    assert len(rollover) == 1
    assert rollover[0]["contract_month"] == "SEP"
    assert rollover[0]["fill_status"] == "filled"
    assert rollover[0]["filled_quantity"] == 100.0
    assert result.open_position_count == 1
    assert result.final_reserved_margin == 4000.0


def test_rollover_reject_policy_blocks_series_mixing():
    start = datetime(2026, 9, 2, 10, 0)
    with pytest.raises(ValueError, match="rollover boundary"):
        run_cash_future_portfolio_strategy(
            [point(start, "SEP", 10), point(start + timedelta(minutes=1), "OCT", 12)],
            lambda current, history: "BUY" if current.gap >= 10 else "HOLD",
            initial_capital=10000, rollover_policy="reject",
        )


def test_rollover_rejects_partial_old_contract_close_instead_of_carrying_stale_position():
    start = datetime(2026, 9, 2, 10, 0)
    def quoted(ts, month, gap, *, bid_qty):
        return CashFutureHistoryPoint(
            timestamp=ts, symbol="AAA", contract_month=month,
            cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
            lot_size=100, margin_required=4000.0,
            expiry_date=date(2026, 9, 30) if month == "SEP" else date(2026, 10, 30),
            cash_ask=100.0, future_bid=100.0 + gap,
            cash_bid=100.0, future_ask=100.0 + gap,
            cash_ask_qty=100.0, future_bid_qty=100.0,
            cash_bid_qty=bid_qty, future_ask_qty=bid_qty,
        )

    points = [
        quoted(start, "SEP", 10, bid_qty=100.0),
        quoted(start + timedelta(minutes=1), "SEP", 8, bid_qty=30.0),
        quoted(start + timedelta(minutes=2), "OCT", 12, bid_qty=100.0),
    ]
    with pytest.raises(ValueError, match="fully close.*rollover"):
        run_cash_future_portfolio_strategy(
            points, lambda current, history: "BUY" if current.gap >= 10 else "HOLD",
            initial_capital=10000, execution_model="bid_ask", rollover_policy="force_exit",
        )

def test_expiry_day_close_precedes_new_contract_entry_after_rollover():
    start = datetime(2026, 9, 29, 10, 0)

    def quoted(ts, month, gap):
        expiry = date(2026, 9, 30) if month == "SEP" else date(2026, 10, 30)
        return CashFutureHistoryPoint(
            timestamp=ts, symbol="AAA", contract_month=month,
            cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
            lot_size=100, margin_required=4000.0, expiry_date=expiry,
        )

    result = run_cash_future_portfolio_strategy(
        [
            quoted(start, "SEP", 10),
            quoted(datetime(2026, 9, 30, 10, 0), "SEP", 8),
            quoted(datetime(2026, 10, 1, 10, 0), "OCT", 12),
        ],
        lambda current, history: "BUY" if current.gap >= 10 else "HOLD",
        initial_capital=10000,
        rollover_policy="force_exit",
    )

    expiry = [trade for trade in result.trades if trade["exit_reason"] == "expiry"]
    rollover = [trade for trade in result.trades if trade["exit_reason"] == "rollover"]
    assert len(expiry) == 1
    assert len(rollover) == 0
    assert expiry[0]["contract_month"] == "SEP"
    assert result.open_position_count == 1
    assert result.trades[-1]["contract_month"] == "SEP"
