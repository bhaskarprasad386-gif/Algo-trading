from datetime import datetime, timedelta

from app.scanner.cash_future_backtest import BacktestConfig, run_backtest
from app.scanner import full_fno_backtest


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

    result = run_backtest(GuardedIterable(), BacktestConfig(min_entry_gap=5.0, exit_gap=0.0, contract_month="CURRENT"))
    assert result["trade_count"] == 1
    assert result["net_profit"] == 100.0


def test_full_fno_resume_skips_durable_symbol_prefix(monkeypatch):
    symbols = ["AAA", "BBB", "CCC"]
    replayed = []
    written = []
    monkeypatch.setattr(full_fno_backtest, "persisted_stock_symbols", lambda db: symbols)
    monkeypatch.setattr(full_fno_backtest, "iter_persisted_symbol_replay", lambda db, symbol, start, end: replayed.append(symbol) or [])
    monkeypatch.setattr(full_fno_backtest, "run_cash_future_paper_backtest", lambda bars, config, cancelled=None: {"status": "completed", "net_profit": 1.0, "max_drawdown": 0.0})

    result = full_fno_backtest.run_full_fno_backtest(
        object(), days=30, min_entry_gap=5.0, exit_gap=0.0, charges_per_trade=1.0,
        funding_cost_per_trade=0.1, max_holding_days=5, future_selection="BOTH",
        result_sink=lambda sequence, symbol, item: written.append((sequence, symbol)),
        collect_results=False, resume_after_sequence=1,
    )

    assert replayed == ["CCC"]
    assert written == [(2, "CCC")]
    assert result["symbols_processed"] == 3
    assert result["chunks_written"] == 1


def test_full_fno_resume_rejects_sequence_beyond_universe(monkeypatch):
    monkeypatch.setattr(full_fno_backtest, "persisted_stock_symbols", lambda db: ["AAA"])
    try:
        full_fno_backtest.run_full_fno_backtest(
            object(), days=30, min_entry_gap=5.0, exit_gap=0.0, charges_per_trade=1.0,
            funding_cost_per_trade=0.1, max_holding_days=5, resume_after_sequence=1,
        )
    except ValueError as exc:
        assert "exceeds persisted symbol universe" in str(exc)
    else:
        raise AssertionError("expected invalid resume sequence to fail")
