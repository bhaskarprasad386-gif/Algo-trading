"""Retired paper schema is preserved when present but is not a startup dependency."""

from sqlalchemy import create_engine, text

from app.core.database import Base
from app import models as _models  # noqa: F401


def test_startup_schema_creation_preserves_existing_paper_rows_without_requiring_paper_models(tmp_path):
    database_path = tmp_path / "startup-schema.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"timeout": 5})

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE existing_business_data "
                    "(id INTEGER PRIMARY KEY, value VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO existing_business_data (id, value) VALUES (1, 'preserve-me')")
            )
            # Simulate an older deployment. Retirement must not drop or rewrite
            # these tables/rows, but main.py no longer imports their ORM models.
            connection.execute(
                text(
                    "CREATE TABLE box_spread_paper_trades "
                    "(id INTEGER PRIMARY KEY, status VARCHAR(24) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO box_spread_paper_trades (id, status) "
                    "VALUES (7, 'CLOSED')"
                )
            )

        Base.metadata.create_all(bind=engine)

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, value FROM existing_business_data WHERE id = 1")
            ).one()
            assert tuple(row) == (1, "preserve-me")
            paper_row = connection.execute(
                text("SELECT id, status FROM box_spread_paper_trades WHERE id = 7")
            ).one()
            assert tuple(paper_row) == (7, "CLOSED")

        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()
