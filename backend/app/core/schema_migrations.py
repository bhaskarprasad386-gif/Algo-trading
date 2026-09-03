from sqlalchemy import inspect, text

from app.core.database import engine


def run_schema_migrations() -> None:
    """Apply small backward-compatible schema additions for existing installs."""
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "mobile_number" in columns:
        return

    # SQLite and PostgreSQL both support adding a nullable column without
    # rewriting existing user rows.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN mobile_number VARCHAR(20)"))
        try:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_mobile_number ON users (mobile_number)"))
        except Exception:
            # Index creation can be handled by SQLAlchemy metadata on a fresh DB.
            pass
