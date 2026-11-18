from types import SimpleNamespace
from time import time_ns

from app.market_data.contracts import InstrumentKey
from app.market_data.normalizer import AngelOneTickNormalizer
from app.market_data.registry import InstrumentDescriptor
from app.scanner.box_spread_live_depth_shadow import BoxLiveDepthShadowFeed


class FakeFeed:
    def __init__(self):
        self.callback = None

    def start(self, descriptors, callback):
        self.descriptors = tuple(descriptors)
        self.callback = callback

    def stop(self):
        self.callback = None


def test_normalized_angel_one_ticks_keep_raw_five_level_depth_for_four_leg_snapshot():
    """Exercise the actual broker normalizer -> shadow callback -> four-leg VWAP path."""
    expiry = "06NOV2026"
    expiry_key = 20261106
    lot_size = 65
    now = time_ns()
    specs = (
        ("101", 25000.0, "CE", 101.0, 90.0),
        ("102", 26000.0, "CE", 101.0, 90.0),
        ("103", 26000.0, "PE", 101.0, 90.0),
        ("104", 25000.0, "PE", 101.0, 90.0),
    )
    descriptors = tuple(
        InstrumentDescriptor(
            key=InstrumentKey("NFO", "NFO", token),
            symbol=f"NIFTY{expiry}{int(strike)}{option_type}",
            instrument_type="option",
            exchange="NFO",
            segment="NFO",
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            lot_size=lot_size,
            underlying="NIFTY",
        )
        for token, strike, option_type, _ask, _bid in specs
    )
    feed = FakeFeed()
    shadow = BoxLiveDepthShadowFeed(enabled=True, feed=feed)
    shadow.start(descriptors)
    normalizer = AngelOneTickNormalizer()

    for descriptor, (token, _strike, _type, ask, bid) in zip(descriptors, specs):
        raw = {
            "token": token,
            "exchange_timestamp_ns": str(now),
            "last_traded_price": "10000",
            "best_5_buy_data": [
                {"price": str(int((bid - level) * 100)), "quantity": "26"}
                for level in range(5)
            ],
            "best_5_sell_data": [
                {"price": str(int((ask + level) * 100)), "quantity": "26"}
                for level in range(5)
            ],
        }
        record = normalizer.normalize(descriptor, raw)
        assert record.payload == raw
        feed.callback(record)

    low = SimpleNamespace(
        underlying="NIFTY", expiry=expiry_key, strike=25000.0, lot_size=lot_size,
        call_timestamp_ns=now, put_timestamp_ns=now, timestamp_ns=now,
    )
    high = SimpleNamespace(
        underlying="NIFTY", expiry=expiry_key, strike=26000.0, lot_size=lot_size,
        call_timestamp_ns=now, put_timestamp_ns=now, timestamp_ns=now,
    )
    result = shadow.compare_opportunity(
        SimpleNamespace(low=low, high=high, direction="LONG", executable_edge=1.0),
        now_ns=now,
    )

    assert result["status"] == "COMPLETE"
    assert result["max_executable_lots"] == 2
    assert result["quantity"] == 2 * lot_size
    assert len(result["legs"]) == 4
    assert all(len(leg["bid_depth_levels"]) == 5 for leg in result["legs"])
    assert all(len(leg["ask_depth_levels"]) == 5 for leg in result["legs"])
    assert all(leg["selected_quantity"] == 2 * lot_size for leg in result["legs"])
    shadow.stop()
