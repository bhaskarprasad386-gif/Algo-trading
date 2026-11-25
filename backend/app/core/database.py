from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import settings


_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_is_memory_sqlite = settings.DATABASE_URL in {"sqlite:///:memory:", "sqlite://"}

engine_kwargs = {
    "connect_args": {"check_same_thread": False} if _is_sqlite else {},
    "pool_pre_ping": True,
}
if not _is_sqlite:
    # Increase the persistent QueuePool by five connections (5 -> 10).
    # Keep max_overflow at SQLAlchemy's existing default of 10; this is a
    # bounded per-process ceiling of 20 connections, not an unbounded pool.
    engine_kwargs.update(pool_size=10, max_overflow=10)

if _is_memory_sqlite:
    # SQLite in-memory databases are connection-local. CI/TestClient can open
    # multiple connections, so a single shared connection is required for the
    # schema and transaction state to remain visible across requests.
    engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> bool:
    """Run a minimal DB round-trip for readiness/health checks."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True



def database_pool_snapshot() -> dict[str, object]:
    """Return bounded, credential-free SQLAlchemy pool occupancy diagnostics.

    QueuePool exposes checked-out/overflow counters; StaticPool and custom
    pools may not. Missing counters stay null rather than being guessed.
    """
    pool = engine.pool
    snapshot: dict[str, object] = {"pool_class": type(pool).__name__}
    for name in ("size", "checkedin", "checkedout", "overflow"):
        getter = getattr(pool, name, None)
        try:
            value = getter() if callable(getter) else None
            snapshot[name] = int(value) if value is not None else None
        except Exception:
            snapshot[name] = None
    return snapshot
