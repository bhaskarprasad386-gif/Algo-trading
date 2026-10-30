from app.market_data.box_spread_subscriptions import select_box_contracts
from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS
from app.market_data.instruments import InstrumentMaster
from app.market_data.synthetic_subscriptions import select_synthetic_contracts


def test_shared_stock_option_window_is_identical_for_box_and_synthetic():
    items = [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK",
         "expiry": "26NOV2026", "token": "900", "symbol": "RELIANCE26NOV26FUT", "lotsize": "250"},
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "FUTSTK",
         "expiry": "24DEC2026", "token": "901", "symbol": "RELIANCE24DEC26FUT", "lotsize": "250"},
    ]
    items += [
        {"exch_seg": "NFO", "name": "RELIANCE", "instrumenttype": "OPTSTK",
         "expiry": "26NOV2026", "strike": str(strike * 100),
         "symbol": f"RELIANCE{strike}{suffix}", "token": f"{strike}{suffix}", "lotsize": "250"}
        for strike in range(80, 121) for suffix in ("CE", "PE")
    ]
    master = InstrumentMaster()
    master.instruments = items
    master._loaded = True

    box = select_box_contracts(
        master, underlying="RELIANCE", instrument_class="STOCK",
        atm_strike=100.0, expiry="26NOV2026",
        allowed_stock_symbols=TOP15_LIQUID_STOCK_SYMBOLS,
    )
    synthetic = select_synthetic_contracts(
        master, underlying="RELIANCE", instrument_class="STOCK",
        atm_strike=100.0, expiry="26NOV2026",
        allowed_stock_symbols=TOP15_LIQUID_STOCK_SYMBOLS,
    )
    box_tokens = {item.token for item in box.subscriptions if item.option_type in {"CE", "PE"}}
    synthetic_tokens = {item.token for item in synthetic.subscriptions if item.option_type in {"CE", "PE"}}
    assert box_tokens == synthetic_tokens
    assert len(box_tokens) == 15 * 2
    assert {item.strike for item in box.subscriptions} == set(float(x) for x in range(93, 108))


def test_top15_liquid_universe_has_exactly_15_symbols():
    assert len(TOP15_LIQUID_STOCK_SYMBOLS) == 15


def test_nifty_weekly_atm15_options_are_box_spread_only():
    items = [
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "FUTIDX",
         "expiry": "26NOV2026", "token": "990", "symbol": "NIFTY26NOV26FUT", "lotsize": "75"},
    ]
    items += [
        {"exch_seg": "NFO", "name": "NIFTY", "instrumenttype": "OPTIDX",
         "expiry": expiry, "strike": str(strike * 100),
         "symbol": f"NIFTY{strike}{suffix}", "token": f"{expiry}-{strike}{suffix}", "lotsize": "75"}
        for expiry in ("22OCT2026", "05NOV2026")
        for strike in range(100, 161) for suffix in ("CE", "PE")
    ]
    master = InstrumentMaster()
    master.instruments = items
    master._loaded = True

    box = select_box_contracts(
        master, underlying="NIFTY", instrument_class="INDEX",
        atm_strike=130.0, expiry="22OCT2026",
    )
    synthetic = select_synthetic_contracts(
        master, underlying="NIFTY", instrument_class="INDEX",
        atm_strike=130.0, expiry="26NOV2026",
    )
    box_options = {item.token for item in box.subscriptions if item.option_type in {"CE", "PE"}}
    synthetic_options = {item.token for item in synthetic.subscriptions if item.option_type in {"CE", "PE"}}
    assert len(box_options) == 31 * 2
    assert all(token.startswith("22OCT2026-") for token in box_options)
    assert len(synthetic_options) == 31 * 2
    assert all(token.startswith("05NOV2026-") for token in synthetic_options)
    assert box_options.isdisjoint(synthetic_options)
