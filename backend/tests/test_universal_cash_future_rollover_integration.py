from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
from app.backtesting.contracts import EventContext, HistoricalRecord
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
    return EventContext(record)


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
