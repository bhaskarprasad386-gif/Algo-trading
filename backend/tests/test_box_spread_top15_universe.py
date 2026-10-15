from app.market_data.nifty50_universe import NIFTY50_STOCK_SYMBOLS, TOP15_LIQUID_STOCK_SYMBOLS


def test_box_spread_stock_universe_is_explicit_top_15_not_all_nifty50():
    assert len(TOP15_LIQUID_STOCK_SYMBOLS) == 15
    assert TOP15_LIQUID_STOCK_SYMBOLS <= NIFTY50_STOCK_SYMBOLS
    assert TOP15_LIQUID_STOCK_SYMBOLS == frozenset({
        "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY",
        "TCS", "AXISBANK", "LT", "BHARTIARTL", "KOTAKBANK",
        "TMPV", "BAJFINANCE", "M&M", "HINDALCO", "TATASTEEL",
    })
