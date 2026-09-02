from sqlalchemy import inspect, text

from app.core.database import Base, engine

# Import models so SQLAlchemy registers all tables before create_all.
from app import models  # noqa: F401,E402


def _ensure_cash_future_history_columns() -> None:
    """Apply small, idempotent schema additions for existing installations.

    ``create_all`` only creates missing tables; it does not add columns to an
    already-created table. Keep this migration deliberately narrow until a full
    migration framework is introduced.
    """
    inspector = inspect(engine)
    table_name = "cash_future_history"
    if not inspector.has_table(table_name):
        return

    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "expiry_date" in columns:
        return

    dialect = engine.dialect.name
    with engine.begin() as connection:
        if dialect == "sqlite":
            connection.execute(text("ALTER TABLE cash_future_history ADD COLUMN expiry_date DATE"))
        elif dialect in {"postgresql", "postgres"}:
            connection.execute(text("ALTER TABLE cash_future_history ADD COLUMN IF NOT EXISTS expiry_date DATE"))
        else:
            raise RuntimeError(f"Unsupported database dialect for schema migration: {dialect}")


def init_db() -> None:
    """Create missing tables and apply safe schema additions without dropping data."""
    Base.metadata.create_all(bind=engine)
    _ensure_cash_future_history_columns()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
