"""Regression tests for additive database startup used by the Box Spread paper ledger."""

from sqlalchemy import create_engine, inspect, text

from app.core import init_db as database_init


def test_init_db_creates_paper_ledger_tables_and_preserves_existing_sqlite_rows(
    tmp_path, monkeypatch
):
    """Exercise startup schema creation against an isolated on-disk SQLite DB."""
    database_path = tmp_path / "startup-schema.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"timeout": 5})

    try:
        # Model a pre-existing production table with a row and legacy columns.
        # init_db must add missing columns and preserve existing data.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE cash_future_history "
                    "(id INTEGER PRIMARY KEY, symbol VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO cash_future_history (id, symbol) VALUES (1, 'NIFTY')")
            )

        monkeypatch.setattr(database_init, "engine", engine)
        database_init.init_db()

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "box_spread_paper_accounts",
            "box_spread_paper_trades",
            "box_spread_paper_entry_claims",
            "box_spread_paper_ledger_entries",
        }.issubset(tables)

        history_columns = {
            column["name"] for column in inspector.get_columns("cash_future_history")
        }
        assert {
            "expiry_date",
            "cash_bid",
            "cash_ask",
            "future_bid",
            "future_ask",
        }.issubset(history_columns)

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, symbol FROM cash_future_history WHERE id = 1")
            ).one()
        assert tuple(row) == (1, "NIFTY")
    finally:
        engine.dispose()
