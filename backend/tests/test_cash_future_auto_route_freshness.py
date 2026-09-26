import inspect
from datetime import date, datetime

from app.models.cash_future_history import CashFutureHistory
from app.scanner import auto_routes


def test_cash_future_auto_route_uses_persisted_feed_and_exposes_freshness_controls(monkeypatch):
    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["ABC"])
    db = type("DB", (), {})()
    row = CashFutureHistory(
        symbol="ABC",
        contract_month="CURRENT",
        timestamp=datetime.now(),
        expiry_date=date.today(),
        cash_price=100.0,
        future_price=106.0,
        gap=6.0,
        gap_pct=6.0,
        lot_size=10,
        margin_required=1000.0,
        volume=5000,
        oi=20000,
        cash_bid=99.9,
        cash_ask=100.0,
        future_bid=106.0,
        future_ask=106.1,
    )
    db.scalars = lambda stmt: type("Result", (), {"all": lambda self: [row]})()

    result = auto_routes.cash_future_live_auto_scanner(
        limit=1,
        max_quote_age_seconds=12.5,
        max_quote_timestamp_skew_seconds=4.5,
        db=db,
    )

    assert result["opportunity_count"] == 1
    assert result["data"][0]["source"] == "stored-live-feed"
    assert result["filters"]["max_quote_age_seconds"] == 12.5
    assert result["filters"]["max_quote_timestamp_skew_seconds"] == 4.5


def test_cash_future_auto_route_freshness_query_constraints_are_positive():
    parameters = inspect.signature(auto_routes.cash_future_live_auto_scanner).parameters
    age_query = parameters["max_quote_age_seconds"].default
    skew_query = parameters["max_quote_timestamp_skew_seconds"].default
    assert age_query.default == 15.0
    assert skew_query.default == 5.0
    assert any(getattr(metadata, "gt", None) == 0 for metadata in age_query.metadata)
    assert any(getattr(metadata, "gt", None) == 0 for metadata in skew_query.metadata)
