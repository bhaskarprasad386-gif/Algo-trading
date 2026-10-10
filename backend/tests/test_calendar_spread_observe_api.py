import time

from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
from app.scanner import calendar_spread_routes


def _payload(token: str, expiry: str, timestamp_ns: int, bid: float, ask: float) -> dict:
    return {
        "exchange": "NFO",
        "segment": "NFO",
        "token": token,
        "symbol": f"NIFTY-{expiry}",
        "instrument_type": "future",
        "timestamp_ns": timestamp_ns,
        "timeframe": "1s",
        "ltp": (bid + ask) / 2,
        "bid": bid,
        "ask": ask,
        "bid_qty": 100,
        "ask_qty": 100,
        "underlying": "NIFTY",
        "expiry": expiry,
        "lot_size": 50,
        "tick_size": 0.05,
    }


def test_calendar_observe_to_pair_and_live_api():
    scanner = LiveCalendarSpreadScanner(minimum_gap_points=0, minimum_gross_profit=0)
    ts = time.time_ns()

    assert scanner.observe(_payload("near", "2026-10-29", ts, 99, 100)) is None
    result = scanner.observe(_payload("far", "2026-11-26", ts, 104, 105))

    assert result is not None
    assert result.gap_points == 4
    assert result.qualifies is True
    assert len(scanner.pair_snapshot()) == 1
    assert len(scanner.snapshot()) == 1

    calendar_spread_routes.configure(scanner)
    try:
        pairs_response = calendar_spread_routes.pairs(limit=10)
        live_response = calendar_spread_routes.live(limit=10, min_gap_points=0, min_gross_profit=0)
    finally:
        calendar_spread_routes.configure(None)

    assert pairs_response["status"] == "success"
    assert pairs_response["pair_count"] == 1
    assert pairs_response["data"][0]["near_contract_month"] == "2026-10-29"
    assert pairs_response["data"][0]["far_contract_month"] == "2026-11-26"
    assert live_response["status"] == "success"
    assert live_response["opportunity_count"] == 1
    assert live_response["data"][0]["qualifies"] is True
