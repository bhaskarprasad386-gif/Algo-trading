from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.live_cash_future_scanner_result import LiveCashFutureScannerResult
from app.scanner.live_cash_future_scanner import LiveCashFutureScanner


def _signal(scanner: LiveCashFutureScanner, timestamp_ns: int):
    scanner.observe({
        "leg": "CASH", "underlying": "ABC", "ltp": 100.0,
        "bid": 99.9, "ask": 100.0, "source_timestamp_ns": timestamp_ns,
    })
    return scanner.observe({
        "leg": "FUTURE", "underlying": "ABC", "contract_month": "CURRENT",
        "ltp": 101.0, "bid": 100.8, "ask": 101.0,
        "source_timestamp_ns": timestamp_ns,
    })


def test_live_scanner_result_persists_and_cleans_up_after_30_days(monkeypatch, tmp_path):
    monkeypatch.setattr("app.scanner.live_cash_future_scanner.settings.LIVE_CASH_FUTURE_RESULT_RETENTION_DAYS", 30)
    engine = create_engine(f"sqlite:///{tmp_path / 'scanner-results.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_factory():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    scanner = LiveCashFutureScanner()
    first = _signal(scanner, 1_000_000_000)
    assert first is not None
    scanner._persist_result(session_factory, first)

    with Session() as db:
        row = db.query(LiveCashFutureScannerResult).one()
        row.observed_at = row.observed_at - timedelta(days=31)
        db.commit()

    second = _signal(scanner, 2_000_000_000)
    assert second is not None
    scanner._last_result_cleanup = 0.0
    scanner._persist_result(session_factory, second)

    with Session() as db:
        rows = db.query(LiveCashFutureScannerResult).all()
        assert len(rows) == 1
        assert rows[0].timestamp_ns == second.timestamp_ns
        assert rows[0].gap_pct == second.gap_pct
