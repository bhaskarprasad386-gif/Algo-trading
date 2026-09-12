from datetime import date

from app.backtesting import monthly_results_routes as routes


def test_monthly_gap_top10_keeps_one_month_high_per_stock(monkeypatch):
    rows = [
        {"trading_date": date(2026, 8, 3), "symbol": "AAA"},
        {"trading_date": date(2026, 8, 4), "symbol": "AAA"},
        {"trading_date": date(2026, 8, 3), "symbol": "BBB"},
        {"trading_date": date(2026, 8, 4), "symbol": "BBB"},
    ]
    payloads = {
        (date(2026, 8, 3), "AAA"): {
            "symbol": "AAA", "weighted_gap": 5000.0, "gap": 10.0,
            "gap_high_timestamp": "2026-08-03T10:15:00+05:30",
            "lot_size": 500, "trading_date": date(2026, 8, 3),
            "cash_price_at_gap_high": 1000.0, "future_price_at_gap_high": 1010.0,
            "contract_month": "202608", "instrument_key": "NFO:AAA",
        },
        (date(2026, 8, 4), "AAA"): {
            "symbol": "AAA", "weighted_gap": 9000.0, "gap": 18.0,
            "gap_high_timestamp": "2026-08-04T11:30:00+05:30",
            "lot_size": 500, "trading_date": date(2026, 8, 4),
            "cash_price_at_gap_high": 1002.0, "future_price_at_gap_high": 1020.0,
            "contract_month": "202608", "instrument_key": "NFO:AAA",
        },
        (date(2026, 8, 3), "BBB"): {
            "symbol": "BBB", "weighted_gap": 7000.0, "gap": 14.0,
            "gap_high_timestamp": "2026-08-03T12:00:00+05:30",
            "lot_size": 500, "trading_date": date(2026, 8, 3),
            "cash_price_at_gap_high": 2000.0, "future_price_at_gap_high": 2014.0,
            "contract_month": "202608", "instrument_key": "NFO:BBB",
        },
        (date(2026, 8, 4), "BBB"): {
            "symbol": "BBB", "weighted_gap": 6000.0, "gap": 12.0,
            "gap_high_timestamp": "2026-08-04T13:00:00+05:30",
            "lot_size": 500, "trading_date": date(2026, 8, 4),
            "cash_price_at_gap_high": 2001.0, "future_price_at_gap_high": 2013.0,
            "contract_month": "202608", "instrument_key": "NFO:BBB",
        },
    }

    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        routes,
        "_cash_future_shorting_payloads",
        lambda trading_day, symbols, **kwargs: [
            payloads[(trading_day, symbol)] for symbol in symbols
        ],
    )

    response = routes.monthly_gap_top10(2026, 8, "STOCK", None, object())

    assert response["count"] == 2
    assert [item["symbol"] for item in response["data"]] == ["AAA", "BBB"]
    assert response["data"][0]["rank"] == 1
    assert response["data"][0]["month_gap_high"] == 18.0
    assert response["data"][0]["gap_value"] == 9000.0
    assert response["data"][0]["gap_high_date"] == date(2026, 8, 4)
    assert response["data"][0]["gap_high_time"] == "11:30:00+05:30"
    assert response["data"][0]["cash_price_at_gap_high"] == 1002.0
    assert response["data"][0]["future_price_at_gap_high"] == 1020.0


def test_monthly_gap_top10_caps_result_at_ten_stocks(monkeypatch):
    trading_day = date(2026, 8, 3)
    rows = [{"trading_date": trading_day, "symbol": f"S{i:02d}"} for i in range(12)]
    payloads = [
        {
            "symbol": f"S{i:02d}",
            "weighted_gap": float(12000 - i * 500),
            "gap": float(24 - i),
            "gap_high_timestamp": f"2026-08-03T09:{15 + i:02d}:00+05:30",
            "lot_size": 500,
            "trading_date": trading_day,
            "cash_price_at_gap_high": 1000.0,
            "future_price_at_gap_high": 1024.0 - i,
            "contract_month": "202608",
            "instrument_key": f"NFO:S{i:02d}",
        }
        for i in range(12)
    ]

    monkeypatch.setattr(routes, "_daily_rows", lambda *args, **kwargs: rows)
    monkeypatch.setattr(routes, "_cash_future_shorting_payloads", lambda *args, **kwargs: payloads)

    response = routes.monthly_gap_top10(2026, 8, "STOCK", None, object())

    assert response["count"] == 10
    assert [item["rank"] for item in response["data"]] == list(range(1, 11))
    assert response["data"][0]["symbol"] == "S00"
    assert response["data"][-1]["symbol"] == "S09"
