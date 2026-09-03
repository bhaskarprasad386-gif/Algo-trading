from sqlalchemy import inspect, text

from app.core.database import engine


def run_schema_migrations() -> None:
    """Apply small backward-compatible schema additions for existing installs."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    additions = {
        "email": "VARCHAR(320)",
        "mobile_number": "VARCHAR(20)",
        "hashed_password": "VARCHAR(256) DEFAULT ''",
        "full_name": "VARCHAR(256)",
        "is_active": "BOOLEAN DEFAULT 1",
    }

    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))

        # These indexes are safe on SQLite/PostgreSQL and preserve the
        # one-account-per-email/mobile invariant for newly created users.
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_mobile_number ON users (mobile_number)"))
