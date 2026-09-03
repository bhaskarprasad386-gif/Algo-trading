from datetime import datetime, timedelta

from app.scanner.cash_future_backtest import BacktestConfig, run_backtest


def test_backtest_accepts_ordered_iterable_without_full_dataset_list():
    class GuardedIterable:
        def __iter__(self):
            for i in range(4):
                yield type("Point", (), {
                    "timestamp": datetime(2026, 1, 1) + timedelta(minutes=i),
                    "contract_month": "CURRENT",
                    "gap": 10.0 if i == 0 else 0.0,
                    "lot_size": 10,
                    "cash_price": 100.0,
                    "margin_required": 100.0,
                    "expiry_date": None,
                })()

    result = run_backtest(
        GuardedIterable(),
        BacktestConfig(min_entry_gap=5.0, exit_gap=0.0, contract_month="CURRENT"),
    )

    assert result["trade_count"] == 1
    assert result["net_profit"] == 100.0
