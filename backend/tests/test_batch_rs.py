"""Batch R/S regressions for shared synthetic and commodity market data."""
from datetime import date

from app.market_data.common_websocket import CommonWebSocketManager
from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.commodity_subscriptions import select_commodity_contracts
from app.market_data.live_synthetic_runner import LiveSyntheticRunner, SyntheticLiveTarget
from app.market_data.live_commodity_stream import LiveCommodityMarketDataRecorder


def _rows():
    return [
        {"exch_seg":"MCX","instrumenttype":"FUTCOM","expiry":"30OCT2026","token":"F1","symbol":"GOLD30OCT26FUT","name":"GOLD","lotsize":"100"},
        {"exch_seg":"MCX","instrumenttype":"FUTCOM","expiry":"30NOV2026","token":"F2","symbol":"GOLD30NOV26FUT","name":"GOLD","lotsize":"100"},
        *[
            {"exch_seg":"MCX","instrumenttype":"OPTFUT","expiry":"30OCT2026","token":f"{s}{side}","symbol":f"GOLD{s}{side}",
             "name":"GOLD","lotsize":"100","strike":str(s),"optiontype":side}
            for s in (70000,70100,70200,70300,70400) for side in ("CE","PE")
        ],
    ]


def test_commodity_selection_is_dynamic_current_near_and_actual_strikes():
    selection = select_commodity_contracts(_rows(), underlying="GOLD", atm_strike=70200, strike_count=2)
    assert [x.token for x in selection.futures] == ["F1", "F2"]
    assert selection.option_count == 10
    assert {x.strike for x in selection.subscriptions if x.option_type} == {70000.0,70100.0,70200.0,70300.0,70400.0}


def test_synthetic_runner_has_no_box_spread_dependency():
    assert "box_spread" not in LiveSyntheticRunner.__module__
    assert SyntheticLiveTarget("NIFTY", "INDEX").instrument_class == "INDEX"


def test_commodity_recorder_uses_common_feed_adapter():
    manager = CommonWebSocketManager()
    feed = CommonStrategyMarketFeed("commodity-test", manager=manager)
    assert feed.manager is manager
    recorder = LiveCommodityMarketDataRecorder(
        "unused", underlyings=["GOLD"], instrument_master=type("M", (), {"download": lambda self: _rows(), "instruments": _rows()})()
    )
    assert recorder.SOURCE == "angelone-commodity-live-1s"
    assert "CommonStrategyMarketFeed" in LiveCommodityMarketDataRecorder.__doc__
