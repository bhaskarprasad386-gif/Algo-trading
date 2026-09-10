from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.continuous_futures import build_continuous_futures_series
from app.backtesting.engine import BacktestEngine
from app.backtesting.fno_rollover import FNORolloverWindow
from app.backtesting.historical_catalog import HistoricalRecord


IST = ZoneInfo("Asia/Kolkata")


def _ns(day: date, hour: int = 10, minute: int = 0) -> int:
    return int(datetime.combine(day, time(hour, minute), tzinfo=IST).timestamp() * 1_000_000_000)


def _record(token: str, day: date, close: float, *, signal: int = 0) -> HistoricalRecord:
    return HistoricalRecord(
        source="test",
        instrument=f"NFO:{token}",
        timeframe="1m",
        timestamp_ns=_ns(day),
        payload={"close": close, "signal": signal},
    )


def _strategy(name: str, signal: int) -> Strategy:
    return Strategy(
        name=name,
        rules=(StrategyRule("signal", lambda context: context.get("signal") == signal),),
    )


def test_engine_runs_one_stream_across_multiple_expiry_contracts():
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 28), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 2, 2)),
    )
    records = {
        "JAN": (_record("JAN", date(2026, 1, 28), 100.0, signal=1), _record("JAN", date(2026, 1, 29), 102.0)),
        "FEB": (_record("FEB", date(2026, 1, 30), 103.0), _record("FEB", date(2026, 2, 2), 105.0, signal=2)),
    }

    result = BacktestEngine().run_continuous_futures(
        windows,
        records,
        _strategy("entry", 1),
        _strategy("exit", 2),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_timestamp == _ns(date(2026, 1, 28))
    assert trade.exit_timestamp == _ns(date(2026, 2, 2))
    assert trade.entry_price == 100.0
    assert trade.exit_price == 105.0


def test_rollover_boundary_has_no_artificial_candle_and_preserves_raw_payload():
    jan = _record("JAN", date(2026, 1, 29), 100.0)
    feb = _record("FEB", date(2026, 1, 30), 110.0)
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 1, 30)),
    )

    series = build_continuous_futures_series(windows, {"JAN": (jan,), "FEB": (feb,)})

    assert len(series) == 2
    assert [item.contract_token for item in series] == ["JAN", "FEB"]
    assert [item.timestamp_ns for item in series] == [_ns(date(2026, 1, 29)), _ns(date(2026, 1, 30))]
    assert [item.payload["close"] for item in series] == [100.0, 110.0]
    assert "rollover" not in jan.payload
    assert "rollover" not in feb.payload


def test_missing_rollover_data_remains_a_gap_instead_of_becoming_a_fake_candle():
    windows = (
        FNORolloverWindow("ABC", "STOCK_FUTURE", "JAN", date(2026, 1, 29), date(2026, 1, 29)),
        FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 1, 30), date(2026, 1, 30)),
    )
    jan = _record("JAN", date(2026, 1, 29), 100.0, signal=1)

    result = BacktestEngine().run_continuous_futures(
        windows,
        {"JAN": (jan,), "FEB": ()},
        _strategy("entry", 1),
        _strategy("exit", 2),
    )

    assert result.trades == ()
    assert result.final_capital == result.initial_capital


def test_continuous_futures_keeps_contract_identity_available_to_derived_candles():
    windows = (FNORolloverWindow("ABC", "STOCK_FUTURE", "FEB", date(2026, 2, 2), date(2026, 2, 2)),)
    record = _record("FEB", date(2026, 2, 2), 105.0)

    series = build_continuous_futures_series(windows, {"FEB": (record,)})
    assert series[0].contract_token == "FEB"
    assert series[0].payload["close"] == 105.0
