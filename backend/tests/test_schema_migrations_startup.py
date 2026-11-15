"""Isolated tests for the legacy startup schema migration.

These tests never import app.main and never connect to the configured application
DATABASE_URL. PostgreSQL runs inside a per-test schema and is opt-in through
IV_TEST_POSTGRES_URL (CI provides a disposable PostgreSQL service).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
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
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE positions (id INTEGER PRIMARY KEY)"))


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



def test_created_legacy_tables_generate_primary_keys(migration_engine, monkeypatch):
    """Dialect-specific generated IDs must work on both supported test engines."""
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)

    schema_migrations.run_schema_migrations()

    with migration_engine.begin() as connection:
        generated_id = connection.execute(
            text(
                """
                INSERT INTO backtest_jobs (
                    job_id, symbol, contract_month, requested_days
                ) VALUES (
                    'migration-id-check', 'NIFTY', '2026-11', 1
                ) RETURNING id
                """
            )
        ).scalar_one()

    assert isinstance(generated_id, int)
    assert generated_id > 0



def test_box_snapshot_expiry_backfill_preserves_only_known_classifications(
    migration_engine, monkeypatch
):
    """Legacy JSON classifications are copied; unknown/malformed rows stay NULL."""
    monkeypatch.setattr(schema_migrations, "engine", migration_engine)
    _seed_minimal_legacy_schema(migration_engine)
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE live_box_spread_snapshots (
                    id INTEGER PRIMARY KEY,
                    payload_json TEXT,
                    observed_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO live_box_spread_snapshots (id, payload_json)
                VALUES
                    (1, '{"expiry_type":"weekly"}'),
                    (2, '{"expiry_type":"MONTHLY"}'),
                    (3, '{"expiry_type":"UNKNOWN"}'),
                    (4, 'not-json')
                """
            )
        )

    schema_migrations.run_schema_migrations()

    with migration_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, expiry_type FROM live_box_spread_snapshots ORDER BY id"
            )
        ).all()

    assert rows == [(1, "WEEKLY"), (2, "MONTHLY"), (3, None), (4, None)]
