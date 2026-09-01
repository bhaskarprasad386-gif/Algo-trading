from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.market_data.candle_builder import CandleBuilder
from app.market_data.candle_storage import CandleStorage
from app.models import Candle


def test_completed_candle_is_persisted():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    builder = CandleBuilder(interval_seconds=60)
    start = datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc)
    builder.update(100, 10, start)
    builder.update(105, 5, start.replace(second=30))
    completed = builder.update(102, 7, start.replace(minute=1))
    assert completed is not None

    with Session(engine) as db:
        storage = CandleStorage(db)
        storage.save(token="26000", symbol="NIFTY", timeframe="1m", candle=completed)
        storage.commit()

        row = db.scalar(select(Candle).where(Candle.token == "26000"))
        assert row is not None
        assert row.symbol == "NIFTY"
        assert row.timeframe == "1m"
        assert row.open == 100
        assert row.high == 105
        assert row.low == 100
        assert row.close == 105
        assert row.volume == 15
