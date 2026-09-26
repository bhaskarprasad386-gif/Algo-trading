from datetime import date, timedelta

from app.scripts.probe_available_cash_future_history import _published_futures


def _future(underlying: str, token: str, expiry: date):
    return {
        "exch_seg": "NFO",
        "instrumenttype": "FUTSTK",
        "token": token,
        "symbol": f"{underlying}{expiry.strftime('%d%b').upper()}",
        "name": underlying,
        "expiry": expiry.strftime("%d%b%Y").upper(),
        "lotsize": "1",
    }


def test_published_futures_are_provider_current_and_sorted():
    today = date(2026, 9, 26)
    rows = [
        _future("AAA", "103", today + timedelta(days=65)),
        _future("AAA", "101", today + timedelta(days=4)),
        _future("AAA", "102", today + timedelta(days=34)),
        _future("OLD", "999", today - timedelta(days=1)),
        {**_future("AAA", "888", today + timedelta(days=4)), "instrumenttype": "OPTSTK"},
    ]
    records = _published_futures(rows, today)
    assert [(r.underlying, r.token) for r in records] == [
        ("AAA", "101"), ("AAA", "102"), ("AAA", "103")
    ]


def test_published_futures_preserves_only_currently_published_expiries():
    today = date(2026, 9, 26)
    rows = [_future("AAA", "101", today + timedelta(days=4))]
    records = _published_futures(rows, today)
    assert len(records) == 1
    assert records[0].expiry == today + timedelta(days=4)
