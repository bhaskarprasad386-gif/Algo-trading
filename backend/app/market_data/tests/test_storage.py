from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.market_data.storage import TickStorage


def test_tick_storage_saves_normalized_tick():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        storage = TickStorage(db)
        row = storage.save({"token": "101", "symbol": "NIFTY", "ltp": "25000.5", "volume": "12"})
        storage.commit()

        assert row.symbol == "NIFTY"
        assert row.ltp == 25000.5
        assert row.volume == 12.0


def test_tick_storage_rejects_missing_identity():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        storage = TickStorage(db)
        try:
            storage.save({"ltp": 100})
        except ValueError as exc:
            assert "requires token and symbol" in str(exc)
        else:
            raise AssertionError("Expected ValueError")
