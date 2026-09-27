from types import SimpleNamespace
from app.scanner import live_box_spread_routes as routes

def test_live_box_route_serializes_bid_ask_snapshot(monkeypatch):
    low=SimpleNamespace(underlying="ABC",instrument_class="STOCK",expiry=20261231,strike=100,timestamp_ns=1,
        call_bid=10,call_ask=11,put_bid=1,put_ask=2,lot_size=10)
    high=SimpleNamespace(strike=110,call_bid=8,call_ask=9,put_bid=2,put_ask=3)
    result=SimpleNamespace(low=low,high=high,direction="LONG",executable_edge=6,edge_per_lot=60,gross_pnl=60,strike_distance=1)
    monkeypatch.setattr(routes,"_latest",lambda:(result,))
    payload=routes.live(10)
    row=payload["data"][0]
    assert row["low_call_bid"]==10 and row["low_call_ask"]==11
    assert row["high_put_bid"]==2 and row["high_put_ask"]==3
