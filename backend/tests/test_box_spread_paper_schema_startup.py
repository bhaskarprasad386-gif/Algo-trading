"""Regression tests for the additive SQLAlchemy schema creation used at API startup."""

from sqlalchemy import create_engine, text

from app.core.database import Base
from app import models as _models  # noqa: F401
from app.scanner import box_spread_paper_ledger as _paper_models  # noqa: F401


def test_startup_create_all_adds_paper_tables_without_touching_existing_rows(tmp_path):
    """Mirror main.py's Base.metadata.create_all against an isolated SQLite DB."""
    database_path = tmp_path / "startup-schema.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"timeout": 5})

    try:
        # Existing production tables and data must survive create_all unchanged.
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

        Base.metadata.create_all(bind=engine)

        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            assert {
                "box_spread_paper_accounts",
                "box_spread_paper_trades",
                "box_spread_paper_entry_claims",
                "box_spread_paper_ledger_entries",
            }.issubset(tables)

            paper_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(box_spread_paper_trades)"))
            }
            assert "entry_spot_price" in paper_columns

            row = connection.execute(
                text("SELECT id, value FROM existing_business_data WHERE id = 1")
            ).one()
            assert tuple(row) == (1, "preserve-me")

        # Startup may run more than once; schema creation must remain idempotent.
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()
