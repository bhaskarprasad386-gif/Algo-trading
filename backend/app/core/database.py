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
