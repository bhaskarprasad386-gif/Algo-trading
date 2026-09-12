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


def test_monthly_top10_retains_expiry_day_winner_and_metadata(monkeypatch) -> None:
    trading_day = date(2026, 1, 29)
    rows = [
        {"trading_date": trading_day, "symbol": "EXP", "lot_size": 100},
        {"trading_date": trading_day, "symbol": "NORMAL", "lot_size": 100},
    ]
    payloads = [
        {
            "trading_date": trading_day,
            "symbol": "EXP",
            "gap": 15.0,
            "weighted_gap": 1500.0,
            "lot_size": 100,
            "gap_high_timestamp": "2026-01-29T12:00:00+05:30",
            "cash_price_at_gap_high": 100.0,
            "future_price_at_gap_high": 115.0,
            "contract_month": "202601",
            "instrument_key": "NFO:EXP-1",
            "expiry_date": trading_day,
            "is_expiry_day": True,
            "margin_required": 125000.0,
            "charges": 37.5,
            "funding_cost": 12.25,
            "net_profit": 1450.25,
            "roi_pct": 1.16,
        },
        {
            "trading_date": trading_day,
            "symbol": "NORMAL",
            "gap": 10.0,
            "weighted_gap": 1000.0,
            "lot_size": 100,
            "gap_high_timestamp": "2026-01-29T11:00:00+05:30",
            "cash_price_at_gap_high": 200.0,
            "future_price_at_gap_high": 210.0,
            "contract_month": "202601",
            "instrument_key": "NFO:NORMAL-1",
            "expiry_date": date(2026, 2, 26),
            "is_expiry_day": False,
        },
    ]

    monkeypatch.setattr(
        routes,
        "_cash_future_shorting_payloads",
        lambda trading_date, symbols, **kwargs: payloads,
    )

    response = routes.monthly_gap_top10(
        year=2026,
        month=1,
        instrument_type="STOCK",
        contract_month=None,
        db=_Db(rows),
    )

    assert response["count"] == 2
    expiry = response["data"][0]
    assert expiry["symbol"] == "EXP"
    assert expiry["rank"] == 1
    assert expiry["is_expiry_day"] is True
    assert expiry["expiry_date"] == trading_day
    assert expiry["gap_high_timestamp"] == "2026-01-29T12:00:00+05:30"
    assert expiry["margin_required"] == 125000.0
    assert expiry["charges"] == 37.5
    assert expiry["funding_cost"] == 12.25
    assert expiry["net_profit"] == 1450.25
    assert expiry["roi_pct"] == 1.16

    normal = response["data"][1]
    assert normal["symbol"] == "NORMAL"
    assert normal["rank"] == 2
    assert normal["is_expiry_day"] is False
