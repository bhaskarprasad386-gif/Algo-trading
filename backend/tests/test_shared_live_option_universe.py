from app.market_data.box_spread_subscriptions import select_box_contracts
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS
from app.market_data.instruments import InstrumentMaster
from app.market_data.synthetic_subscriptions import select_synthetic_contracts


def test_shared_stock_option_window_is_identical_for_box_and_synthetic():
    items = [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK",
         "expiry": "29OCT2026", "token": "900", "symbol": "RELIANCE29OCT26FUT", "lotsize": "250"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK",
         "expiry": "26NOV2026", "token": "901", "symbol": "RELIANCE26NOV26FUT", "lotsize": "250"},
    ]
    items += [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK",
         "expiry": "29OCT2026", "strike": str(strike * 100),
         "symbol": f"RELIANCE{strike}{suffix}", "token": f"{strike}{suffix}", "lotsize": "250"}
        for strike in range(80, 121) for suffix in ("CE", "PE")
    ]
    master = InstrumentMaster()
    master.instruments = items
    master._loaded = True

    box = select_box_contracts(
        master, underlying="RELIANCE", instrument_class="STOCK",
        atm_strike=100.0, expiry="29OCT2026",
        allowed_stock_symbols=TOP15_LIQUID_STOCK_SYMBOLS,
    )
    synthetic = select_synthetic_contracts(
        master, underlying="RELIANCE", instrument_class="STOCK",
        atm_strike=100.0, expiry="29OCT2026",
        allowed_stock_symbols=TOP15_LIQUID_STOCK_SYMBOLS,
    )
    box_tokens = {item.token for item in box.subscriptions if item.option_type in {"CE", "PE"}}
    synthetic_tokens = {item.token for item in synthetic.subscriptions if item.option_type in {"CE", "PE"}}
    assert box_tokens == synthetic_tokens
    assert len(box_tokens) == 15 * 2
    assert {item.strike for item in box.subscriptions} == set(float(x) for x in range(93, 108))


def test_top15_liquid_universe_has_exactly_15_symbols():
    assert len(TOP15_LIQUID_STOCK_SYMBOLS) == 15
