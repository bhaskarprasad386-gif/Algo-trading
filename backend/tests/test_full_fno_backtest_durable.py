from datetime import datetime

from app.scanner import full_fno_backtest
from app.scanner.synchronized_replay import ReplayBar


def _bars():
    for minute in range(3):
        yield ReplayBar(
            timestamp=datetime(2026, 1, 2, 9, 15 + minute),
            spot=100.0,
            current_future=102.0,
            near_future=103.0,
            current_expiry=datetime(2026, 1, 29).date(),
            near_expiry=datetime(2026, 2, 26).date(),
            lot_size=50,
        )


def test_full_fno_durable_sink_forces_bounded_result_mode(monkeypatch):
    symbols = ["AAA", "BBB"]
    seen = []
    sinked = []

    monkeypatch.setattr(full_fno_backtest, "persisted_stock_symbols", lambda db: symbols)
    monkeypatch.setattr(
        full_fno_backtest,
        "iter_persisted_symbol_replay",
        lambda db, symbol, start, end: _bars(),
    )

    def fake_backtest(bars, config, cancelled=None):
        assert config.collect_ledger is False
        assert not isinstance(bars, list)
        seen.append(next(iter(bars)))
        return {
            "status": "completed",
            "net_profit": 10.0,
            "max_drawdown": 2.0,
        }

    monkeypatch.setattr(full_fno_backtest, "run_cash_future_paper_backtest", fake_backtest)

    result = full_fno_backtest.run_full_fno_backtest(
        object(),
        days=365,
        min_entry_gap=1.0,
        exit_gap=0.0,
        charges_per_trade=1.0,
        funding_cost_per_trade=0.1,
        max_holding_days=30,
        result_sink=lambda sequence, symbol, item: sinked.append((sequence, symbol, item)),
        collect_results=True,
    )

    assert len(seen) == 2
    assert [(seq, symbol) for seq, symbol, _ in sinked] == [(0, "AAA"), (1, "BBB")]
    assert result["status"] == "completed"
    assert result["symbols_processed"] == 2
    assert result["chunks_written"] == 2
    assert result["total_net_profit"] == 20.0
    assert result["max_drawdown"] == 2.0
    assert result["results"] is None
