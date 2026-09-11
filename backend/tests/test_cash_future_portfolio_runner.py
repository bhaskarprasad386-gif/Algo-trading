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
        points,
        lambda current, history: "BUY" if current.gap >= 10 else "SELL",
        initial_capital=10000,
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
        points,
        lambda current, history: "BUY" if current.gap >= 10 else "SELL",
        initial_capital=10000,
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
        points,
        lambda current, history: "BUY" if current.gap >= 10 else "SELL",
        initial_capital=10000,
    )
    assert len(result.trades) == 2
    assert {(t["symbol"], t["contract_month"]) for t in result.trades} == {("AAA", "SEP"), ("AAA", "OCT")}
    assert result.final_reserved_margin == 0.0
