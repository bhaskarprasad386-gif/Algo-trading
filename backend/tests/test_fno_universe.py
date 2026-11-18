from app.models import Instrument
from app.scanner.fno_universe import _is_stock_future, universe_coverage


def test_full_fno_universe_excludes_index_derivatives():
    stock = Instrument(token="1", symbol="RELIANCE", exchange="NFO", instrument_type="FUTSTK")
    index = Instrument(token="2", symbol="NIFTY", exchange="NFO", instrument_type="FUTIDX")
    other = Instrument(token="3", symbol="RELIANCE", exchange="NSE", instrument_type="EQ")

    assert _is_stock_future(stock) is True
    assert _is_stock_future(index) is False
    assert _is_stock_future(other) is False


def test_universe_coverage_is_auditable():
    stock = Instrument(token="1", symbol="TCS", exchange="NFO", instrument_type="FUTSTK")
    from app.scanner.fno_universe import FnoStockInstrument

    universe = [
        FnoStockInstrument(symbol=stock.symbol, token=stock.token, exchange=stock.exchange, instrument_type=stock.instrument_type)
    ]
    report = universe_coverage(universe)
    assert report["universe"] == "FULL_FNO_STOCK"
    assert report["symbols_total"] == 1
    assert report["index_derivatives_excluded"] is True
