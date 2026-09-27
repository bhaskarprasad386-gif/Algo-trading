from app.execution.box_spread_paper_routes import _entry_cashflow, _exit_cashflow


class P:
    direction = "LONG"


def _entry(direction):
    return type("E", (), {
        "direction": direction,
        "low_call_price": 11.0, "low_put_price": 2.0,
        "high_call_price": 9.0, "high_put_price": 3.0,
    })()


def _exit(low_call, low_put, high_call, high_put):
    return type("X", (), {
        "low_call_price": low_call, "low_put_price": low_put,
        "high_call_price": high_call, "high_put_price": high_put,
    })()


def test_box_spread_paper_uses_executable_entry_cashflows():
    assert _entry_cashflow(_entry("LONG")) == -1.0
    assert _entry_cashflow(_entry("SHORT")) == 1.0


def test_box_spread_paper_long_close_cashflow_and_pnl_sign():
    entry = _entry("LONG")
    position = type("P", (), {"direction": "LONG"})()
    close = _exit(12, 1, 10, 2)
    assert _entry_cashflow(entry) + _exit_cashflow(position, close) == 1.0


def test_box_spread_paper_short_close_cashflow_and_pnl_sign():
    entry = _entry("SHORT")
    position = type("P", (), {"direction": "SHORT"})()
    close = _exit(12, 1, 10, 2)
    assert _entry_cashflow(entry) + _exit_cashflow(position, close) == -1.0


def test_box_spread_bse_index_cashflow_uses_scanner_executable_prices():
    req = _entry("LONG")
    req.low_call_price = 101.0
    req.low_put_price = 111.0
    req.high_call_price = 90.0
    req.high_put_price = 120.0
    req.underlying = "SENSEX"
    req.instrument_class = "INDEX"
    assert _entry_cashflow(req) == -2.0


def test_box_spread_paper_mark_to_market_uses_reverse_executable_prices():
    entry = _entry("LONG")
    position = type("P", (), {
        "direction": "LONG", "lot_size": 10, "lots": 1,
        "high_call_entry": entry.high_call_price, "high_put_entry": entry.high_put_price,
        "low_call_entry": entry.low_call_price, "low_put_entry": entry.low_put_price,
    })()
    # Closing a LONG buys high strikes at ask and sells low strikes at bid.
    close = _exit(12, 1, 10, 2)
    assert _exit_cashflow(position, close) == 1.0
    assert (_entry_cashflow(entry) + _exit_cashflow(position, close)) * 10 == 10.0



def test_box_spread_exit_from_scanner_maps_live_bid_ask(monkeypatch):
    from types import SimpleNamespace
    from app.execution import box_spread_paper_routes as routes

    position = SimpleNamespace(underlying="SENSEX", instrument_class="INDEX", expiry="20260930", low_strike=80000.0, high_strike=80100.0, direction="LONG")
    low = SimpleNamespace(underlying="SENSEX", instrument_class="INDEX", expiry="20260930", strike=80000.0, call_bid=100.0, call_ask=101.0, put_bid=110.0, put_ask=111.0)
    high = SimpleNamespace(strike=80100.0, call_bid=90.0, call_ask=91.0, put_bid=120.0, put_ask=121.0)
    match = SimpleNamespace(low=low, high=high)
    class Query:
        def filter_by(self, **kwargs): return self
        def first(self): return position
    class DB:
        def query(self, model): return Query()
    monkeypatch.setattr(routes.scanner if hasattr(routes, "scanner") else __import__("app.scanner.live_box_spread_routes", fromlist=["x"]), "_latest", lambda: (match,), raising=False)
    captured = {}
    monkeypatch.setattr(routes, "exit", lambda req, user, db: captured.setdefault("req", req) or {"status": "success"})
    routes.exit_from_scanner(user=1, db=DB())
    assert captured["req"].low_call_price == 100.0
    assert captured["req"].low_put_price == 110.0
    assert captured["req"].high_call_price == 91.0
    assert captured["req"].high_put_price == 121.0


def test_box_spread_scanner_entry_maps_executable_bid_ask():
    from app.execution.box_spread_paper_routes import _scanner_entry_request
    from types import SimpleNamespace
    low=SimpleNamespace(underlying="SENSEX",instrument_class="INDEX",expiry=20260930,strike=80000,lot_size=20,call_bid=100,call_ask=101,put_bid=110,put_ask=111,volume=50)
    high=SimpleNamespace(strike=80100,call_bid=90,call_ask=91,put_bid=120,put_ask=121,volume=40)
    match=SimpleNamespace(low=low,high=high,direction="LONG",executable_edge=10,edge_per_lot=200)
    req=_scanner_entry_request(match,1)
    assert req.low_call_price==101 and req.low_put_price==111
    assert req.high_call_price==90 and req.high_put_price==120
    assert req.liquidity_qty==40 and req.expiry=="20260930"


def test_box_spread_auto_exit_uses_current_executable_quotes():
    from app.execution.box_spread_paper_routes import _scanner_exit_request, _exit_cashflow
    from types import SimpleNamespace
    p=SimpleNamespace(direction="LONG")
    low=SimpleNamespace(call_bid=100,call_ask=101,put_bid=110,put_ask=111)
    high=SimpleNamespace(call_bid=90,call_ask=91,put_bid=120,put_ask=121)
    req=_scanner_exit_request(p,SimpleNamespace(low=low,high=high))
    assert req.low_call_price==100 and req.low_put_price==110
    assert req.high_call_price==91 and req.high_put_price==121
    assert _exit_cashflow(p,req)==-2


def test_box_spread_entry_rejects_expired_contract_and_excess_quantity():
    from app.execution.box_spread_paper_routes import Entry, _validate
    from fastapi import HTTPException
    base=dict(underlying="SENSEX",instrument_class="INDEX",low_strike=80000,high_strike=80100,direction="LONG",lot_size=20,lots=1,low_call_price=101,low_put_price=111,high_call_price=90,high_put_price=120)
    try:
        _validate(Entry(expiry="20200101",**base))
        assert False
    except HTTPException as exc:
        assert exc.status_code==422 and "expiry" in exc.detail
    try:
        _validate(Entry(expiry="20990101",lot_size=1,lots=5001,**{k:v for k,v in base.items() if k!="lot_size" and k!="lots"}))
        assert False
    except HTTPException as exc:
        assert exc.status_code==422 and "risk limit" in exc.detail


def test_box_spread_cycle_holds_open_position_below_target(monkeypatch):
    from app.execution import box_spread_paper_routes as routes
    from types import SimpleNamespace
    position=SimpleNamespace(id=7,lot_size=20,lots=1,direction="LONG",high_call_entry=90,high_put_entry=120,low_call_entry=101,low_put_entry=111,underlying="SENSEX",instrument_class="INDEX",expiry="20990101",low_strike=80000.0,high_strike=80100.0)
    class Query:
        def filter_by(self, **kwargs): return self
        def first(self): return position
    class DB:
        def query(self, model): return Query()
    low=SimpleNamespace(underlying="SENSEX",instrument_class="INDEX",expiry="20990101",strike=80000.0,call_bid=100,call_ask=101,put_bid=110,put_ask=111)
    high=SimpleNamespace(strike=80100.0,call_bid=90,call_ask=91,put_bid=120,put_ask=121)
    monkeypatch.setattr(routes,"_current_scanner_match",lambda p: SimpleNamespace(low=low,high=high))
    result=routes.cycle(lots=1,min_pnl=0,user=1,db=DB())
    assert result["status"]=="hold" and result["position_id"]==7


def test_box_spread_auto_cycle_settings_update_and_limit(monkeypatch):
    from app.execution import box_spread_paper_routes as routes
    from types import SimpleNamespace
    from fastapi import HTTPException

    account = SimpleNamespace(is_active=True, mode="PAPER", box_spread_auto_lots=1)
    class DB:
        def query(self, model):
            class Query:
                def filter(self, *args): return self
                def first(self): return account
            return Query()
        def commit(self): pass

    result = routes.update_auto_cycle_settings(routes.AutoCycleSettings(lots=3), user=1, db=DB())
    assert result["lots"] == 3
    assert account.box_spread_auto_lots == 3

    try:
        routes.update_auto_cycle_settings(routes.AutoCycleSettings(lots=5001), user=1, db=DB())
        assert False
    except HTTPException as exc:
        assert exc.status_code == 422


def test_box_spread_journal_is_user_scoped_and_limited():
    from app.execution import box_spread_paper_routes as routes
    from types import SimpleNamespace

    rows=[
        SimpleNamespace(id=2,created_at="t2",message="user=7 status=exit",details="pnl"),
        SimpleNamespace(id=1,created_at="t1",message="user=7 status=hold",details=""),
    ]
    class Query:
        def filter(self,*args): return self
        def order_by(self,*args): return self
        def limit(self,n): return self
        def all(self): return rows
    class DB:
        def query(self,model): return Query()
    result=routes.journal(limit=2,user=7,db=DB())
    assert result["count"]==2
    assert result["items"][0]["id"]==2


def test_box_spread_journal_rejects_invalid_limit():
    from app.execution import box_spread_paper_routes as routes
    from fastapi import HTTPException
    try:
        routes.journal(limit=201,user=7,db=None)
        assert False
    except HTTPException as exc:
        assert exc.status_code==422


def test_box_spread_history_is_user_scoped_and_returns_lifecycle_fields():
    from app.execution import box_spread_paper_routes as routes
    from types import SimpleNamespace
    p=SimpleNamespace(id=9,underlying="SENSEX",instrument_class="INDEX",expiry="20990101",
        low_strike=80000.0,high_strike=80100.0,direction="LONG",lot_size=20,lots=2,
        low_call_entry=101.0,low_put_entry=111.0,high_call_entry=90.0,high_put_entry=120.0,
        realized_pnl=760.0,is_open=0,created_at="t1",closed_at="t2")
    class Query:
        def filter(self,*args): return self
        def order_by(self,*args): return self
        def limit(self,n): return self
        def all(self): return [p]
    class DB:
        def query(self,model): return Query()
    result=routes.history(limit=10,user=7,db=DB())
    item=result["items"][0]
    assert item["quantity"]==40
    assert item["realized_pnl"]==760.0
    assert item["is_open"] is False
    assert item["created_at"]=="t1" and item["closed_at"]=="t2"


def test_box_spread_journal_returns_structured_lifecycle_event():
    from app.execution import box_spread_paper_routes as routes
    from types import SimpleNamespace
    import json
    row=SimpleNamespace(id=3,created_at="t3",message="user=7 event=EXIT",
        details=json.dumps({"event":"EXIT","user_id":7,"position_id":9,"gross_pnl":125.5,"lots":2}))
    class Query:
        def filter(self,*args): return self
        def order_by(self,*args): return self
        def limit(self,n): return self
        def all(self): return [row]
    class DB:
        def query(self,model): return Query()
    item=routes.journal(limit=10,user=7,db=DB())["items"][0]
    assert item["event"]=="EXIT"
    assert item["position_id"]==9
    assert item["gross_pnl"]==125.5
