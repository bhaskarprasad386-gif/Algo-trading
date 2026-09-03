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

        account_tables = set(inspect(connection).get_table_names())
        if "trading_accounts" in account_tables:
            account_columns = {
                column["name"] for column in inspect(connection).get_columns("trading_accounts")
            }
            if "realized_pnl" not in account_columns:
                connection.execute(
                    text("ALTER TABLE trading_accounts ADD COLUMN realized_pnl FLOAT DEFAULT 0.0")
                )

        # Paper execution persistence for existing databases.
        if "orders" in account_tables:
            order_columns = {column["name"] for column in inspect(connection).get_columns("orders")}
            for name, definition in {
                "user_id": "INTEGER",
                "price": "FLOAT",
                "pnl": "FLOAT",
            }.items():
                if name not in order_columns:
                    connection.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {definition}"))

        if "positions" in account_tables:
            position_columns = {column["name"] for column in inspect(connection).get_columns("positions")}
            for name, definition in {
                "user_id": "INTEGER",
                "stop_loss": "FLOAT",
                "target": "FLOAT",
            }.items():
                if name not in position_columns:
                    connection.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {definition}"))

        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_mobile_number ON users (mobile_number)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions (user_id)"))
