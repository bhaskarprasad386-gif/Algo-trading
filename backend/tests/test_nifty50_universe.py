from app.market_data.nifty50_universe import NIFTY50_INDEX_SYMBOLS, NIFTY50_STOCK_SYMBOLS


def test_box_spread_universe_is_nifty50_only():
    assert len(NIFTY50_STOCK_SYMBOLS) == 50
    assert NIFTY50_INDEX_SYMBOLS == {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    assert "RELIANCE" in NIFTY50_STOCK_SYMBOLS
    assert "INDIGO" in NIFTY50_STOCK_SYMBOLS
    assert "HEROMOTOCO" not in NIFTY50_STOCK_SYMBOLS
    assert "ABC" not in NIFTY50_STOCK_SYMBOLS
