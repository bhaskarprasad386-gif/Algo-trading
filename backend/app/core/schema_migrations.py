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
            account_columns = {column["name"] for column in inspect(connection).get_columns("trading_accounts")}
            if "realized_pnl" not in account_columns:
                connection.execute(text("ALTER TABLE trading_accounts ADD COLUMN realized_pnl FLOAT DEFAULT 0.0"))

        if "orders" in account_tables:
            order_columns = {column["name"] for column in inspect(connection).get_columns("orders")}
            for name, definition in {"user_id": "INTEGER", "price": "FLOAT", "pnl": "FLOAT"}.items():
                if name not in order_columns:
                    connection.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {definition}"))

        if "positions" in account_tables:
            position_columns = {column["name"] for column in inspect(connection).get_columns("positions")}
            for name, definition in {"user_id": "INTEGER", "stop_loss": "FLOAT", "target": "FLOAT"}.items():
                if name not in position_columns:
                    connection.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {definition}"))

        if "backtest_jobs" not in account_tables:
            connection.execute(text("""
                CREATE TABLE backtest_jobs (
                    id INTEGER PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL UNIQUE,
                    status VARCHAR(16) NOT NULL DEFAULT 'queued',
                    symbol VARCHAR(128) NOT NULL,
                    contract_month VARCHAR(64) NOT NULL,
                    requested_days INTEGER NOT NULL,
                    progress_pct FLOAT NOT NULL DEFAULT 0.0,
                    symbols_processed INTEGER NOT NULL DEFAULT 0,
                    symbols_total INTEGER NOT NULL DEFAULT 0,
                    message TEXT,
                    result_json TEXT,
                    config_json TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_jobs_status ON backtest_jobs (status)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_jobs_job_id ON backtest_jobs (job_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_jobs_symbol ON backtest_jobs (symbol)"))
        else:
            job_columns = {column["name"] for column in inspect(connection).get_columns("backtest_jobs")}
            if "config_json" not in job_columns:
                connection.execute(text("ALTER TABLE backtest_jobs ADD COLUMN config_json TEXT"))

        if "backtest_job_result_chunks" not in account_tables:
            connection.execute(text("""
                CREATE TABLE backtest_job_result_chunks (
                    id INTEGER PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL,
                    sequence INTEGER NOT NULL,
                    symbol VARCHAR(128) NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at DATETIME
                )
            """))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_job_result_chunk ON backtest_job_result_chunks (job_id, sequence)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_job_result_chunks_job ON backtest_job_result_chunks (job_id, sequence)"))

        if "password_reset_tokens" not in account_tables:
            connection.execute(text("""
                CREATE TABLE password_reset_tokens (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash VARCHAR(64) NOT NULL UNIQUE,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME,
                    created_at DATETIME NOT NULL
                )
            """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user ON password_reset_tokens (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_expires ON password_reset_tokens (expires_at)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash ON password_reset_tokens (token_hash)"))

        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_mobile_number ON users (mobile_number)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions (user_id)"))

    # Resume only jobs that have a complete persisted configuration. Older jobs are
    # failed safely by the recovery routine instead of being replayed with guesses.
    from app.scanner.backtest_jobs import recover_interrupted_jobs
    recover_interrupted_jobs()
