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
    assert result["legs"][0]["leg_gross_pnl"] is not None
    assert result["gross_pnl"] is None



def test_trace_exit_uses_full_live_depth_and_weighted_average_price():
    trace = SimpleNamespace(
        id=9, direction="LONG", quantity_lots=2, lot_size=10,
        entry_json=json.dumps({"trace_entry_prices": {
            "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
        }}),
    )
    quotes = {}
    for leg in ("low_call", "low_put", "high_call", "high_put"):
        quotes[leg] = _quote(100, 101, 10)
        quotes[leg] = SimpleNamespace(
            timestamp_ns=time_ns(), bid=100, ask=101, bid_qty=10, ask_qty=10,
            payload={
                "best_5_buy_data": [{"price": 10000, "quantity": 10}, {"price": 9900, "quantity": 10}],
                "best_5_sell_data": [{"price": 10100, "quantity": 10}, {"price": 10200, "quantity": 10}],
            },
        )
    result = _exit_valuation(trace, quotes)
    assert result["status"] == "EXITABLE"
    assert result["max_complete_exit_lots_now"] == 2
    assert result["gross_pnl"] == -80
    assert all(leg["executable_quantity"] == 20 for leg in result["legs"])
    assert all(leg["exit_price"] == 99.5 for leg in result["legs"] if leg["exit_side"] == "SELL")
    assert all(leg["exit_price"] == 101.5 for leg in result["legs"] if leg["exit_side"] == "BUY")


def test_trace_exit_partial_depth_reports_weighted_price_only_for_available_quantity():
    trace = SimpleNamespace(
        id=10, direction="LONG", quantity_lots=2, lot_size=10,
        entry_json=json.dumps({"trace_entry_prices": {
            "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
        }}),
    )
    quotes = {}
    for leg in ("low_call", "low_put", "high_call", "high_put"):
        quotes[leg] = SimpleNamespace(
            timestamp_ns=time_ns(), bid=100, ask=101, bid_qty=10, ask_qty=10,
            payload={
                "best_5_buy_data": [{"price": 10000, "quantity": 10}],
                "best_5_sell_data": [{"price": 10100, "quantity": 10}],
            },
        )
    result = _exit_valuation(trace, quotes)
    assert result["status"] == "PARTIAL_DEPTH"
    assert result["max_complete_exit_lots_now"] == 1
    assert result["gross_pnl"] is None
    assert all(leg["executable_quantity"] == 10 for leg in result["legs"])



def test_trace_exit_reports_saved_entry_edge_and_recomputed_latest_long_edge():
    trace = SimpleNamespace(
        id=11, direction="LONG", quantity_lots=1, lot_size=10,
        entry_json=json.dumps({
            "low_strike": 100, "high_strike": 120, "executable_edge": 1,
            "trace_entry_prices": {
                "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
            },
        }),
    )
    result = _exit_valuation(trace, {
        "low_call": _quote(105, 106),
        "low_put": _quote(49, 50),
        "high_call": _quote(79, 80),
        "high_put": _quote(45, 46),
    })
    assert result["entry_edge"] == 1
    assert result["latest_edge"] == -4


def test_trace_exit_reports_recomputed_latest_short_edge():
    trace = SimpleNamespace(
        id=12, direction="SHORT", quantity_lots=1, lot_size=10,
        entry_json=json.dumps({
            "low_strike": 100, "high_strike": 120, "executable_edge": 6,
            "trace_entry_prices": {
                "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
            },
        }),
    )
    result = _exit_valuation(trace, {
        "low_call": _quote(15, 16),
        "low_put": _quote(10, 11),
        "high_call": _quote(2, 3),
        "high_put": _quote(25, 26),
    })
    assert result["entry_edge"] == 6
    assert result["latest_edge"] == 6


def test_trace_exit_latest_edge_is_unavailable_when_any_leg_is_stale():
    from time import time_ns
    trace = SimpleNamespace(
        id=13, direction="LONG", quantity_lots=1, lot_size=10,
        entry_json=json.dumps({
            "low_strike": 100, "high_strike": 120, "executable_edge": 1,
            "trace_entry_prices": {
                "low_call": 100, "low_put": 100, "high_call": 100, "high_put": 100,
            },
        }),
    )
    stale = SimpleNamespace(timestamp_ns=time_ns() - 10_000_000_000,
                            bid=79, ask=80, bid_qty=20, ask_qty=20)
    result = _exit_valuation(trace, {
        "low_call": _quote(105, 106),
        "low_put": _quote(49, 50),
        "high_call": stale,
        "high_put": _quote(45, 46),
    })
    assert result["entry_edge"] == 1
    assert result["latest_edge"] is None
