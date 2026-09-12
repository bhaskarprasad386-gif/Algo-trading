from datetime import date

from app.backtesting import monthly_results_routes as routes


class _Mappings:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def all(self) -> list[dict]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class _Db:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, *_args, **_kwargs) -> _Result:
        return _Result(self._rows)


def test_monthly_top10_keeps_one_row_per_stock_and_uses_monthly_max(monkeypatch) -> None:
    rows = [
        {"trading_date": date(2026, 1, 5), "symbol": "AAA", "lot_size": 100},
        {"trading_date": date(2026, 1, 6), "symbol": "AAA", "lot_size": 100},
        {"trading_date": date(2026, 1, 7), "symbol": "BBB", "lot_size": 50},
    ]
    payloads = {
        date(2026, 1, 5): [
            {"trading_date": date(2026, 1, 5), "symbol": "AAA", "gap": 8.0, "weighted_gap": 800.0,
             "lot_size": 100, "gap_high_timestamp": "2026-01-05T10:00:00+05:30",
             "cash_price_at_gap_high": 100.0, "future_price_at_gap_high": 108.0,
             "contract_month": "202601", "instrument_key": "NFO:AAA-1"},
        ],
        date(2026, 1, 6): [
            {"trading_date": date(2026, 1, 6), "symbol": "AAA", "gap": 12.0, "weighted_gap": 1200.0,
             "lot_size": 100, "gap_high_timestamp": "2026-01-06T11:00:00+05:30",
             "cash_price_at_gap_high": 100.0, "future_price_at_gap_high": 112.0,
             "contract_month": "202601", "instrument_key": "NFO:AAA-1"},
        ],
        date(2026, 1, 7): [
            {"trading_date": date(2026, 1, 7), "symbol": "BBB", "gap": 20.0, "weighted_gap": 1000.0,
             "lot_size": 50, "gap_high_timestamp": "2026-01-07T12:00:00+05:30",
             "cash_price_at_gap_high": 200.0, "future_price_at_gap_high": 220.0,
             "contract_month": "202601", "instrument_key": "NFO:BBB-1"},
        ],
    }

    monkeypatch.setattr(
        routes,
        "_cash_future_shorting_payloads",
        lambda trading_date, symbols, **kwargs: payloads[trading_date],
    )

    response = routes.monthly_gap_top10(
        year=2026,
        month=1,
        instrument_type="STOCK",
        contract_month=None,
        db=_Db(rows),
    )

    assert response["count"] == 2
    assert [item["symbol"] for item in response["data"]] == ["AAA", "BBB"]
    assert response["data"][0]["month_gap_high"] == 1200.0
    assert response["data"][0]["gap_high_date"] == date(2026, 1, 6)
    assert response["data"][0]["gap_high_timestamp"] == "2026-01-06T11:00:00+05:30"
    assert response["data"][1]["month_gap_high"] == 1000.0
    assert len({item["symbol"] for item in response["data"]}) == 2
    assert response["data"][0]["rank"] == 1
    assert response["data"][1]["rank"] == 2
