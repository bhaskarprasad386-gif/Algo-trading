from datetime import date, datetime

from app.backtesting.cash_future_download_queue import build_rollover_download_queue


class _Future:
    def __init__(self, token: str, symbol: str, exchange: str = "NFO"):
        self.token = token
        self.symbol = symbol
        self.exchange = exchange


class _Catalog:
    def resolve(self, *, exchange, underlying, expiry, as_of):
        return _Future(token="123", symbol="NIFTY26SEP")


def test_queue_passes_explicit_session_days_to_rollover_resolver(monkeypatch):
    captured = {}

    def fake_build_mode_segments(catalog, **kwargs):
        captured.update(kwargs)
        return ()

    monkeypatch.setattr(
        "app.backtesting.cash_future_download_queue.build_mode_segments",
        fake_build_mode_segments,
    )

    build_rollover_download_queue(
        catalog=_Catalog(),
        spot_instrument="NSE:NIFTY",
        exchange="NFO",
        underlying="NIFTY",
        start=datetime(2026, 9, 5, 9, 15),
        end=datetime(2026, 9, 8, 15, 30),
        session_days=(date(2026, 9, 7),),
    )

    assert captured["session_days"] == (date(2026, 9, 7),)
