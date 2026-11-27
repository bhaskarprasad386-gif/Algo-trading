from app.market_data.nifty50_universe import TOP15_LIQUID_STOCK_SYMBOLS


def test_top15_liquid_universe_has_exactly_15_symbols():
    assert len(TOP15_LIQUID_STOCK_SYMBOLS) == 15
