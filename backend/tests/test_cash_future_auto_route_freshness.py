import inspect

from app.scanner import auto_routes


def test_cash_future_auto_route_passes_quote_freshness_controls(monkeypatch):
    captured = {}

    monkeypatch.setattr(auto_routes, "discover_cash_future_symbols", lambda limit: ["ABC"])

    class FakeCollector:
        def __init__(self, symbols, config=None, max_quote_age_seconds=None, max_quote_timestamp_skew_seconds=None):
            captured["symbols"] = symbols
            captured["max_quote_age_seconds"] = max_quote_age_seconds
            captured["max_quote_timestamp_skew_seconds"] = max_quote_timestamp_skew_seconds

        def collect(self, db):
            return {
                "collected": [{"symbol": "ABC", "executable": True, "net_profit": 10.0, "roi_pct": 1.0}],
                "errors": [],
            }

    monkeypatch.setattr(auto_routes, "CashFutureHistoryCollector", FakeCollector)

    result = auto_routes.cash_future_live_auto_scanner(
        limit=1,
        max_quote_age_seconds=12.5,
        max_quote_timestamp_skew_seconds=4.5,
        db=object(),
    )

    assert result["opportunity_count"] == 1
    assert captured == {
        "symbols": ["ABC"],
        "max_quote_age_seconds": 12.5,
        "max_quote_timestamp_skew_seconds": 4.5,
    }
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
