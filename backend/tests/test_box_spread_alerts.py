from types import SimpleNamespace

from app.notifications.box_spread_alerts import BoxSpreadAlertService


def _result():
    low = SimpleNamespace(
        underlying="ABC", instrument_class="STOCK", expiry="31DEC2026",
        strike=100.0, timestamp_ns=123, call_bid=10.0, call_ask=11.0,
        put_bid=1.0, put_ask=2.0, lot_size=10,
    )
    high = SimpleNamespace(
        strike=110.0, call_bid=8.0, call_ask=9.0,
        put_bid=2.0, put_ask=3.0,
    )
    return SimpleNamespace(
        low=low, high=high, direction="LONG", strike_distance=1,
        executable_edge=6.0, edge_per_lot=60.0, gross_pnl=60.0,
    )


def test_box_alert_message_contains_exact_bid_ask_snapshot():
    message = BoxSpreadAlertService._message(_result())

    assert "LOW CE Bid/Ask: ₹10.00 (—)/₹11.00 (—)" in message
    assert "LOW PE Bid/Ask: ₹1.00 (—)/₹2.00 (—)" in message
    assert "HIGH CE Bid/Ask: ₹8.00 (—)/₹9.00 (—)" in message
    assert "HIGH PE Bid/Ask: ₹2.00 (—)/₹3.00 (—)" in message
    assert "Executable Edge: ₹6.0000" in message


def test_box_alert_cooldown_identity_includes_expiry():
    result = _result()
    service = BoxSpreadAlertService()
    key_a = (result.low.underlying, result.low.expiry, result.low.strike, result.high.strike, result.direction)
    result.low.expiry = "30JAN2027"
    key_b = (result.low.underlying, result.low.expiry, result.low.strike, result.high.strike, result.direction)
    assert key_a != key_b
    assert key_a not in service._last_sent
    assert key_b not in service._last_sent


def test_box_alert_message_preserves_bse_index_and_bid_ask_fields():
    result = _result()
    result.low.underlying = "SENSEX"
    result.low.instrument_class = "INDEX"
    message = BoxSpreadAlertService._message(result)
    assert "SENSEX INDEX" in message
    assert "LOW CE Bid/Ask: ₹10.00 (—)/₹11.00 (—)" in message
    assert "HIGH PE Bid/Ask: ₹2.00 (—)/₹3.00 (—)" in message


def test_box_alert_message_shows_depth_qty_next_to_each_bid_ask_price():
    result = _result()
    result.low.call_bid_qty = 15
    result.low.call_ask_qty = 25
    result.low.put_bid_qty = 35
    result.low.put_ask_qty = 45
    result.high.call_bid_qty = 55
    result.high.call_ask_qty = 65
    result.high.put_bid_qty = 75
    result.high.put_ask_qty = 85

    message = BoxSpreadAlertService._message(result)
    assert "₹10.00 (15)/₹11.00 (25)" in message
    assert "₹1.00 (35)/₹2.00 (45)" in message
    assert "₹8.00 (55)/₹9.00 (65)" in message
    assert "₹2.00 (75)/₹3.00 (85)" in message



def test_alert_eligibility_requires_tradable_depth_and_at_least_two_lots():
    two_lots = _result()
    two_lots.tradability_status = "TRADABLE"
    two_lots.max_executable_lots = 2

    three_lots = _result()
    three_lots.tradability_status = "TRADABLE"
    three_lots.max_executable_lots = 3

    one_lot = _result()
    one_lot.tradability_status = "NOT_TRADABLE"
    one_lot.max_executable_lots = 1

    rejected = _result()
    rejected.tradability_status = "NOT_TRADABLE"
    rejected.max_executable_lots = 5

    assert BoxSpreadAlertService._eligible_results(
        (two_lots, three_lots, one_lot, rejected)
    ) == (two_lots, three_lots)


def test_alert_eligibility_fails_closed_when_depth_quantity_is_missing():
    missing = _result()
    missing.tradability_status = "TRADABLE"

    assert BoxSpreadAlertService._eligible_results((missing,)) == ()
