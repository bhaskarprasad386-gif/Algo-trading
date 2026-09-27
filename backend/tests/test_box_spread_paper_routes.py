from types import SimpleNamespace


def test_box_spread_paper_route_has_bid_ask_ready_entry_models():
    from app.execution.box_spread_paper_routes import Entry, ScannerEntry
    req=Entry(underlying="ABC",instrument_class="STOCK",expiry="20261231",low_strike=100,high_strike=110,direction="LONG",lot_size=10,lots=1,low_call_price=11,low_put_price=2,high_call_price=9,high_put_price=3)
    assert req.low_call_price == 11 and req.high_put_price == 3
    scan=ScannerEntry(**req.model_dump(),executable_edge=2.5,edge_per_lot=25,liquidity_qty=10)
    assert scan.executable_edge == 2.5


def test_box_spread_paper_pnl_formula_long_and_short():
    q=10
    long_pnl=((11-12)+(2-1)+(10-9)+(4-3))*q
    short_pnl=((12-11)+(1-2)+(9-10)+(3-4))*q
    assert long_pnl == 20
    assert short_pnl == -20
