from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import Candle


def test_candle_identity_is_unique():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = Candle(
            token="123",
            symbol="TEST",
            timeframe="ONE_MINUTE",
            timestamp=datetime(2026, 9, 1, 9, 15),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
        )
        db.add(first)
        db.commit()

        duplicate = Candle(
            token="123",
            symbol="TEST",
            timeframe="ONE_MINUTE",
            timestamp=datetime(2026, 9, 1, 9, 15),
            open=100,
            high=102,
            low=98,
            close=101,
            volume=1200,
        )
        db.add(duplicate)
        try:
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError("duplicate candle identity was accepted")

        rows = db.scalars(select(Candle)).all()
        assert len(rows) == 1
