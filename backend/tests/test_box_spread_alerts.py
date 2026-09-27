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
