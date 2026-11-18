"""Database-level mutation guards for audited IV constituent basket versions.

ORM mapper events are useful but can be bypassed by bulk/Core SQL. These
triggers enforce append-only history at the database boundary as well.
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


_TABLE = "iv_constituent_basket_versions"
_TRIGGER_UPDATE = "trg_iv_basket_versions_no_update"
_TRIGGER_DELETE = "trg_iv_basket_versions_no_delete"
_FUNCTION = "iv_basket_version_reject_mutation"


def install_iv_basket_immutability_guards(engine: Engine) -> None:
    """Install idempotent UPDATE/DELETE guards for SQLite or PostgreSQL."""
    if _TABLE not in set(inspect(engine).get_table_names()):
        raise RuntimeError(f"required table {_TABLE} is missing; refusing to start without append-only guards")

    dialect = engine.dialect.name
    with engine.begin() as connection:
        if dialect == "sqlite":
            connection.execute(text(f"""
                CREATE TRIGGER IF NOT EXISTS {_TRIGGER_UPDATE}
                BEFORE UPDATE ON {_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'IV constituent basket versions are append-only');
                END
            """))
            connection.execute(text(f"""
                CREATE TRIGGER IF NOT EXISTS {_TRIGGER_DELETE}
                BEFORE DELETE ON {_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'IV constituent basket versions are append-only');
                END
            """))
        elif dialect == "postgresql":
            connection.execute(text(f"""
                CREATE OR REPLACE FUNCTION {_FUNCTION}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'IV constituent basket versions are append-only';
                END;
                $$
            """))
            connection.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgname = '{_TRIGGER_UPDATE}'
                          AND tgrelid = '{_TABLE}'::regclass
                          AND NOT tgisinternal
                    ) THEN
                        CREATE TRIGGER {_TRIGGER_UPDATE}
                        BEFORE UPDATE ON {_TABLE}
                        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}();
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgname = '{_TRIGGER_DELETE}'
                          AND tgrelid = '{_TABLE}'::regclass
                          AND NOT tgisinternal
                    ) THEN
                        CREATE TRIGGER {_TRIGGER_DELETE}
                        BEFORE DELETE ON {_TABLE}
                        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}();
                    END IF;
                END
                $$;
            """))
        else:
            raise RuntimeError(
                f"database dialect {dialect!r} is unsupported for IV basket append-only guards"
            )
