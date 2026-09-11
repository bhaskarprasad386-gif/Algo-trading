from datetime import date, datetime, timedelta

from app.backtesting.cash_future_portfolio_runner import run_cash_future_portfolio_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, symbol, gap, month="SEP", margin=6000.0, expiry=date(2026, 9, 30), **quotes):
    return CashFutureHistoryPoint(
        timestamp=ts, symbol=symbol, contract_month=month,
        cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
        lot_size=100, margin_required=margin, expiry_date=expiry, **quotes
    )


def test_portfolio_allows_simultaneous_symbols_and_reserves_margin_independently():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "AAA", 10, margin=6000),
        point(start + timedelta(minutes=1), "BBB", 10, margin=6000),
        point(start + timedelta(minutes=2), "AAA", 4, margin=6000),
        point(start + timedelta(minutes=3), "BBB", 4, margin=6000),
    ]
    result = run_cash_future_portfolio_strategy(
        points, lambda current, history: "BUY" if current.gap >= 10 else "SELL", initial_capital=10000,
    )
    assert len(result.trades) == 2
    assert {trade["symbol"] for trade in result.trades} == {"AAA", "BBB"}
    assert result.final_reserved_margin == 0.0
    assert result.final_available_capital == 11200.0
    assert result.blocked_entry_count == 0


def test_portfolio_blocks_only_the_position_that_exceeds_available_capital():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "AAA", 10, margin=7000),
        point(start + timedelta(minutes=1), "BBB", 10, margin=7000),
        point(start + timedelta(minutes=2), "AAA", 4, margin=7000),
    ]
    result = run_cash_future_portfolio_strategy(
        points, lambda current, history: "BUY" if current.gap >= 10 else "SELL", initial_capital=10000,
    )
    assert len(result.trades) == 1
    assert result.trades[0]["symbol"] == "AAA"
    blocked = [signal for signal in result.signals if signal.get("execution_status") == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["symbol"] == "BBB"
    assert result.blocked_entry_count == 1
    assert result.final_reserved_margin == 0.0
    assert result.final_available_capital == 10600.0


def test_portfolio_keeps_contract_series_isolated():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "AAA", 10, month="SEP", margin=4000),
        point(start + timedelta(minutes=1), "AAA", 10, month="OCT", margin=4000),
        point(start + timedelta(minutes=2), "AAA", 4, month="SEP", margin=4000),
        point(start + timedelta(minutes=3), "AAA", 4, month="OCT", margin=4000),
    ]
    result = run_cash_future_portfolio_strategy(
        points, lambda current, history: "BUY" if current.gap >= 10 else "SELL", initial_capital=10000,
    )
    assert len(result.trades) == 2
    assert {(t["symbol"], t["contract_month"]) for t in result.trades} == {("AAA", "SEP"), ("AAA", "OCT")}
    assert result.final_reserved_margin == 0.0


def test_portfolio_equity_includes_unrealized_mtm_for_all_open_positions():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "AAA", 10, margin=4000),
        point(start + timedelta(minutes=1), "BBB", 10, margin=4000),
        point(start + timedelta(minutes=2), "AAA", 7, margin=4000),
        point(start + timedelta(minutes=3), "BBB", 6, margin=4000),
    ]
    result = run_cash_future_portfolio_strategy(
        points, lambda current, history: "BUY" if current.gap >= 10 else "HOLD", initial_capital=10000,
    )
    assert result.trades == ()
    assert result.equity_curve[-1]["unrealized_pnl"] == 700.0
    assert result.equity_curve[-1]["equity"] == 10700.0
    assert result.equity_curve[-1]["available_capital"] == 2000.0
    assert result.equity_curve[-1]["reserved_margin"] == 8000.0
    assert result.open_position_count == 2
    assert result.final_capital == 10700.0


def test_portfolio_forces_historical_exit_when_marked_equity_breaches_margin():
    start = datetime(2026, 9, 2, 10, 0)
    points = [
        point(start, "AAA", 10, margin=7000),
        point(start + timedelta(minutes=1), "AAA", -30, margin=7000),
    ]
    result = run_cash_future_portfolio_strategy(
        points,
        lambda current, history: "BUY" if current.gap >= 10 else "NONE",
        initial_capital=10000,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["exit_reason"] == "margin_breach"
    assert trade["gross_profit"] == -4000.0
    assert result.open_position_count == 0
    assert result.final_reserved_margin == 0.0
    assert result.final_capital == 6000.0
    assert result.equity_curve[-1]["unrealized_pnl"] == 0.0
    assert result.equity_curve[-1]["equity"] == 6000.0


def test_bid_ask_partial_entry_uses_only_genuine_depth_quantities():
    start = datetime(2026, 9, 2, 10, 0)
    entry = point(
        start, "AAA", 10, margin=10000,
        cash_ask=101.0, future_bid=109.0,
        cash_ask_qty=40.0, future_bid_qty=25.0,
    )
    hold = point(
        start + timedelta(minutes=1), "AAA", 8, margin=10000,
        cash_bid=108.0, future_ask=108.0,
        cash_ask=108.0, future_bid=108.0,
    )
    result = run_cash_future_portfolio_strategy(
        [entry, hold], lambda current, history: "BUY" if current is entry else "HOLD",
        initial_capital=100000, execution_model="bid_ask",
    )
    signal = result.signals[0]
    assert signal["execution_status"] == "partial_fill"
    assert signal["filled_quantity"] == 25.0
    assert signal["unfilled_quantity"] == 75.0
    assert signal["liquidity_source"] == "historical_depth"
    assert result.open_position_count == 1
    assert result.final_reserved_margin == 2500.0


def test_bid_ask_partial_exit_releases_proportional_margin_and_pnl_uses_filled_quantity():
    start = datetime(2026, 9, 2, 10, 0)
    entry = point(
        start, "AAA", 10, margin=10000,
        cash_ask=101.0, future_bid=109.0,
        cash_ask_qty=100.0, future_bid_qty=100.0,
    )
    exit_point = point(
        start + timedelta(minutes=1), "AAA", 8, margin=10000,
        cash_bid=108.0, future_ask=108.0,
        cash_bid_qty=30.0, future_ask_qty=30.0,
    )
    result = run_cash_future_portfolio_strategy(
        [entry, exit_point], lambda current, history: "BUY" if current is entry else "SELL",
        initial_capital=100000, execution_model="bid_ask",
    )
    trade = result.trades[0]
    assert trade["filled_quantity"] == 30.0
    assert trade["unfilled_quantity"] == 70.0
    assert trade["fill_status"] == "partial_fill"
    assert trade["gross_profit"] == 180.0
    assert result.open_position_count == 1
    assert result.final_reserved_margin == 7000.0


def test_missing_depth_does_not_invent_liquidity_and_keeps_strict_bid_ask_fill():
    start = datetime(2026, 9, 2, 10, 0)
    entry = point(start, "AAA", 10, cash_ask=101.0, future_bid=109.0)
    hold = point(start + timedelta(minutes=1), "AAA", 8, cash_bid=108.0, future_ask=108.0)
    result = run_cash_future_portfolio_strategy(
        [entry, hold], lambda current, history: "BUY" if current is entry else "HOLD",
        initial_capital=100000, execution_model="bid_ask",
    )
    assert result.signals[0]["execution_status"] == "executed"
    assert result.signals[0]["filled_quantity"] == 100.0
    assert result.signals[0]["liquidity_source"] == "strict_bid_ask_no_depth"


def test_zero_historical_depth_produces_no_fill():
    start = datetime(2026, 9, 2, 10, 0)
    entry = point(
        start, "AAA", 10, cash_ask=101.0, future_bid=109.0,
        cash_ask_qty=0.0, future_bid_qty=0.0,
    )
    result = run_cash_future_portfolio_strategy(
        [entry], lambda current, history: "BUY", initial_capital=100000, execution_model="bid_ask",
    )
    assert result.signals[0]["execution_status"] == "no_fill"
    assert result.signals[0]["filled_quantity"] == 0.0
    assert result.open_position_count == 0
