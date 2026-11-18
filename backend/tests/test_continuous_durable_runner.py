from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.backtesting.backtest_ledger import BacktestTradeLedger
from app.backtesting.continuous_durable_runner import run_continuous_futures_events_to_durable_ledger
from app.backtesting.engine import BacktestEngine, EventSignal
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalRecord

IST = ZoneInfo("Asia/Kolkata")


def _ns(day: date) -> int:
    return int(datetime.combine(day, time(10, 0), tzinfo=IST).timestamp() * 1_000_000_000)


def _record(token: str, day: date, close: float) -> HistoricalRecord:
    return HistoricalRecord("test", f"NFO:{token}", "1m", _ns(day), {"close": close})


def test_continuous_durable_runner_persists_actual_run_summary(tmp_path):
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 1, 30)),
    )
    records = {
        "JAN": (_record("JAN", date(2026, 1, 29), 100.0),),
        "FEB": (_record("FEB", date(2026, 1, 30), 108.0),),
    }

    def strategy(context):
        token = context.payload["contract_token"]
        if token == "JAN":
            return EventSignal("BUY")
        if token == "FEB":
            return EventSignal("SELL")
        return EventSignal("HOLD")

    with BacktestTradeLedger(tmp_path / "ledger.sqlite") as ledger:
        result = run_continuous_futures_events_to_durable_ledger(
            BacktestEngine(),
            windows,
            records,
            strategy,
            ledger=ledger,
            run_id="continuous-run-1",
            chunk_size=1,
        )

        summary = ledger.run_summary("continuous-run-1")
        assert summary is not None
        assert summary["net_pnl"] == result.net_pnl == 8.0
        assert summary["final_capital"] == result.final_capital == 100008.0
        assert ledger.count("continuous-run-1") == 1
        assert ledger.net_pnl("continuous-run-1") == 8.0
