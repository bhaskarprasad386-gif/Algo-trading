import time
from types import SimpleNamespace
from app.scanner import live_box_spread_routes as routes

def test_live_box_route_serializes_bid_ask_snapshot(monkeypatch):
    low=SimpleNamespace(underlying="ABC",instrument_class="STOCK",expiry=20261231,strike=100,timestamp_ns=time.time_ns(),
        call_bid=10,call_ask=11,put_bid=1,put_ask=2,lot_size=10,volume=25)
    high=SimpleNamespace(strike=110,call_bid=8,call_ask=9,put_bid=2,put_ask=3,volume=20)
    result=SimpleNamespace(low=low,high=high,direction="LONG",executable_edge=6,edge_per_lot=60,gross_pnl=60,strike_distance=1)
    monkeypatch.setattr(routes,"_latest",lambda:(result,))
    payload=routes.live(10)
    row=payload["data"][0]
    assert row["low_call_bid"]==10 and row["low_call_ask"]==11
    assert row["high_put_bid"]==2 and row["high_put_ask"]==3
    assert row["low_call_price"]==11 and row["low_put_price"]==2
    assert row["high_call_price"]==8 and row["high_put_price"]==2
    assert row["liquidity_qty"]==20


def test_live_box_route_preserves_bse_index_identity(monkeypatch):
    low=SimpleNamespace(underlying="SENSEX",instrument_class="INDEX",expiry=20260930,strike=80000,timestamp_ns=time.time_ns(),
        call_bid=100,call_ask=101,put_bid=110,put_ask=111,lot_size=20,volume=50)
    high=SimpleNamespace(strike=80100,call_bid=90,call_ask=91,put_bid=120,put_ask=121,volume=40)
    result=SimpleNamespace(low=low,high=high,direction="LONG",executable_edge=10,edge_per_lot=200,gross_pnl=200,strike_distance=1)
    monkeypatch.setattr(routes,"_latest",lambda:(result,))
    row=routes.live(10)["data"][0]
    assert row["symbol"]=="SENSEX"
    assert row["instrument_class"]=="INDEX"
    assert row["low_call_bid"]==100 and row["low_call_ask"]==101
    assert row["high_put_bid"]==120 and row["high_put_ask"]==121
