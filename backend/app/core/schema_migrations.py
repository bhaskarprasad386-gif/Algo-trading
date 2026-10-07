from sqlalchemy import inspect, text

from app.core.database import engine


def run_schema_migrations() -> None:
    """Apply small backward-compatible schema additions for existing installs."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    user_columns = columns
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
        if "alerts_enabled" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN alerts_enabled BOOLEAN DEFAULT 1"))
        for name, definition in {
            "whatsapp_alerts_enabled": "BOOLEAN DEFAULT 1",
            "telegram_alerts_enabled": "BOOLEAN DEFAULT 1",
            "email_alerts_enabled": "BOOLEAN DEFAULT 1",
            "alert_email": "VARCHAR(320)",
        }.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))

        alert_tables = set(inspect(connection).get_table_names())
        if "alert_contacts" not in alert_tables:
            connection.execute(text("""
                CREATE TABLE alert_contacts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    label VARCHAR(64) NOT NULL DEFAULT 'Primary',
                    mobile_number VARCHAR(32) NOT NULL DEFAULT '',
                    email_address VARCHAR(320),
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    sms_enabled BOOLEAN NOT NULL DEFAULT 0,
                    app_enabled BOOLEAN NOT NULL DEFAULT 1,
                    whatsapp_enabled BOOLEAN NOT NULL DEFAULT 0,
                    telegram_enabled BOOLEAN NOT NULL DEFAULT 0,
                    telegram_chat_id VARCHAR(128) NOT NULL DEFAULT '',
                    email_enabled BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_alert_contacts_user ON alert_contacts (user_id, id)"))
        alert_contact_columns = {column["name"] for column in inspect(connection).get_columns("alert_contacts")}
        for name, definition in {
            "email_address": "VARCHAR(320)",
            "telegram_enabled": "BOOLEAN DEFAULT 0",
            "telegram_chat_id": "VARCHAR(128) DEFAULT ''",
        }.items():
            if name not in alert_contact_columns:
                connection.execute(text(f"ALTER TABLE alert_contacts ADD COLUMN {name} {definition}"))

        if "alert_rules" in alert_tables:
            alert_columns = {column["name"] for column in inspect(connection).get_columns("alert_rules")}
            for name, definition in {
                "name": "VARCHAR(128) DEFAULT 'Unnamed Alert'",
                "metric": "VARCHAR(32) DEFAULT 'gross_profit'",
                "operator": "VARCHAR(2) DEFAULT '>='",
                "threshold": "FLOAT DEFAULT 0.0",
            }.items():
                if name not in alert_columns:
                    connection.execute(text(f"ALTER TABLE alert_rules ADD COLUMN {name} {definition}"))

        account_tables = set(inspect(connection).get_table_names())
        if "trading_accounts" in account_tables:
            account_columns = {column["name"] for column in inspect(connection).get_columns("trading_accounts")}
            if "realized_pnl" not in account_columns:
                connection.execute(text("ALTER TABLE trading_accounts ADD COLUMN realized_pnl FLOAT DEFAULT 0.0"))
            if "box_spread_auto_lots" not in account_columns:
                connection.execute(text("ALTER TABLE trading_accounts ADD COLUMN box_spread_auto_lots INTEGER DEFAULT 1"))
            if "initial_virtual_balance" not in account_columns:
                connection.execute(text("ALTER TABLE trading_accounts ADD COLUMN initial_virtual_balance FLOAT DEFAULT 10000000.0"))
                connection.execute(text("""
                    UPDATE trading_accounts
                    SET initial_virtual_balance =
                        virtual_balance
                        - COALESCE(realized_pnl, 0.0)
                        + COALESCE((
                            SELECT SUM(ABS(p.quantity) * p.average_price)
                            FROM positions p
                            WHERE p.user_id = trading_accounts.user_id
                              AND p.is_paper = 1
                              AND p.quantity != 0
                        ), 0.0)
                """))
            if "initial_balance_source" not in account_columns:
                connection.execute(text("ALTER TABLE trading_accounts ADD COLUMN initial_balance_source VARCHAR(32) DEFAULT 'MIGRATED_INFERRED'"))

        if "live_box_spread_paper_positions" in account_tables:
            box_columns = {column["name"] for column in inspect(connection).get_columns("live_box_spread_paper_positions")}
            if "closed_at" not in box_columns:
                connection.execute(text("ALTER TABLE live_box_spread_paper_positions ADD COLUMN closed_at DATETIME"))

        if "orders" in account_tables:
            order_columns = {column["name"] for column in inspect(connection).get_columns("orders")}
            for name, definition in {
                "user_id": "INTEGER",
                "price": "FLOAT",
                "pnl": "FLOAT",
                "order_type": "VARCHAR(16) DEFAULT 'MARKET'",
                "trigger_price": "FLOAT",
                "filled_quantity": "INTEGER DEFAULT 0",
                "average_fill_price": "FLOAT",
                "time_in_force": "VARCHAR(16) DEFAULT 'DAY'",
                "fill_id": "VARCHAR(128)",
                "audit_hash": "VARCHAR(64)",
                "previous_audit_hash": "VARCHAR(64)",
            }.items():
                if name not in order_columns:
                    connection.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {definition}"))

        if "positions" in account_tables:
            position_columns = {column["name"] for column in inspect(connection).get_columns("positions")}
            for name, definition in {"user_id": "INTEGER", "stop_loss": "FLOAT", "target": "FLOAT"}.items():
                if name not in position_columns:
                    connection.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {definition}"))

        if "strategy_auto_paper_positions" in account_tables:
            paper_columns = {column["name"] for column in inspect(connection).get_columns("strategy_auto_paper_positions")}
            paper_additions = {
                "user_id": "INTEGER", "trade_key": "VARCHAR(256)", "alert_event_id": "VARCHAR(256)",
                "direction": "VARCHAR(64)", "entry_pnl": "FLOAT DEFAULT 0.0", "realized_pnl": "FLOAT",
                "pnl_pct": "FLOAT DEFAULT 0.0", "capital_allocated": "FLOAT DEFAULT 0.0",
                "first_expiry": "VARCHAR(64)", "exit_reason": "VARCHAR(64)",
                "emergency_closed": "BOOLEAN DEFAULT 0", "legs_json": "TEXT DEFAULT '[]'",
            }
            for name, definition in paper_additions.items():
                if name not in paper_columns:
                    connection.execute(text(f"ALTER TABLE strategy_auto_paper_positions ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_strategy_auto_paper_trade_key ON strategy_auto_paper_positions (trade_key)"))

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
        if "live_cash_future_alert_history" in account_tables:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_live_cf_alert_identity ON live_cash_future_alert_history (symbol, contract_month, timestamp_ns, event)"))
        if "live_synthetic_alert_history" not in account_tables:
            connection.execute(text("""
                CREATE TABLE live_synthetic_alert_history (
                    id INTEGER PRIMARY KEY,
                    observed_at DATETIME NOT NULL,
                    timestamp_ns BIGINT NOT NULL,
                    symbol VARCHAR(128) NOT NULL,
                    instrument_class VARCHAR(16) NOT NULL,
                    expiry INTEGER NOT NULL,
                    strike FLOAT NOT NULL,
                    direction VARCHAR(8) NOT NULL,
                    executable_edge FLOAT NOT NULL,
                    edge_per_lot FLOAT NOT NULL,
                    gross_pnl FLOAT NOT NULL,
                    lot_size INTEGER NOT NULL
                )
            """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_live_syn_alert_observed_at ON live_synthetic_alert_history (observed_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_live_syn_alert_symbol ON live_synthetic_alert_history (symbol, observed_at)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_live_syn_alert_identity ON live_synthetic_alert_history (symbol, expiry, timestamp_ns, strike, direction)"))
        if "live_paper_trades" in account_tables:
            # Prevent concurrent duplicate paper entries for the same user/event.
            # Existing duplicate rows are not silently deleted; deployment must
            # fail loudly if legacy data violates the new invariant.
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_live_paper_user_event ON live_paper_trades (user_id, event_id)"))
        # A broker fill can be retried, but the same user/fill identity must
        # never create a second cash/P&L mutation. Nullable keeps legacy orders valid.
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_user_fill_id ON orders (user_id, fill_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_audit_hash ON orders (audit_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_audit_chain ON orders (user_id, id, audit_hash, previous_audit_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions (user_id)"))
        # Enforce the same invariant on existing SQLite deployments. Do not silently
        # deduplicate legacy rows: duplicate active paper positions must fail schema
        # migration and be reconciled explicitly before trading can start.
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_positions_user_symbol_active_paper "
            "ON positions (user_id, symbol) "
            "WHERE is_paper = 1 AND is_open = 1"
        ))
