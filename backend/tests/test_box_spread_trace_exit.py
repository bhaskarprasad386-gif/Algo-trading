import json
from types import SimpleNamespace
from time import time_ns

from app.scanner.box_spread_trace_routes import _entry_sides, _exit_valuation


def _quote(bid, ask, qty=20):
    return SimpleNamespace(timestamp_ns=time_ns(), bid=bid, ask=ask, bid_qty=qty, ask_qty=qty)


def test_long_trace_exit_uses_opposite_executable_sides_and_saved_entry_prices():
    trace = SimpleNamespace(
        id=7, direction="LONG", quantity_lots=1, lot_size=10,
        entry_json=json.dumps({"trace_entry_prices": {
            "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
        }}),
    )
    assert _entry_sides("LONG") == {
        "low_call": "BUY", "low_put": "SELL", "high_call": "SELL", "high_put": "BUY",
    }
    result = _exit_valuation(trace, {
        "low_call": _quote(105, 106),
        "low_put": _quote(49, 50),
        "high_call": _quote(79, 80),
        "high_put": _quote(45, 46),
    })
    assert result["status"] == "EXITABLE"
    assert result["gross_pnl"] == 200
    assert result["can_exit_full_quantity_now"] is True
    assert result["orders_placed"] is False


def test_trace_exit_refuses_full_exit_when_one_leg_depth_is_insufficient():
    trace = SimpleNamespace(
        id=8, direction="SHORT", quantity_lots=2, lot_size=10,
        entry_json=json.dumps({"trace_entry_prices": {
            "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
        }}),
    )
    result = _exit_valuation(trace, {
        "low_call": _quote(99, 100, 30),
        "low_put": _quote(99, 100, 30),
        "high_call": _quote(99, 100, 30),
        "high_put": _quote(99, 100, 10),
    })
    assert result["status"] == "PARTIAL_DEPTH"
    assert result["max_complete_exit_lots_now"] == 1
    assert result["can_exit_full_quantity_now"] is False
    assert result["gross_pnl"] is not None
