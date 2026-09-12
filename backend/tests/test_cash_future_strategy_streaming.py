from datetime import date, datetime, timedelta

from app.backtesting.cash_future_strategy_runner import CashFutureStrategyConfig, run_cash_future_strategy
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(ts, gap, month="SEP"):
    return CashFutureHistoryPoint(
        timestamp=ts, symbol="ABC", contract_month=month,
        cash_price=100.0, future_price=100.0 + gap, gap=gap, gap_pct=gap,
        lot_size=100, margin_required=10000.0, expiry_date=date(2026, 9, 30),
    )


def test_strategy_accepts_generator_without_eager_materialization():
    now = datetime(2026, 9, 2, 10, 0)
    consumed = []

    def source():
        for i in range(3):
            consumed.append(i)
            yield point(now + timedelta(minutes=i), 10 - i)

    result = run_cash_future_strategy(source(), lambda current, history: "HOLD", strategy_id="stream")
    assert len(result.signals) == 3
    assert consumed == [0, 1, 2]


def test_strategy_stops_consuming_after_end_date():
    now = datetime(2026, 9, 2, 10, 0)
    consumed = []

    def source():
        for i in range(4):
            consumed.append(i)
            yield point(now + timedelta(days=i), 10)

    result = run_cash_future_strategy(
        source(), lambda current, history: "HOLD", strategy_id="stop",
        config=CashFutureStrategyConfig(end_date=now.date()),
    )
    assert len(result.signals) == 1
    assert consumed == [0, 1]


def test_streaming_runner_rejects_out_of_order_points():
    now = datetime(2026, 9, 2, 10, 0)
    def source():
        yield point(now + timedelta(minutes=1), 10)
        yield point(now, 9)
    try:
        run_cash_future_strategy(source(), lambda current, history: "HOLD", strategy_id="order")
    except ValueError as exc:
        assert "ordered by timestamp" in str(exc)
    else:
        raise AssertionError("out-of-order stream was accepted")


def test_history_window_bounds_visible_strategy_history():
    now = datetime(2026, 9, 2, 10, 0)
    observed_lengths = []

    def strategy(current, history):
        observed_lengths.append(len(history))
        assert history[-1] is current
        return "HOLD"

    run_cash_future_strategy(
        [point(now + timedelta(minutes=i), 10 - i) for i in range(5)],
        strategy,
        strategy_id="window",
        config=CashFutureStrategyConfig(history_window=2),
    )

    assert observed_lengths == [1, 2, 2, 2, 2]


def test_history_window_must_be_positive():
    try:
        CashFutureStrategyConfig(history_window=0)
    except ValueError as exc:
        assert "history_window must be positive" in str(exc)
    else:
        raise AssertionError("non-positive history_window was accepted")
