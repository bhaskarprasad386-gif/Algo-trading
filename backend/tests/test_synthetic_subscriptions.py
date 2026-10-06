from app.market_data.instruments import InstrumentMaster
from app.market_data.synthetic_subscriptions import select_synthetic_contracts


def _master(items):
    master = InstrumentMaster()
    master.instruments = items
    master._loaded = True
    return master


def test_index_selection_uses_actual_strikes_with_locked_plus_minus_10():
    items = [
        {"exch_seg":"NFO","name":"NIFTY","instrumenttype":"FUTIDX","expiry":"29OCT2026","token":"900","symbol":"NIFTY29OCT26FUT","lotsize":"65"},
        *[
            {"exch_seg":"NFO","name":"NIFTY","instrumenttype":"OPTIDX","expiry":"29OCT2026","token":str(i),"symbol":f"NIFTY{i}CE","strike":str(2400000+i*5000),"lotsize":"65"}
            for i in range(1, 32)
        ],
    ]
    selected = select_synthetic_contracts(
        _master(items), underlying="NIFTY", instrument_class="INDEX", atm_strike=24150.0,
        expiry="29OCT2026",
    )
    strikes = {x.strike for x in selected.subscriptions if x.strike is not None}
    assert strikes
    assert max(abs(s-24150.0) for s in strikes) <= 10 * 50


def test_stock_selection_requires_configured_nifty50_universe():
    items=[{"exch_seg":"NFO","name":"ABC","instrumenttype":"FUTSTK","expiry":"30SEP2026","token":"1","symbol":"ABCFUT","lotsize":"10"}]
    try:
        select_synthetic_contracts(_master(items), underlying="ABC", instrument_class="STOCK", atm_strike=100, expiry="30SEP2026")
    except ValueError as exc:
        assert "NIFTY-50" in str(exc)
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
    assert {x.token for x in selected.subscriptions}=={"10","11","20","21","30","31"}
