from datetime import date

from app.backtesting.backtest_resolution import BacktestResolution
from app.backtesting.backtest_run import BacktestRunSpec


def test_run_is_independent_and_carries_resolution_and_watermarks() -> None:
    resolution = BacktestResolution("s", "angelone", 1_000, 9_000)
    run = BacktestRunSpec(
        run_id="run-1",
        strategy_id="box-spread",
        strategy_version="v3",
        instrument="NFO:NIFTY",
        start_ns=2_000,
        end_ns=8_000,
        resolution=resolution,
        parameters={"distance": 5, "expiry": date(2026, 9, 24).isoformat()},
        data_watermarks={"NIFTY": 8_500},
    )

    assert run.provenance["run_id"] == "run-1"
    assert run.provenance["resolution"] == "s"
    assert run.provenance["source"] == "angelone"
    assert run.provenance["data_watermarks"] == {"NIFTY": 8_500}


def test_run_rejects_resolution_that_does_not_cover_range() -> None:
    resolution = BacktestResolution("m", "archive", 5_000, 8_000)
    try:
        BacktestRunSpec(
            run_id="run-2",
            strategy_id="strategy",
            strategy_version="v1",
            instrument="NFO:ABC",
            start_ns=4_000,
            end_ns=8_000,
            resolution=resolution,
        )
    except ValueError as exc:
        assert "coverage" in str(exc)
    else:
        raise AssertionError("expected incomplete resolution coverage rejection")
