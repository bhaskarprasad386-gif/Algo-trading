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

    assert "LOW CE Bid/Ask: ₹10.00/₹11.00" in message
    assert "LOW PE Bid/Ask: ₹1.00/₹2.00" in message
    assert "HIGH CE Bid/Ask: ₹8.00/₹9.00" in message
    assert "HIGH PE Bid/Ask: ₹2.00/₹3.00" in message
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
    assert "LOW CE Bid/Ask: ₹10.00/₹11.00" in message
    assert "HIGH PE Bid/Ask: ₹2.00/₹3.00" in message
