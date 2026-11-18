from datetime import date

from app.backtesting import monthly_results_routes as routes


class _DummyDB:
    pass


def test_monthly_top10_preserves_gap_high_financial_fields(monkeypatch) -> None:
    trading_day = date(2026, 1, 15)
    rows = [
        {
            "trading_date": trading_day,
            "symbol": "TEST",
            "instrument_type": "STOCK",
            "contract_month": "202601",
            "lot_size": 500,
        }
    ]
    payload = {
        "trading_date": trading_day,
        "symbol": "TEST",
        "gap": 12.5,
        "weighted_gap": 6250.0,
        "gap_high_timestamp": "2026-01-15T11:23:00",
        "cash_price_at_gap_high": 100.0,
        "future_price_at_gap_high": 112.5,
        "lot_size": 500,
        "contract_month": "202601",
        "instrument_key": "NFO:TEST-FUT",
        "expiry_date": date(2026, 1, 29),
        "is_expiry_day": False,
        "margin_required": 125000.0,
        "charges": 37.5,
        "funding_cost": 12.25,
        "net_profit": 6200.25,
        "roi_pct": 4.9602,
    }

    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        routes,
        "_cash_future_shorting_payloads",
        lambda *args, **kwargs: [payload],
    )

    result = routes.monthly_gap_top10(2026, 1, "STOCK", None, _DummyDB())
    item = result["data"][0]

    assert item["symbol"] == "TEST"
    assert item["gap_high_timestamp"] == "2026-01-15T11:23:00"
    assert item["cash_price_at_gap_high"] == 100.0
    assert item["future_price_at_gap_high"] == 112.5
    assert item["margin_required"] == 125000.0
    assert item["charges"] == 37.5
    assert item["funding_cost"] == 12.25
    assert item["net_profit"] == 6200.25
    assert item["roi_pct"] == 4.9602
