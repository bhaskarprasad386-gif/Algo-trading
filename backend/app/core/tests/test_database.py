from sqlalchemy import inspect

from app.core.database import Base, engine
from app.core.init_db import init_db
from app.models import Candle, Instrument, SystemLog, Tick


def test_database_initializes_without_dropping_schema():
    init_db()
    table_names = set(inspect(engine).get_table_names())

    expected = {
        Instrument.__tablename__,
        Tick.__tablename__,
        Candle.__tablename__,
        SystemLog.__tablename__,
    }
    assert expected.issubset(table_names)
    assert Base.metadata.tables.keys() >= expected
