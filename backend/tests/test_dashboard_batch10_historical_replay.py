from pathlib import Path

HTML = Path("web/dashboard/index.html").read_text(encoding="utf-8")


def test_batch10_historical_replay_workspace_contract():
    required = [
        'id="historicalReplayNav"',
        'id="historicalReplayPage"',
        "Historical + Replay",
        "/api/v1/backtesting/replay/instruments",
        "/api/v1/backtesting/replay/stream",
        "angelone-live-1s",
        'id="histDate"',
        'id="histInstrument"',
        'id="histTimeframe"',
        "histPlay()",
        "histPause()",
        "histStep(1)",
        "histReset()",
        "histSetSpeed('1s')",
        "histSetSpeed('30s')",
        "histSetSpeed('1m')",
        "histSetSpeed('5m')",
        "histSetSpeed('15m')",
        "histSetSpeed('30m')",
        "histSetSpeed('1h')",
        "HISTORICAL REPLAY • BROKER ORDERS OFF",
        "no historical downloader",
    ]
    for marker in required:
        assert marker in HTML, marker
