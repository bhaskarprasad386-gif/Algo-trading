from app.scanner import auto_routes
from app.scanner.cash_future import CashFutureConfig, CashQuote, FutureQuote, calculate_cash_future


class FakeMaster:
    def __init__(self):
        self.instruments = [
            {"name": "SBIN", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
            {"name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
            {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "30SEP2026"},
            {"name": "OLD", "exch_seg": "NFO", "instrumenttype": "FUTSTK", "expiry": "30SEP2025"},
            {"name": "ABC", "exch_seg": "NSE", "instrumenttype": "FUTSTK", "expiry": "30SEP2026"},
        ]

    def search(self, exchange=None):
        return self.instruments


def test_discover_cash_future_symbols(monkeypatch):
    monkeypatch.setattr(auto_routes, "InstrumentMaster", FakeMaster)
    result = auto_routes.discover_cash_future_symbols(limit=2)
    assert result == ["RELIANCE", "SBIN"]
    assert "NIFTY" not in result


def _quote(**overrides):
    values = {
        "symbol": "SBIN",
        "contract_month": "CURRENT",
        "ltp": 101.0,
        "lot_size": 10,
        "margin_required": 1000.0,
        "volume": 5000,
        "oi": 20000,
        "bid": 100.9,
        "ask": 101.1,
    }
    values.update(overrides)
    return FutureQuote(**values)


def test_cash_future_passes_requested_executable_filters():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(),
        CashFutureConfig(min_gap=0.5, min_gap_pct=0.5, min_net_profit=5.0, min_volume=1000, min_oi=10000),
    )
    assert result.executable is True
    assert result.rejection_reasons == ()
    assert result.net_profit == 9.0
    assert result.deployed_capital == 2000.0
    assert result.roi_pct == 0.45


def test_cash_future_rejects_gap_profit_volume_and_oi_filters():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(ltp=100.2, bid=100.2, volume=100, oi=1000),
        CashFutureConfig(min_gap=0.5, min_gap_pct=0.5, min_net_profit=5.0, min_volume=1000, min_oi=10000),
    )
    assert result.executable is False
    assert "gap_below_minimum" in result.rejection_reasons
    assert "gap_pct_below_minimum" in result.rejection_reasons
    assert "net_profit_below_minimum" in result.rejection_reasons
    assert "volume_below_minimum" in result.rejection_reasons
    assert "oi_below_minimum" in result.rejection_reasons


def test_cash_future_rejects_wide_future_bid_ask_spread():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(bid=99.0, ask=103.0),
        CashFutureConfig(max_bid_ask_spread_pct=2.0),
    )
    assert result.executable is False
    assert "bid_ask_spread_above_maximum" in result.rejection_reasons


def test_cash_future_rejects_insufficient_broker_margin():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(margin_required=900.0),
        CashFutureConfig(min_margin=1000.0),
    )
    assert result.executable is False
    assert "margin_below_minimum" in result.rejection_reasons


def test_cash_future_rejects_low_broker_margin_roi():
    result = calculate_cash_future(
        CashQuote(symbol="SBIN", ltp=100.0, bid=99.9, ask=100.0),
        _quote(margin_required=10000.0),
        CashFutureConfig(min_roi_pct=0.2),
    )
    assert result.executable is False
    assert "roi_below_minimum" in result.rejection_reasons


def test_cash_future_live_auto_api_reads_persisted_live_observation(monkeypatch):
    from datetime import datetime, date
    from app.models.cash_future_history import CashFutureHistory

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["SBIN"])
    db = type("DB", (), {})()
    row = CashFutureHistory(
        symbol="SBIN", contract_month="CURRENT", timestamp=datetime.now(), expiry_date=date.today(),
        cash_price=100.0, future_price=106.0, gap=6.0, gap_pct=6.0, lot_size=10,
        margin_required=1000.0, volume=5000, oi=20000, cash_bid=99.9, cash_ask=100.0,
        future_bid=106.0, future_ask=106.1,
    )
    db.scalars = lambda stmt: type("Result", (), {"all": lambda self: [row]})()
    response = auto_routes.cash_future_live_auto_scanner(limit=1, db=db)
    assert response["status"] == "success"
    assert response["opportunity_count"] == 1
    assert response["data"][0]["source"] == "stored-live-feed"
    assert response["data"][0]["symbol"] == "SBIN"


def test_cash_future_live_auto_api_sorts_persisted_rows_by_net_profit(monkeypatch):
    from datetime import datetime, date
    from app.models.cash_future_history import CashFutureHistory

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["SBIN", "TCS", "INFY"])
    db = type("DB", (), {})()
    now = datetime.now()
    rows = [
        CashFutureHistory(symbol="SBIN", contract_month="CURRENT", timestamp=now, expiry_date=date.today(), cash_price=100, future_price=102, gap=2, gap_pct=2, lot_size=10, margin_required=1000, volume=5000, oi=20000, cash_bid=99.9, cash_ask=100, future_bid=102, future_ask=102.1),
        CashFutureHistory(symbol="TCS", contract_month="CURRENT", timestamp=now, expiry_date=date.today(), cash_price=100, future_price=105, gap=5, gap_pct=5, lot_size=10, margin_required=1000, volume=5000, oi=20000, cash_bid=99.9, cash_ask=100, future_bid=105, future_ask=105.1),
        CashFutureHistory(symbol="INFY", contract_month="CURRENT", timestamp=now, expiry_date=date.today(), cash_price=100, future_price=105, gap=5, gap_pct=5, lot_size=10, margin_required=1000, volume=5000, oi=20000, cash_bid=99.9, cash_ask=100, future_bid=105, future_ask=105.1),
    ]
    db.scalars = lambda stmt: type("Result", (), {"all": lambda self: rows})()
    response = auto_routes.cash_future_live_auto_scanner(limit=3, db=db)
    assert [item["symbol"] for item in response["data"]] == ["INFY", "TCS", "SBIN"]


def test_cash_future_live_auto_api_applies_requested_filters_to_stored_feed(monkeypatch):
    from datetime import datetime, date
    from app.models.cash_future_history import CashFutureHistory

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["SBIN"])
    db = type("DB", (), {})()
    row = CashFutureHistory(
        symbol="SBIN", contract_month="CURRENT", timestamp=datetime.now(), expiry_date=date.today(),
        cash_price=100.0, future_price=100.2, gap=0.2, gap_pct=0.2, lot_size=10,
        margin_required=1000.0, volume=100, oi=1000, cash_bid=99.9, cash_ask=100.0,
        future_bid=100.2, future_ask=100.3,
    )
    db.scalars = lambda stmt: type("Result", (), {"all": lambda self: [row]})()
    response = auto_routes.cash_future_live_auto_scanner(
        limit=1, min_gap=1.25, min_gap_pct=0.75, min_net_profit=15.0,
        min_roi_pct=0.6, min_volume=2000, min_oi=30000, db=db,
    )
    assert response["status"] == "success"
    assert response["opportunity_count"] == 0
