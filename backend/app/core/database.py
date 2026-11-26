import threading
import time

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

# Track live pool checkouts without retaining DBAPI connections or SQL values.
_POOL_LONG_CHECKOUT_SECONDS = 30.0
_pool_checkout_lock = threading.Lock()
_pool_checkouts: dict[int, tuple[float, str]] = {}


def _track_pool_checkout(dbapi_connection, connection_record, connection_proxy=None) -> None:
    with _pool_checkout_lock:
        _pool_checkouts[id(connection_record)] = (
            time.monotonic(), threading.current_thread().name
        )


def _clear_pool_checkout(dbapi_connection, connection_record, *args) -> None:
    with _pool_checkout_lock:
        _pool_checkouts.pop(id(connection_record), None)


event.listen(engine, "checkout", _track_pool_checkout)
event.listen(engine, "checkin", _clear_pool_checkout)
event.listen(engine, "invalidate", _clear_pool_checkout)
# Detached DBAPI connections leave the pool without a normal checkin event.
event.listen(engine, "detach", _clear_pool_checkout)


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

class DatabaseHealthProbeBusy(RuntimeError):
    """Another bounded health probe is still waiting for the database pool."""


class DatabaseHealthProbeTimeout(TimeoutError):
    """The database health probe did not finish within its response budget."""


# At most one health-probe worker may wait on the application pool at a time.
# A timed-out daemon worker keeps this lock until its checkout eventually
# completes, preventing repeated health requests from accumulating threads.
_health_probe_lock = threading.Lock()


def check_database_bounded(probe=check_database, *, timeout_seconds: float = 0.75) -> bool:
    """Run SELECT 1 without allowing a health request to wait on pool checkout.

    The worker is intentionally single-flight. If SQLAlchemy's pool checkout
    blocks after the initial saturation snapshot, the HTTP caller returns
    degraded after the short budget. The one daemon worker may finish later,
    but no additional worker is created until it releases the lock.
    """
    if timeout_seconds <= 0:
        raise ValueError("health probe timeout must be positive")
    if not _health_probe_lock.acquire(blocking=False):
        raise DatabaseHealthProbeBusy("a previous database health probe is still in flight")

    completed = threading.Event()
    outcome: dict[str, Exception] = {}

    def run_probe() -> None:
        try:
            probe()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            _health_probe_lock.release()
            completed.set()

    worker = threading.Thread(
        target=run_probe,
        name="database-health-probe",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        _health_probe_lock.release()
        raise

    if not completed.wait(timeout_seconds):
        raise DatabaseHealthProbeTimeout(
            f"database health probe exceeded {timeout_seconds:.2f}s response budget"
        )
    error = outcome.get("error")
    if error is not None:
        raise error
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

    max_overflow = getattr(pool, "_max_overflow", None)
    try:
        max_overflow = int(max_overflow) if max_overflow is not None else None
    except (TypeError, ValueError):
        max_overflow = None
    checked_out = snapshot.get("checkedout")
    pool_size = snapshot.get("size")
    # QueuePool's documented pool_size=0 setting disables the size limit;
    # it does not mean the pool has only max_overflow connections. Likewise,
    # max_overflow=-1 permits unlimited overflow. In either configuration a
    # finite saturation threshold cannot be derived safely.
    capacity = (
        pool_size + max_overflow
        if (
            isinstance(pool_size, int)
            and pool_size > 0
            and max_overflow is not None
            and max_overflow >= 0
        )
        else None
    )
    snapshot["max_overflow"] = max_overflow
    snapshot["capacity"] = capacity
    snapshot["saturated"] = (
        checked_out >= capacity if capacity is not None and isinstance(checked_out, int) else None
    )

    now = time.monotonic()
    with _pool_checkout_lock:
        checkouts = tuple(_pool_checkouts.values())
    ages = [(max(0.0, now - started), thread_name) for started, thread_name in checkouts]
    oldest = max(ages, default=(0.0, None), key=lambda item: item[0])
    active_checkout_count = len(ages)
    tracking_delta = (
        checked_out - active_checkout_count
        if isinstance(checked_out, int)
        else None
    )
    snapshot.update({
        "active_checkout_count": active_checkout_count,
        "long_checkout_count": sum(age >= _POOL_LONG_CHECKOUT_SECONDS for age, _ in ages),
        "oldest_checkout_seconds": round(oldest[0], 3) if ages else None,
        "oldest_checkout_thread": oldest[1],
        "long_checkout_threshold_seconds": _POOL_LONG_CHECKOUT_SECONDS,
        # Pool counters and event tracking are sampled independently. A
        # mismatch can be a normal checkout/checkin race, so it is never
        # reported as an untracked connection or a leak count.
        "checkout_tracking_delta": tracking_delta,
        "untracked_checkout_count": None,
        "checkout_tracking_status": (
            "MATCH" if tracking_delta == 0
            else "COUNTER_MISMATCH" if tracking_delta is not None
            else "UNAVAILABLE"
        ),
    })
    return snapshot
