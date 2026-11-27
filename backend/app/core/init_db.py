from sqlalchemy import inspect, text

from app.core.database import Base, engine

# Import models so SQLAlchemy registers all tables before create_all.
from app import models  # noqa: F401,E402


def _ensure_cash_future_history_columns() -> None:
    """Apply idempotent schema additions for existing Cash-Future tables."""
    inspector = inspect(engine)
    table_name = "cash_future_history"
    if not inspector.has_table(table_name):
        return

    columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing = {
        "expiry_date": "DATE",
        "cash_bid": "FLOAT",
        "cash_ask": "FLOAT",
        "future_bid": "FLOAT",
        "future_ask": "FLOAT",
    }
    dialect = engine.dialect.name
    for name, sql_type in missing.items():
        if name in columns:
            continue
        with engine.begin() as connection:
            if dialect == "sqlite":
                connection.execute(text(f"ALTER TABLE cash_future_history ADD COLUMN {name} {sql_type}"))
            elif dialect in {"postgresql", "postgres"}:
                connection.execute(text(f"ALTER TABLE cash_future_history ADD COLUMN IF NOT EXISTS {name} {sql_type}"))
            else:
                raise RuntimeError(f"Unsupported database dialect for schema migration: {dialect}")


def init_db() -> None:
    """Create missing tables and apply safe schema additions without dropping data."""
    Base.metadata.create_all(bind=engine)
    _ensure_cash_future_history_columns()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
