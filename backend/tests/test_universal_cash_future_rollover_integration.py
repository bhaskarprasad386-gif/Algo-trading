from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
from app.backtesting.backtest_result import BacktestRunWriter
from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec
from app.backtesting.contracts import EventContext, HistoricalRecord
from app.backtesting.result_ledger import BacktestResultLedger
from app.backtesting.universal_engine import UniversalEventBacktestEngine


def _context(ts, future_instrument, cash_bid, cash_ask, future_bid, future_ask):
    record = HistoricalRecord(
        "test",
        "NSE:ABC",
        "tick",
        ts,
        {
            "cash": {
                "bid": cash_bid,
                "ask": cash_ask,
                "bid_quantity": 20,
                "ask_quantity": 20,
            },
            "future": {
                "bid": future_bid,
                "ask": future_ask,
                "bid_quantity": 20,
                "ask_quantity": 20,
            },
            "__replay_legs__": {
                "cash": {
                    "source": "test",
                    "instrument": "NSE:ABC",
                    "timeframe": "tick",
                },
                "future": {
                    "source": "test",
                    "instrument": future_instrument,
                    "timeframe": "tick",
                },
            },
        },
        ts,
    )
    return EventContext(record.timestamp_ns, record.sequence, record.source, record.instrument, record.payload, record)


def _writer(tmp_path, run_id):
    ledger = BacktestResultLedger(tmp_path / f"{run_id}.db")
    spec = BacktestRunSpec(
        run_id=run_id,
        strategy_id="universal-cash-future",
        strategy_version="v1",
        instrument="ABC",
        start_ns=1,
        end_ns=2,
        resolution=BacktestResolution("tick", "historical", 1, 2),
        parameters={},
        data_watermarks={"ABC": 2},
        initial_capital=100_000.0,
    )
    return ledger, BacktestRunWriter(ledger, spec)


def test_universal_multi_leg_context_executes_bound_source_and_strategy():
    from app.backtesting.contracts import RunContext
    from app.backtesting.clock import BacktestClock

    adapter = CashFutureUniversalMultiLegAdapter(
        direction="LONG_CASH_SHORT_FUTURE",
        quantity=10,
    )
    records = [
        _context(1, "NFO:ABC-OLD", 100.0, 101.0, 104.0, 105.0).record,
    ]

    class Source:
        def iter_events(self, *, start_ns=None, end_ns=None):
            return iter(records)

    engine = UniversalEventBacktestEngine(100_000.0)
    context = RunContext(
        spec=BacktestRunSpec(
            run_id="multi-leg-context",
            strategy_id="universal-cash-future",
            strategy_version="v1",
            instrument="ABC",
            start_ns=1,
            end_ns=1,
            resolution=BacktestResolution("tick", "historical", 1, 1),
            parameters={},
            data_watermarks={"ABC": 1},
        ),
        clock=engine.clock,
        data_source=Source(),
        strategy=adapter,
        execution=engine.execution,
        portfolio=engine.portfolio,
        result_writer=None,
    )

    result = engine.run_multi_leg_context(context)

    assert result.fill_count == 2
    assert engine.portfolio.positions["NSE:ABC"].quantity == 10
    assert engine.portfolio.positions["NFO:ABC-OLD"].quantity == -10


def test_cash_future_rollover_closes_original_contract_and_accounts_pnl():
    adapter = CashFutureUniversalMultiLegAdapter(
        direction="LONG_CASH_SHORT_FUTURE",
        quantity=10,
    )
    open_event = _context(1, "NFO:ABC-OLD", 100.0, 101.0, 104.0, 105.0)
    close_event = _context(2, "NFO:ABC-NEW", 106.0, 107.0, 102.0, 103.0)

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_multi_leg([open_event, close_event], adapter)

    assert result.fill_count == 4
    assert engine.portfolio.positions["NSE:ABC"].quantity == 0
    assert engine.portfolio.positions["NFO:ABC-OLD"].quantity == 0
    assert "NFO:ABC-NEW" not in engine.portfolio.positions
    assert result.realized_pnl == 60.0


def test_cash_future_rollover_persists_all_four_fills_with_original_contract(tmp_path):
    ledger, writer = _writer(tmp_path, "cf-rollover-durable")
    adapter = CashFutureUniversalMultiLegAdapter(
        direction="LONG_CASH_SHORT_FUTURE",
        quantity=10,
    )
    open_event = _context(1, "NFO:ABC-OLD", 100.0, 101.0, 104.0, 105.0)
    close_event = _context(2, "NFO:ABC-NEW", 106.0, 107.0, 102.0, 103.0)

    engine = UniversalEventBacktestEngine(
        100_000.0,
        result_writer=writer,
        retain_history=False,
    )
    result = engine.run_multi_leg([open_event, close_event], adapter)

    fills = ledger.fills("cf-rollover-durable")
    assert result.fill_count == 4
    assert len(fills) == 4
    assert [row["sequence"] for row in fills] == [0, 1, 2, 3]
    assert [(row["instrument"], row["side"], row["quantity"], row["price"]) for row in fills] == [
        ("NSE:ABC", "BUY", 10.0, 101.0),
        ("NFO:ABC-OLD", "SELL", 10.0, 104.0),
        ("NSE:ABC", "SELL", 10.0, 106.0),
        ("NFO:ABC-OLD", "BUY", 10.0, 103.0),
    ]
    assert ledger.run("cf-rollover-durable")["status"] == "COMPLETED"