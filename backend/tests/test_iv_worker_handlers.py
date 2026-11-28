from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.scanner.iv_skew_flip.worker_handlers import evaluate_iv_snapshot


def test_worker_handler_returns_fail_closed_result_for_missing_broker_iv():
    payload = {
        "quotes": [
            {
                "underlying": "NIFTY", "expiry": "2026-12-03",
                "strike": strike, "option_type": side,
                "timestamp_ns": 10_000_000_000, "ltp": 10.0,
                "bid": 9.5, "ask": 10.5, "oi": 1000,
                "token": f"{strike}-{side}",
            }
            for strike in (100.0, 150.0, 200.0)
            for side in ("CE", "PE")
        ],
        "config": {
            "symbol": "NIFTY", "expiry_type": "WEEKLY",
            "bullish_skew_threshold": 0.8, "bearish_skew_threshold": -0.8,
        },
        "now": datetime(2026, 11, 28, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")).isoformat(),
        "now_ns": 10_000_000_000,
        "spot": 150.0,
        "previous_atm_iv_diff": None,
        "oi_references": {},
    }

    result = evaluate_iv_snapshot(payload)

    assert result["evaluated_at_ns"] == payload["now_ns"]
    assert result["signals_allowed"] is False
    assert result["confluence"]["status"] != "SIGNAL"
    assert all(item["status"] == "WAITING" for item in result["strategies"][:2])


def test_worker_handler_rejects_non_mapping_payload():
    with pytest.raises(TypeError):
        evaluate_iv_snapshot([])
