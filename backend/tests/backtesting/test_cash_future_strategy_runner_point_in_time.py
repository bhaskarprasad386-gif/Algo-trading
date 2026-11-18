from datetime import datetime

from app.backtesting.cash_future_strategy_runner import (
    CashFutureStrategyConfig,
    run_cash_future_strategy,
)
from app.scanner.cash_future_history import CashFutureHistoryPoint


def point(timestamp: str, contract: str, gap: float) -> CashFutureHistoryPoint:
    return CashFutureHistoryPoint(
        timestamp=datetime.fromisoformat(timestamp),
        symbol="SBIN",
        contract_month=contract,
        cash_price=100.0,
        future_price=100.0 + gap,
        gap=gap,
        gap_pct=gap,
        lot_size=15,
        margin_required=10_000.0,
    )


def test_strategy_is_evaluated_point_in_time_without_future_observations():
    seen = []

    def strategy(current, history):
        seen.append((current.timestamp, tuple(p.timestamp for p in history)))
        return "HOLD"

    run_cash_future_strategy(
        [
            point("2026-09-01T09:15:00", "202609", 1.0),
            point("2026-09-01T09:16:00", "202609", 2.0),
            point("2026-09-01T09:17:00", "202609", -1.0),
        ],
        strategy,
        strategy_id="test",
        config=CashFutureStrategyConfig(),
    )

    assert seen[0][1] == (datetime.fromisoformat("2026-09-01T09:15:00"),)
    assert seen[1][1] == (
        datetime.fromisoformat("2026-09-01T09:15:00"),
        datetime.fromisoformat("2026-09-01T09:16:00"),
    )
    assert seen[2][1] == (
        datetime.fromisoformat("2026-09-01T09:15:00"),
        datetime.fromisoformat("2026-09-01T09:16:00"),
        datetime.fromisoformat("2026-09-01T09:17:00"),
    )


def test_strategy_run_keeps_selected_contract_isolated():
    def strategy(current, history):
        return "BUY" if current.gap > 0 else "SELL"

    result = run_cash_future_strategy(
        [
            point("2026-09-01T09:15:00", "202609", 2.0),
            point("2026-09-01T09:16:00", "202610", -5.0),
            point("2026-09-01T09:17:00", "202609", -1.0),
        ],
        strategy,
        strategy_id="test",
        config=CashFutureStrategyConfig(contract_month="202609"),
    )

    assert len(result.signals) == 2
    assert all(signal["contract_month"] == "202609" for signal in result.signals)
    assert len(result.trades) == 1
    assert result.trades[0]["contract_month"] == "202609"
