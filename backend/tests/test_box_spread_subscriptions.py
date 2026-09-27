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
