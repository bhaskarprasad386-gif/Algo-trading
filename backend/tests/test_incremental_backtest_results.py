from datetime import date, datetime, timedelta

from app.scanner.cash_future_paper_backtest import PaperBacktestConfig, run_cash_future_paper_backtest
from app.scanner.synchronized_replay import ReplayBar
from app.scanner import full_fno_backtest


def test_paper_backtest_can_stream_without_minute_ledger():
    start = datetime(2026, 1, 1, 10, 0)
    bars = [
        ReplayBar(start, 100.0, 105.0, 106.0, date(2026, 1, 29), date(2026, 2, 26), 10),
        ReplayBar(start + timedelta(minutes=1), 101.0, 101.0, 106.0, date(2026, 1, 29), date(2026, 2, 26), 10),
    ]
    result = run_cash_future_paper_backtest(
        bars,
        PaperBacktestConfig(
            future_selection="CURRENT",
            min_entry_gap=5.0,
            exit_gap=0.0,
            collect_ledger=False,
        ),
    )

    assert result["status"] == "completed"
    assert "ledger" not in result
    assert result["net_profit"] == 10.0


def test_full_fno_streams_symbol_results_to_sink_without_accumulating(monkeypatch):
    monkeypatch.setattr(full_fno_backtest, "persisted_stock_symbols", lambda db: ["AAA", "BBB"])
    monkeypatch.setattr(full_fno_backtest, "iter_persisted_symbol_replay", lambda db, symbol, start, end: iter(()))
    monkeypatch.setattr(
        full_fno_backtest,
        "run_cash_future_paper_backtest",
        lambda bars, config, cancelled=None: {
            "status": "no_entry",
            "future_selection": config.future_selection,
            "starting_capital": config.starting_capital,
            "ending_capital": config.starting_capital,
            "net_profit": 0.0,
        },
    )

    chunks = []
    result = full_fno_backtest.run_full_fno_backtest(
        object(),
        days=365,
        min_entry_gap=5.0,
        exit_gap=0.0,
        charges_per_trade=0.0,
        funding_cost_per_trade=0.0,
        max_holding_days=30,
        future_selection="BOTH",
        result_sink=lambda sequence, symbol, item: chunks.append((sequence, symbol, item)),
        collect_results=False,
    )

    assert result["status"] == "completed"
    assert result["results"] is None
    assert result["chunks_written"] == 2
    assert result["symbols_processed"] == 2
    assert [symbol for _, symbol, _ in chunks] == ["AAA", "BBB"]
    assert [sequence for sequence, _, _ in chunks] == [0, 1]
