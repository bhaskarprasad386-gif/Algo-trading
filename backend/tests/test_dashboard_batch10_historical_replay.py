from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "app" / "main.py"


def test_generic_historical_replay_api_remains_independent_of_retired_cash_future_ui():
    source = MAIN.read_text(encoding="utf-8")
    assert "create_replay_router(settings.BACKTEST_DATA_DB)" in source
    assert "app.include_router(create_replay_router(settings.BACKTEST_DATA_DB))" in source
    assert not (ROOT / "web" / "dashboard" / "index.html").exists()
    assert "CashFutureReplay" not in source
