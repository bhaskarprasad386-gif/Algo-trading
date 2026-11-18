from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAYOUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_full_fno_backtest.xml"


def test_cash_future_ui_has_no_free_historical_1m_download_dependency():
    layout = LAYOUT.read_text(encoding="utf-8")
    assert "CashFutureDataDownloadView" not in layout
    assert "No free historical 1-minute download dependency" in layout
    assert "live Angel One data is accumulated incrementally" in layout


def test_cash_future_replay_keeps_resolution_source_aware():
    replay = (ROOT / "backend" / "app" / "backtesting" / "cash_future_replay_routes.py").read_text(encoding="utf-8")
    assert "available_replay_intervals" in replay
    assert "Never fabricate resolution" in replay
    assert '"source_min_interval_seconds"' in replay
    assert '"angelone-live-1s"' in replay
