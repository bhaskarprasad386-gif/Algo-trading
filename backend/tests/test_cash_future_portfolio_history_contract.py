from datetime import date, datetime

from app.backtesting.cash_future_portfolio_runner import run_cash_future_portfolio_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def _point(ts, month, gap):
    return CashFutureHistoryPoint(
        timestamp=ts,
        symbol="AAA",
        contract_month=month,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=100,
        margin_required=4000.0,
        expiry_date=date(2026, 9, 30) if month == "SEP" else date(2026, 10, 30),
    )


def test_cash_future_strategy_history_contains_only_prior_points():
    start = datetime(2026, 9, 2, 10, 0)
    points = [_point(start, "SEP", 10), _point(start, "OCT", 12)]
    seen = []

    def strategy(current, history):
        seen.append((current.contract_month, tuple(p.contract_month for p in history)))
        return "HOLD"

    run_cash_future_portfolio_strategy(points, strategy, initial_capital=10000)

    assert seen == [("SEP", ()), ("OCT", ("SEP",))]
