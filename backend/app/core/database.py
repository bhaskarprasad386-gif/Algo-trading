import threading
import time
from contextvars import copy_context

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import settings
from app.core.contention_telemetry import PoolCheckoutTelemetry


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

# Event-driven telemetry captures checkout ownership and hold time without retaining
# DBAPI connections, SQL text, credentials, or request query parameters.
_pool_checkout_telemetry = PoolCheckoutTelemetry(max_owners=24)


def _track_pool_checkout(dbapi_connection, connection_record, connection_proxy=None) -> None:
    _pool_checkout_telemetry.checkout(connection_record)


def _clear_pool_checkout(dbapi_connection, connection_record, *args) -> None:
    _pool_checkout_telemetry.checkin(connection_record)


def _invalidate_pool_checkout(dbapi_connection, connection_record, exception=None) -> None:
    _pool_checkout_telemetry.checkin(connection_record, event_name="invalidate")


def _detach_pool_checkout(dbapi_connection, connection_record) -> None:
    _pool_checkout_telemetry.checkin(connection_record, event_name="detach")


event.listen(engine, "checkout", _track_pool_checkout)
event.listen(engine, "checkin", _clear_pool_checkout)
event.listen(engine, "invalidate", _invalidate_pool_checkout)
event.listen(engine, "detach", _detach_pool_checkout)

# Pool.connect() is the public pool acquisition boundary. Timing this call
# captures queue wait plus pool checkout setup for ORM and Core consumers. Reinstall
# the wrapper after Engine.dispose(), which replaces the underlying pool instance.
_observed_pool_identity: int | None = None


def _install_pool_connect_observer() -> None:
    global _observed_pool_identity
    pool = engine.pool
    if _observed_pool_identity == id(pool):
        return
    original_connect = pool.connect

    def observed_connect(*args, **kwargs):
        started = time.monotonic()
        try:
            connection = original_connect(*args, **kwargs)
        except Exception as exc:
            _pool_checkout_telemetry.record_wait(
                time.monotonic() - started,
                timed_out=type(exc).__name__ == "TimeoutError",
            )
            raise
        _pool_checkout_telemetry.record_wait(time.monotonic() - started)
        return connection

    pool.connect = observed_connect
    _observed_pool_identity = id(pool)


_install_pool_connect_observer()


@event.listens_for(engine, "engine_disposed")
def _reinstall_pool_connect_observer(_engine) -> None:
    _install_pool_connect_observer()


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

    request_context = copy_context()
    worker = threading.Thread(
        target=request_context.run,
        args=(run_probe,),
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

    checkout_metrics = _pool_checkout_telemetry.snapshot()
    active_checkout_count = int(checkout_metrics["active_checkouts"])
    tracking_delta = (
        checked_out - active_checkout_count
        if isinstance(checked_out, int)
        else None
    )
    snapshot.update(checkout_metrics)
    snapshot.update({
        "active_checkout_count": active_checkout_count,
        "long_checkout_count": checkout_metrics["long_checkouts_30s"],
        "long_checkout_threshold_seconds": 30.0,
        # Preserve the established diagnostic keys for existing health consumers.
        "oldest_checkout_seconds": checkout_metrics["oldest_active_hold_seconds"],
        "oldest_checkout_thread": checkout_metrics["oldest_active_thread"],
        # Pool counters and event tracking are sampled independently. A
        # mismatch can be a normal checkout/checkin race, never a leak claim.
        "checkout_tracking_delta": tracking_delta,
        "untracked_checkout_count": None,
        "checkout_tracking_status": (
            "MATCH" if tracking_delta == 0
            else "COUNTER_MISMATCH" if tracking_delta is not None
            else "UNAVAILABLE"
        ),
    })
    return snapshot
