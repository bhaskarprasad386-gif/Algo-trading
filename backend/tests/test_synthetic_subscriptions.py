from app.market_data.instruments import InstrumentMaster
from app.market_data.synthetic_subscriptions import select_synthetic_contracts


def _master(items):
    master = InstrumentMaster()
    master.instruments = items
    master._loaded = True
    return master


def test_index_selection_subscribes_actual_chain_for_liquidity_selection():
    items = [
        {"exch_seg":"NFO","name":"NIFTY","instrumenttype":"FUTIDX","expiry":"29OCT2026","token":"900","symbol":"NIFTY29OCT26FUT","lotsize":"65"},
        *[
            {
                "exch_seg":"NFO",
                "name":"NIFTY",
                "instrumenttype":"OPTIDX",
                "expiry":"29OCT2026",
                "token":str(i * 2 + offset),
                "symbol":f"NIFTY{24000 + i * 50}{option_type}",
                "strike":str(2400000+i*5000),
                "lotsize":"65",
            }
            for i in range(1, 62)
            for option_type, offset in (("CE", 0), ("PE", 1))
        ],
    ]
    selected = select_synthetic_contracts(
        _master(items), underlying="NIFTY", instrument_class="INDEX", atm_strike=25550.0,
        expiry="29OCT2026",
    )
    options = [x for x in selected.subscriptions if x.option_type in {"CE", "PE"}]
    strikes = {x.strike for x in options if x.strike is not None}
    assert len(strikes) == 31  # 15 actual strikes per side plus ATM
    assert len(options) == 62  # Angel CE + PE token for every selected strike
    assert {x.option_type for x in options} == {"CE", "PE"}
    assert 25550.0 in strikes
    assert min(strikes) == 24800.0
    assert max(strikes) == 26300.0
    for strike in strikes:
        assert {x.option_type for x in options if x.strike == strike} == {"CE", "PE"}

    # Regression: a weekly chain must never be accepted against the monthly future.
    try:
        select_synthetic_contracts(
            _master(items), underlying="NIFTY", instrument_class="INDEX",
            atm_strike=25550.0, expiry="22OCT2026",
        )
    except LookupError as exc:
        assert "does not match current future expiry" in str(exc)
    else:
        raise AssertionError("weekly options must not be paired with a monthly future")


def test_stock_selection_requires_configured_top15_liquid_universe():
    items=[{"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"30SEP2026","token":"1","symbol":"ABCFUT","lotsize":"10"}]
    try:
        select_synthetic_contracts(_master(items), underlying="ABC", instrument_class="STOCK", atm_strike=100, expiry="30SEP2026")
    except ValueError as exc:
        assert "top-15 liquid" in str(exc)
    else:
        raise AssertionError("expected universe rejection")


def test_selection_keeps_only_matching_expiry_and_real_tokens():
    items=[
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"29OCT2026","token":"10","symbol":"ABCFUT","lotsize":"10"},
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"26NOV2026","token":"11","symbol":"ABCFUT2","lotsize":"10"},
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"OPTSTK","expiry":"29OCT2026","token":"20","symbol":"ABC100CE","strike":"10000","lotsize":"10"},
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"OPTSTK","expiry":"29OCT2026","token":"21","symbol":"ABC100PE","strike":"10000","lotsize":"10"},
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"OPTSTK","expiry":"26NOV2026","token":"30","symbol":"ABC100CE","strike":"10000","lotsize":"10"},
        {"exch_seg":"NFO","name":"ABC","instrumenttype":"OPTSTK","expiry":"26NOV2026","token":"31","symbol":"ABC100PE","strike":"10000","lotsize":"10"},
    ]
    selected=select_synthetic_contracts(
        _master(items), underlying="ABC", instrument_class="STOCK", atm_strike=100,
        expiry="29OCT2026", allowed_stock_symbols=frozenset({"ABC"})
    )
    assert selected.expiry=="29OCT2026"
    assert {x.token for x in selected.subscriptions}=={"10","11","20","21"}
