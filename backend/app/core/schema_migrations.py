import json

from sqlalchemy import inspect, text

from app.core.database import engine


def run_schema_migrations() -> None:
    """Apply small backward-compatible schema additions for existing installs."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    if "box_spread_paper_trades" in tables:
        paper_columns = {column["name"] for column in inspector.get_columns("box_spread_paper_trades")}
        with engine.begin() as connection:
            if "entry_spot_price" not in paper_columns:
                connection.execute(text("ALTER TABLE box_spread_paper_trades ADD COLUMN entry_spot_price NUMERIC(18, 4)"))
            if "alert_edge" not in paper_columns:
                connection.execute(text("ALTER TABLE box_spread_paper_trades ADD COLUMN alert_edge NUMERIC(18, 6)"))

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
        live_box_tables = set(inspect(connection).get_table_names())
        if "live_box_spread_snapshots" in live_box_tables:
            snapshot_columns = {
                column["name"]
                for column in inspect(connection).get_columns("live_box_spread_snapshots")
            }
            if "expiry_type" not in snapshot_columns:
                connection.execute(text(
                    "ALTER TABLE live_box_spread_snapshots ADD COLUMN expiry_type VARCHAR(8)"
                ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_box_snapshot_symbol_expiry_time "
                "ON live_box_spread_snapshots (symbol, expiry_type, observed_at)"
            ))

            # Backfill only classifications already persisted in JSON. This startup
            # migration deliberately does not guess from an instrument master that
            # is initialized later; unclassified legacy rows remain NULL and are
            # resolved by the API's compatibility path.
            last_id = 0
            while True:
                batch = connection.execute(text(
                    "SELECT id, payload_json FROM live_box_spread_snapshots "
                    "WHERE id > :last_id AND expiry_type IS NULL "
                    "ORDER BY id LIMIT 500"
                ), {"last_id": last_id}).fetchall()
                if not batch:
                    break
                updates = []
                for snapshot_id, payload_json in batch:
                    last_id = snapshot_id
                    try:
                        payload = json.loads(payload_json or "{}")
                    except (TypeError, ValueError):
                        continue
                    kind = str(payload.get("expiry_type") or "").strip().upper()
                    if kind in {"WEEKLY", "MONTHLY"}:
                        updates.append({"snapshot_id": snapshot_id, "kind": kind})
                if updates:
                    connection.execute(text(
                        "UPDATE live_box_spread_snapshots SET expiry_type = :kind "
                        "WHERE id = :snapshot_id AND expiry_type IS NULL"
                    ), updates)
        if "live_box_spread_alert_history" in live_box_tables:
            box_alert_columns = {column["name"] for column in inspect(connection).get_columns("live_box_spread_alert_history")}
            for name in (
                "low_call_bid_qty", "low_call_ask_qty", "low_put_bid_qty", "low_put_ask_qty",
                "high_call_bid_qty", "high_call_ask_qty", "high_put_bid_qty", "high_put_ask_qty",
            ):
                if name not in box_alert_columns:
                    connection.execute(text(f"ALTER TABLE live_box_spread_alert_history ADD COLUMN {name} INTEGER"))
            if "expiry_type" not in box_alert_columns:
                connection.execute(text("ALTER TABLE live_box_spread_alert_history ADD COLUMN expiry_type VARCHAR(8)"))
            for name, definition in {
                "depth_edge_per_unit": "FLOAT",
                "max_executable_lots": "INTEGER",
                "tradability_status": "VARCHAR(16)",
            }.items():
                if name not in box_alert_columns:
                    connection.execute(text(f"ALTER TABLE live_box_spread_alert_history ADD COLUMN {name} {definition}"))
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
        # Durable IV observations are isolated from all other scanner histories.
        # Source-time uniqueness makes retries idempotent across sampler restarts.
        # PostgreSQL needs SERIAL for generated integer primary keys; SQLite
        # uses INTEGER PRIMARY KEY backed by rowid.
        observation_id_type = "SERIAL" if connection.dialect.name == "postgresql" else "INTEGER"
        connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS iv_option_observations (
                id {observation_id_type} PRIMARY KEY,
                token VARCHAR(64) NOT NULL,
                underlying VARCHAR(32) NOT NULL,
                expiry VARCHAR(32) NOT NULL,
                strike FLOAT NOT NULL,
                option_type VARCHAR(2) NOT NULL,
                source_timestamp_ns BIGINT NOT NULL,
                spot_timestamp_ns BIGINT NOT NULL,
                spot_price FLOAT,
                sampled_at_ns BIGINT NOT NULL,
                iv FLOAT,
                iv_source VARCHAR(24) NOT NULL DEFAULT 'LOCAL_MODEL',
                oi BIGINT,
                bid FLOAT,
                ask FLOAT,
                ltp FLOAT,
                mark FLOAT,
                volume BIGINT
            )
        """))
        observation_columns = {column["name"] for column in inspect(connection).get_columns("iv_option_observations")}
        if "spot_price" not in observation_columns:
            connection.execute(text("ALTER TABLE iv_option_observations ADD COLUMN spot_price FLOAT"))
        if "iv_source" not in observation_columns:
            connection.execute(text("ALTER TABLE iv_option_observations ADD COLUMN iv_source VARCHAR(24) NOT NULL DEFAULT 'LOCAL_MODEL'"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_iv_option_observation_source "
            "ON iv_option_observations (token, source_timestamp_ns)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_iv_option_observation_contract_time "
            "ON iv_option_observations (underlying, expiry, token, source_timestamp_ns)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_iv_option_observation_time "
            "ON iv_option_observations (source_timestamp_ns)"
        ))

        # Dedicated source-timestamped spot ticks; never infer this series from option sampling.
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS iv_spot_observations (
                underlying VARCHAR(32) NOT NULL,
                source_timestamp_ns BIGINT NOT NULL,
                price FLOAT NOT NULL,
                received_at_ns BIGINT NOT NULL,
                PRIMARY KEY (underlying, source_timestamp_ns)
            )
        """))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_iv_spot_observation_time "
            "ON iv_spot_observations (underlying, source_timestamp_ns)"
        ))

        # Durable IV detector transitions are separate from option observations.
        signal_event_id_type = "SERIAL" if connection.dialect.name == "postgresql" else "INTEGER"
        connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS iv_signal_events (
                id {signal_event_id_type} PRIMARY KEY,
                stream_id VARCHAR(32) NOT NULL,
                underlying VARCHAR(32) NOT NULL,
                expiry_type VARCHAR(16) NOT NULL,
                state VARCHAR(16) NOT NULL,
                direction VARCHAR(8) NOT NULL,
                event_timestamp_ns BIGINT NOT NULL,
                source_timestamp_ns BIGINT,
                spot_timestamp_ns BIGINT,
                spot_price FLOAT,
                volume_ratio FLOAT,
                premium_change_pct FLOAT,
                iv_change_points FLOAT,
                spot_change_pct FLOAT,
                contracts_confirming INTEGER NOT NULL DEFAULT 0,
                qualifying_tokens TEXT NOT NULL DEFAULT '[]',
                reason TEXT NOT NULL DEFAULT ''
            )
        """))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_iv_signal_event_identity "
            "ON iv_signal_events (stream_id, state, direction, event_timestamp_ns)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_iv_signal_event_time "
            "ON iv_signal_events (underlying, event_timestamp_ns)"
        ))

        # A broker fill can be retried, but the same user/fill identity must
        # never create a second cash/P&L mutation. Nullable keeps legacy orders valid.
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_user_fill_id ON orders (user_id, fill_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_audit_hash ON orders (audit_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_audit_chain ON orders (user_id, id, audit_hash, previous_audit_hash)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions (user_id)"))

