from datetime import datetime, timezone

import pytest

from app.scanner.cash_future_historical_ingest import ingest_cash_future_history
from app.scanner.cash_future_history import CashFutureHistoryPoint


class Source:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def fetch(self, *, symbol, contract_month, start, end):
        self.calls.append((symbol, contract_month, start, end))
        yield from self.points


def point(ts: int, symbol: str = "ABC", contract: str = "2026-09"):
    return CashFutureHistoryPoint(
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        symbol=symbol,
        contract_month=contract,
        cash_price=100.0,
        future_price=102.0,
        gap=2.0,
        gap_pct=2.0,
        lot_size=10,
        margin_required=1000.0,
    )


def test_ingest_streams_bounded_batches_and_is_idempotent(db_session):
    points = [point(1), point(2), point(3)]
    source = Source(points)
    start = datetime.fromtimestamp(0, tz=timezone.utc)
    end = datetime.fromtimestamp(10, tz=timezone.utc)

    first = ingest_cash_future_history(
        db_session, source, symbol="abc", contract_month="2026-09",
        start=start, end=end, batch_size=2,
    )
    second = ingest_cash_future_history(
        db_session, source, symbol="ABC", contract_month="2026-09",
        start=start, end=end, batch_size=2,
    )

    assert first.fetched == 3
    assert first.persisted == 3
    assert second.fetched == 3
    assert second.persisted == 3
    assert len(source.calls) == 2

    from app.models.cash_future_history import CashFutureHistory
    assert db_session.query(CashFutureHistory).count() == 3


def test_ingest_rejects_wrong_contract(db_session):
    source = Source([point(1, contract="2026-10")])
    with pytest.raises(ValueError, match="different contract month"):
        ingest_cash_future_history(
            db_session, source, symbol="ABC", contract_month="2026-09",
            start=datetime.fromtimestamp(0, tz=timezone.utc),
            end=datetime.fromtimestamp(10, tz=timezone.utc),
        )


def test_ingest_rejects_out_of_range_record(db_session):
    source = Source([point(100)])
    with pytest.raises(ValueError, match="outside requested range"):
        ingest_cash_future_history(
            db_session, source, symbol="ABC", contract_month="2026-09",
            start=datetime.fromtimestamp(0, tz=timezone.utc),
            end=datetime.fromtimestamp(10, tz=timezone.utc),
        )
