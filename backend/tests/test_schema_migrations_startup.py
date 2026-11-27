"""Isolated tests for the legacy startup schema migration.

These tests never import app.main and never connect to the configured application
DATABASE_URL. PostgreSQL runs inside a per-test schema and is opt-in through
IV_TEST_POSTGRES_URL (CI provides a disposable PostgreSQL service).
"""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from app.core import schema_migrations


@pytest.fixture(params=["sqlite", "postgres"])
def migration_engine(request):
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            yield engine
        finally:
            engine.dispose()
        return

    url = os.getenv("IV_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("IV_TEST_POSTGRES_URL is not configured")

    admin_engine = create_engine(url, pool_pre_ping=True)
    schema = f"migration_test_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed_minimal_legacy_schema(engine):
    # A legacy install has a users table, but can have only a subset of the
    # tables/columns that newer application versions expect.
    generated_id_type = "SERIAL" if engine.dialect.name == "postgresql" else "INTEGER"
    with engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE users (id {generated_id_type} PRIMARY KEY)"))
        connection.execute(text(f"CREATE TABLE orders (id {generated_id_type} PRIMARY KEY)"))
        connection.execute(text(f"CREATE TABLE positions (id {generated_id_type} PRIMARY KEY)"))


def test_fresh_database_is_left_for_orm_create_all(migration_engine, monkeypatch):
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)

    schema_migrations.run_schema_migrations()

    assert inspect(migration_engine).get_table_names() == []


def test_legacy_schema_upgrade_is_repeatable(migration_engine, monkeypatch):
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)

    schema_migrations.run_schema_migrations()

    inspector = inspect(migration_engine)
    assert {"users", "orders", "positions", "alert_contacts"} <= set(
        inspector.get_table_names()
    )
    assert {
        "email",
        "mobile_number",
        "hashed_password",
        "alerts_enabled",
    } <= {column["name"] for column in inspector.get_columns("users")}
    assert {
        "user_id",
        "fill_id",
        "audit_hash",
        "previous_audit_hash",
    } <= {column["name"] for column in inspector.get_columns("orders")}
    assert {
        "user_id",
        "stop_loss",
        "target",
    } <= {column["name"] for column in inspector.get_columns("positions")}

    # A second startup must not attempt duplicate columns, tables, or indexes.
    schema_migrations.run_schema_migrations()

    assert "iv_option_observations" in inspect(migration_engine).get_table_names()




def test_existing_legacy_backtest_jobs_records_are_preserved_without_migration(
    migration_engine, monkeypatch
):
    """Retirement must leave existing job history untouched and never recreate it."""
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    with migration_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE backtest_jobs (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, "
            "status TEXT NOT NULL, legacy_payload TEXT NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO backtest_jobs (id, job_id, status, legacy_payload) "
            "VALUES (7, 'legacy-job-7', 'complete', 'preserve-me')"
        ))
        before_columns = [item["name"] for item in inspect(connection).get_columns("backtest_jobs")]

    schema_migrations.run_schema_migrations()

    with migration_engine.connect() as connection:
        after_columns = [item["name"] for item in inspect(connection).get_columns("backtest_jobs")]
        row = connection.execute(
            text("SELECT id, job_id, status, legacy_payload FROM backtest_jobs WHERE id=7")
        ).one()
    assert after_columns == before_columns
    assert row == (7, "legacy-job-7", "complete", "preserve-me")


def test_box_historical_snapshot_records_are_not_migrated_or_deleted(
    migration_engine, monkeypatch
):
    """Box history remains byte-for-byte readable; runtime startup adds no Box schema."""
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    original_rows = [
        (1, "NIFTY", '{"expiry_type":"weekly"}'),
        (2, "NIFTY", '{"expiry_type":"MONTHLY"}'),
        (3, "NIFTY", '{"expiry_type":"UNKNOWN"}'),
        (4, "NIFTY", "not-json"),
    ]
    with migration_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE live_box_spread_snapshots ("
            "id INTEGER PRIMARY KEY, symbol VARCHAR(32), payload_json TEXT, observed_at TIMESTAMP)"
        ))
        connection.execute(
            text("INSERT INTO live_box_spread_snapshots (id, symbol, payload_json) "
                 "VALUES (:id, :symbol, :payload)"),
            [{"id": row[0], "symbol": row[1], "payload": row[2]} for row in original_rows],
        )
        before_columns = [item["name"] for item in inspect(connection).get_columns("live_box_spread_snapshots")]

    schema_migrations.run_schema_migrations()

    with migration_engine.connect() as connection:
        after_columns = [item["name"] for item in inspect(connection).get_columns("live_box_spread_snapshots")]
        rows = connection.execute(
            text("SELECT id, symbol, payload_json FROM live_box_spread_snapshots ORDER BY id")
        ).all()
    assert after_columns == before_columns
    assert rows == original_rows

def test_order_fill_identity_index_prevents_duplicate_cash_mutations(
    migration_engine, monkeypatch
):
    """A repeated broker fill identity for one user must be rejected."""
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    schema_migrations.run_schema_migrations()

    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO orders (user_id, fill_id) VALUES "
                "(7, 'broker-fill-001'), (7, NULL), (7, NULL)"
            )
        )

    with pytest.raises(IntegrityError):
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO orders (user_id, fill_id) "
                    "VALUES (7, 'broker-fill-001')"
                )
            )



def test_concurrent_postgres_startup_migrations_are_serialized(
    migration_engine, monkeypatch
):
    """Two workers upgrading the same legacy schema must not race on DDL."""
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("Concurrent startup lock is PostgreSQL-specific")

    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    start = threading.Barrier(2)

    def migrate():
        start.wait(timeout=10)
        schema_migrations.run_schema_migrations()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(migrate) for _ in range(2)]
        for future in futures:
            future.result(timeout=120)

    inspector = inspect(migration_engine)
    assert "alert_contacts" in inspector.get_table_names()
    assert "fill_id" in {
        column["name"] for column in inspector.get_columns("orders")
    }



def test_postgres_migration_failure_rolls_back_and_restart_recovers(
    migration_engine, monkeypatch
):
    """A failed DDL batch rolls back, releases the lock, and can be retried."""
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("Transactional DDL recovery assertion is PostgreSQL-specific")

    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    original_text = schema_migrations.text

    def fail_at_final_fill_index(statement):
        if "uq_orders_user_fill_id" in str(statement):
            return original_text("SELECT * FROM intentionally_missing_migration_table")
        return original_text(statement)

    monkeypatch.setattr(schema_migrations, "text", fail_at_final_fill_index)
    with pytest.raises(SQLAlchemyError):
        schema_migrations.run_schema_migrations()

    # Earlier DDL in the same migration transaction must not be partially visible.
    inspector = inspect(migration_engine)
    assert "email" not in {
        column["name"] for column in inspector.get_columns("users")
    }
    assert "alert_contacts" not in inspector.get_table_names()

    # Simulate a fresh worker after the failed startup: lock is released and
    # the migration can safely run again from the unchanged legacy schema.
    monkeypatch.setattr(schema_migrations, "text", original_text)
    schema_migrations.run_schema_migrations()

    inspector = inspect(migration_engine)
    assert "email" in {
        column["name"] for column in inspector.get_columns("users")
    }
    assert "alert_contacts" in inspector.get_table_names()


def test_iv_observation_expiry_time_index_supports_bounded_restore(migration_engine, monkeypatch):
    """The recent-history query can seek directly by underlying, expiry and time."""
    if migration_engine.dialect.name != "sqlite":
        pytest.skip("SQLite query-plan assertion is dialect-specific")

    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    schema_migrations.run_schema_migrations()

    with migration_engine.begin() as connection:
        index = next(
            item for item in inspect(connection).get_indexes("iv_option_observations")
            if item["name"] == "ix_iv_option_observation_expiry_time"
        )
        assert index["column_names"] == ["underlying", "expiry", "source_timestamp_ns"]

        # Seed enough rows across multiple expiries/underlyings for SQLite's
        # planner to compare the contract-time and expiry-time indexes.
        rows = []
        base_ns = 1_800_000_000_000_000_000
        for group, (underlying, expiry) in enumerate((
            ("NIFTY", "2026-11-12"),
            ("NIFTY", "2026-11-19"),
            ("BANKNIFTY", "2026-11-12"),
            ("BANKNIFTY", "2026-11-19"),
        )):
            for token_number in range(5):
                for tick in range(100):
                    timestamp_ns = base_ns + tick * 1_000_000_000
                    rows.append({
                        "token": f"{group}-{token_number}",
                        "underlying": underlying,
                        "expiry": expiry,
                        "strike": 24000 + token_number * 100,
                        "option_type": "CE",
                        "source_timestamp_ns": timestamp_ns,
                        "spot_timestamp_ns": timestamp_ns,
                        "sampled_at_ns": timestamp_ns,
                    })
        connection.execute(
            text(
                "INSERT INTO iv_option_observations "
                "(token, underlying, expiry, strike, option_type, "
                "source_timestamp_ns, spot_timestamp_ns, sampled_at_ns) "
                "VALUES (:token, :underlying, :expiry, :strike, :option_type, "
                ":source_timestamp_ns, :spot_timestamp_ns, :sampled_at_ns)"
            ),
            rows,
        )
        connection.execute(text("ANALYZE iv_option_observations"))

        # Do not force an index: assert that SQLite naturally chooses the new
        # expiry/time index for the exact bounded restore predicate.
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT token, source_timestamp_ns FROM iv_option_observations "
                "WHERE underlying = :underlying AND expiry = :expiry "
                "AND source_timestamp_ns >= :cutoff "
                "AND source_timestamp_ns <= :now "
                "ORDER BY source_timestamp_ns ASC"
            ),
            {
                "underlying": "NIFTY",
                "expiry": "2026-11-12",
                "cutoff": base_ns + 90_000_000_000,
                "now": base_ns + 99_000_000_000,
            },
        ).all()

    details = " ".join(str(row[-1]) for row in plan)
    assert "SEARCH" in details.upper()
    assert "ix_iv_option_observation_expiry_time" in details
    assert "TEMP B-TREE" not in details.upper()


def test_iv_latest_per_contract_restore_uses_covering_contract_index(
    migration_engine, monkeypatch
):
    """Latest-per-token aggregation should be index-only and avoid temp grouping."""
    if migration_engine.dialect.name != "sqlite":
        pytest.skip("SQLite query-plan assertion is dialect-specific")

    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    schema_migrations.run_schema_migrations()

    base_ns = 1_800_000_000_000_000_000
    rows = []
    for group, (underlying, expiry) in enumerate((
        ("NIFTY", "2026-11-12"),
        ("NIFTY", "2026-11-19"),
        ("BANKNIFTY", "2026-11-12"),
        ("BANKNIFTY", "2026-11-19"),
    )):
        for token_number in range(20):
            for tick in range(30):
                timestamp_ns = base_ns + tick * 1_000_000_000
                rows.append({
                    "token": f"{group}-{token_number}",
                    "underlying": underlying,
                    "expiry": expiry,
                    "strike": 24000 + token_number * 100,
                    "option_type": "CE",
                    "source_timestamp_ns": timestamp_ns,
                    "spot_timestamp_ns": timestamp_ns,
                    "sampled_at_ns": timestamp_ns,
                })

    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO iv_option_observations "
                "(token, underlying, expiry, strike, option_type, "
                "source_timestamp_ns, spot_timestamp_ns, sampled_at_ns) "
                "VALUES (:token, :underlying, :expiry, :strike, :option_type, "
                ":source_timestamp_ns, :spot_timestamp_ns, :sampled_at_ns)"
            ),
            rows,
        )
        connection.execute(text("ANALYZE iv_option_observations"))
        # Match the production restore shape: aggregate latest timestamp per
        # token, then join those identities back to full observation rows.
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT iv_option_observations.* "
                "FROM iv_option_observations "
                "JOIN ("
                " SELECT token AS token, "
                " MAX(source_timestamp_ns) AS latest_source_timestamp_ns "
                " FROM iv_option_observations "
                " WHERE underlying = :underlying AND expiry = :expiry "
                " AND source_timestamp_ns <= :now "
                " GROUP BY token"
                ") AS latest_source "
                "ON iv_option_observations.token = latest_source.token "
                "AND iv_option_observations.source_timestamp_ns = "
                "latest_source.latest_source_timestamp_ns "
                "WHERE iv_option_observations.underlying = :underlying "
                "AND iv_option_observations.expiry = :expiry "
                "AND iv_option_observations.source_timestamp_ns <= :now"
            ),
            {
                "underlying": "NIFTY",
                "expiry": "2026-11-12",
                "now": base_ns + 29_000_000_000,
            },
        ).all()

    details = [str(row[-1]).upper() for row in plan]
    combined = " ".join(details)
    assert any(
        "COVERING INDEX IX_IV_OPTION_OBSERVATION_CONTRACT_TIME" in detail
        for detail in details
    ), combined
    assert sum("INDEX IX_IV_OPTION_OBSERVATION_CONTRACT_TIME" in detail for detail in details) >= 2, combined
    assert any("SEARCH" in detail for detail in details), combined
    assert not any("TEMP B-TREE" in detail for detail in details), combined
