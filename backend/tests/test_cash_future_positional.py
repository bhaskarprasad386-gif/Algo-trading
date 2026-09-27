"""Regression coverage for Cash-Future positional holding mode."""
from datetime import date, datetime

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _point(ts, gap):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="ABC",
        contract_month="SEP",
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=100,
        margin_required=10_000.0,
        expiry_date=date(2026, 9, 30),
    )


def test_cash_future_positional_holds_overnight_without_forced_eod_exit():
    day1 = datetime(2026, 9, 28, 15, 29)
    day2 = datetime(2026, 9, 29, 9, 16)

    def strategy(current, history):
        if len(history) == 1:
            return "BUY"
        return "HOLD"

    result = run_cash_future_strategy(
        (_point(day1, 10.0), _point(day2, 8.0)),
        strategy,
        strategy_id="cash-future-positional",
        config=CashFutureStrategyConfig(
            initial_capital=100_000.0,
            start_date=day1.date(),
            end_date=day2.date(),
            holding_mode="POSITIONAL",
        ),
    )

    assert len(result.trades) == 0
    assert result.final_reserved_margin == 10_000.0
    assert result.final_available_capital == 90_000.0
    assert result.equity_curve[-1]["unrealized_pnl"] == 200.0


def test_cash_future_positional_rejects_unknown_holding_mode():
    try:
        CashFutureStrategyConfig(holding_mode="INTRADAY")
    except ValueError as exc:
        assert "holding_mode" in str(exc)
    else:
        raise AssertionError("non-positional holding mode must be rejected")
