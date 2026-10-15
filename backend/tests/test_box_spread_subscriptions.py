from app.backtesting.arbitrage_scan_policy import ScanPolicy
from app.market_data.box_spread_subscriptions import select_box_contracts


class FakeMaster:
    instruments = [
        {"exch_seg": "NFO", "name": "ABC", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
        {"exch_seg": "NFO", "name": "ABC", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "strike": "10000", "symbol": "ABC10000CE", "token": "1", "lotsize": "10"},
        {"exch_seg": "NFO", "name": "ABC", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "strike": "10100", "symbol": "ABC10100CE", "token": "2", "lotsize": "10"},
        {"exch_seg": "NFO", "name": "ABC", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "strike": "10000", "symbol": "ABC10000PE", "token": "3", "lotsize": "10"},
        {"exch_seg": "NFO", "name": "ABC", "instrumenttype": "OPTSTK", "expiry": "30SEP2026", "strike": "10100", "symbol": "ABC10100PE", "token": "4", "lotsize": "10"},
    ]


def test_box_subscription_accepts_non_nifty50_stock_when_explicitly_allowed():
    policy = ScanPolicy(stock_box_distances=(1,))
    selected = select_box_contracts(
        FakeMaster(),
        underlying="ABC",
        instrument_class="STOCK",
        atm_strike=100.0,
        expiry="30SEP2026",
        allowed_stock_symbols=frozenset({"ABC"}),
        policy=policy,
    )
    assert len(selected.subscriptions) == 4


class FakeBseIndexMaster:
    instruments = [
        {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "FUTIDX", "expiry": "30SEP2026"},
        {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "8000000", "symbol": "SENSEX80000CE", "token": "11", "lotsize": "20"},
        {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "8010000", "symbol": "SENSEX80100CE", "token": "12", "lotsize": "20"},
        {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "8000000", "symbol": "SENSEX80000PE", "token": "13", "lotsize": "20"},
        {"exch_seg": "BFO", "name": "SENSEX", "instrumenttype": "OPTIDX", "expiry": "30SEP2026", "strike": "8010000", "symbol": "SENSEX80100PE", "token": "14", "lotsize": "20"},
    ]


def test_box_subscription_uses_bfo_for_sensex_index():
    selected = select_box_contracts(
        FakeBseIndexMaster(),
        underlying="SENSEX",
        instrument_class="INDEX",
        atm_strike=80000.0,
        expiry="30SEP2026",
        policy=ScanPolicy(index_box_distances=(1,)),
    )
    assert selected.subscriptions
    assert all(item.exchange_type == 4 for item in selected.subscriptions)


class WideIndexMaster:
    instruments = [
        {"exch_seg":"NFO","name":"NIFTY","instrumenttype":"FUTIDX","expiry":"29OCT2026"},
    ] + [
        {"exch_seg":"NFO","name":"NIFTY","instrumenttype":"OPTIDX","expiry":"22OCT2026",
         "strike":str(strike * 100), "symbol":f"NIFTY{strike}{suffix}",
         "token":f"{strike}{suffix}", "lotsize":"75"}
        for strike in range(100, 161) for suffix in ("CE", "PE")
    ]


def test_box_index_subscribes_atm_plus_minus_15_listed_strikes_on_weekly_expiry():
    selected = select_box_contracts(
        WideIndexMaster(), underlying="NIFTY", instrument_class="INDEX",
        atm_strike=130.0, expiry="22OCT2026",
    )
    strikes = {item.strike for item in selected.subscriptions}
    assert strikes == set(float(x) for x in range(115, 146))
    assert len(selected.subscriptions) == 31 * 2
    assert {item.expiry for item in selected.subscriptions} == {"22OCT2026"}
