from app.backtesting.high_resolution_ledger import HighResolutionLedgerWriter
from app.backtesting.high_resolution_streaming import HighResolutionStreamingRunner
from app.backtesting.ledger import BacktestLedger
from app.backtesting.universal import MarketEvent


class BuyThenSell:
    def __init__(self):
        self.seen = 0

    def on_event(self, event):
        self.seen += 1
        if self.seen == 1:
            return {"side": "BUY", "quantity": 2}
        if self.seen == 3:
            return {"side": "SELL", "quantity": 2}
        return None


def test_streaming_path_keeps_only_bounded_state_and_persists_trade(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "ledger.db"))
    ledger.start_run("stream-1", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "stream-1")

    events = (
        MarketEvent(30, "NIFTY", data=(("price", 103.0),)),
        MarketEvent(10, "NIFTY", data=(("price", 100.0),)),
        MarketEvent(20, "NIFTY", data=(("price", 101.0),)),
    )
    result = HighResolutionStreamingRunner().run(events, BuyThenSell(), writer)

    assert result.events_processed == 3
    assert result.signals_processed == 2
    assert result.trades_closed == 1
    assert result.net_pnl == 2.0
    assert result.open_positions == 0
    records = writer.records()
    assert len(records) == 1
    assert records[0].payload["entry_timestamp_ns"] == 10
    assert records[0].payload["exit_timestamp_ns"] == 30
    assert records[0].payload["net_pnl"] == 2.0
    ledger.close()


def test_streaming_runner_consumes_generator_without_event_history(tmp_path):
    ledger = BacktestLedger(str(tmp_path / "ledger.db"))
    ledger.start_run("stream-2", "strategy", "1", 1000)
    writer = HighResolutionLedgerWriter(ledger, "stream-2")

    def events():
        for ts in range(1, 1001):
            yield MarketEvent(ts, "NIFTY", data=(("price", 100.0),))

    class NeverTrade:
        def on_event(self, event):
            return None

    result = HighResolutionStreamingRunner().run(events(), NeverTrade(), writer)
    assert result.events_processed == 1000
    assert result.trades_closed == 0
    assert result.net_pnl == 0.0
    assert writer.records() == ()
    ledger.close()
