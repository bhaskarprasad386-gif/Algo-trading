from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.market_data.storage import TickStorage
from app.models import Tick


def test_market_data_storage_end_to_end():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        storage = TickStorage(db)
        storage.save({
            "token": "26000",
            "symbol": "NIFTY",
            "ltp": 25001.25,
            "volume": 12345,
        })
        storage.commit()

        row = db.scalar(select(Tick).where(Tick.token == "26000"))
        assert row is not None
        assert row.symbol == "NIFTY"
        assert row.ltp == 25001.25
        assert row.volume == 12345.0
