from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.backtest.data_store import (
    instrument_key,
    iter_bars,
    missing_ranges,
    record_coverage,
    upsert_1m_bars,
)
from app.core.database import Base, SessionLocal, engine
from app.models import BacktestDataCoverage, HistoricalMarketBar


def _bar(key, timestamp, close):
    return {
        "instrument_key": key,
        "symbol": "RELIANCE",
        "segment": "NFO",
        "instrument_type": "FUTURE",
        "contract_month": "2026-10",
        "expiry_date": datetime(2026, 10, 29),
        "lot_size": 500,
        "timestamp": timestamp,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1000,
        "open_interest": 2000,
        "bid": close - 0.5,
        "ask": close + 0.5,
        "source": "test",
        "data_version": "v1",
        "source_hash": "test-hash",
    }


def test_1m_upsert_is_idempotent_and_repairs_corrected_minute():
    Base.metadata.create_all(bind=engine)
    key = instrument_key("RELIANCE", "NFO", "FUTURE", "2026-10")
    start = datetime(2026, 1, 2, 9, 15)
    db = SessionLocal()
    try:
        db.query(HistoricalMarketBar).filter(HistoricalMarketBar.instrument_key == key).delete()
        db.commit()
        assert upsert_1m_bars(db, [_bar(key, start, 100.0)]) == 1
        assert upsert_1m_bars(db, [_bar(key, start, 101.0)]) == 1
        rows = list(db.scalars(select(HistoricalMarketBar).where(HistoricalMarketBar.instrument_key == key)))
        assert len(rows) == 1
        assert rows[0].close == 101.0
    finally:
        db.query(HistoricalMarketBar).filter(HistoricalMarketBar.instrument_key == key).delete()
        db.commit()
        db.close()


def test_missing_ranges_only_fetch_uncovered_windows():
    Base.metadata.create_all(bind=engine)
    key = instrument_key("RELIANCE", "NFO", "FUTURE", "2026-10")
    start = datetime(2026, 1, 2, 9, 15)
    covered_end = start + timedelta(minutes=30)
    end = start + timedelta(minutes=90)
    db = SessionLocal()
    try:
        db.query(BacktestDataCoverage).filter(BacktestDataCoverage.instrument_key == key).delete()
        db.commit()
        record_coverage(
            db,
            key=key,
            symbol="RELIANCE",
            segment="NFO",
            contract_month="2026-10",
            start=start,
            end=covered_end,
            row_count=31,
            data_version="v1",
            source_hash="hash",
        )
        assert missing_ranges(db, key, start, end) == [(covered_end, end)]
    finally:
        db.query(BacktestDataCoverage).filter(BacktestDataCoverage.instrument_key == key).delete()
        db.commit()
        db.close()


def test_iter_bars_streams_ordered_history_without_materializing_full_year():
    Base.metadata.create_all(bind=engine)
    key = instrument_key("RELIANCE", "NFO", "FUTURE", "2026-10")
    start = datetime(2026, 1, 2, 9, 15)
    db = SessionLocal()
    try:
        db.query(HistoricalMarketBar).filter(HistoricalMarketBar.instrument_key == key).delete()
        db.commit()
        bars = [_bar(key, start + timedelta(minutes=i), 100.0 + i) for i in range(7)]
        upsert_1m_bars(db, bars)
        streamed = iter_bars(db, key=key, start=start, end=start + timedelta(minutes=6), chunk_size=2)
        assert [row.timestamp for row in streamed] == [item["timestamp"] for item in bars]
    finally:
        db.query(HistoricalMarketBar).filter(HistoricalMarketBar.instrument_key == key).delete()
        db.commit()
        db.close()


def test_coverage_record_is_idempotent_and_updates_metadata():
    Base.metadata.create_all(bind=engine)
    key = instrument_key("RELIANCE", "NFO", "FUTURE", "2026-10")
    start = datetime(2026, 1, 3, 9, 15)
    end = start + timedelta(minutes=60)
    db = SessionLocal()
    try:
        db.query(BacktestDataCoverage).filter(BacktestDataCoverage.instrument_key == key).delete()
        db.commit()
        first = record_coverage(db, key=key, symbol="RELIANCE", segment="NFO", contract_month="2026-10", start=start, end=end, row_count=61, data_version="v1", source_hash="a")
        second = record_coverage(db, key=key, symbol="RELIANCE", segment="NFO", contract_month="2026-10", start=start, end=end, row_count=62, data_version="v2", source_hash="b")
        count = db.scalar(select(func.count()).select_from(BacktestDataCoverage).where(BacktestDataCoverage.instrument_key == key))
        assert first.id == second.id and count == 1 and second.row_count == 62 and second.data_version == "v2"
    finally:
        db.query(BacktestDataCoverage).filter(BacktestDataCoverage.instrument_key == key).delete()
        db.commit()
        db.close()
